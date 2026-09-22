import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from loan_assignment.loan_assignment import approval, eligibility
from loan_assignment.loan_assignment.doctype.loan_approval_rule.loan_approval_rule import get_matching_rule
from loan_assignment.loan_assignment.utils import get_monthly_gross


class LoanApplication(Document):
	def validate(self):
		employee_doc = frappe.get_cached_doc("Employee", self.employee)

		try:
			eligibility.check_hard_blocks(employee_doc)
		except eligibility.HardBlock as e:
			frappe.throw(str(e))

		if self.docstatus == 0:
			self.monthly_gross = get_monthly_gross(self.employee)
			self.eligibility_status, self.eligibility_notes = eligibility.run_soft_checks(self)

		if self.eligibility_status == "Ineligible" and not self.eligibility_override:
			frappe.throw(_("Application is not eligible: {0}").format(self.eligibility_notes))

		if self.eligibility_override and "HR Manager" not in frappe.get_roles():
			frappe.throw(_("Only an HR Manager may override eligibility."))

	def before_update_after_submit(self):
		"""Frappe only calls `validate()` for the "save" and "submit"
		actions — a save that touches nothing but allow_on_submit fields on
		an already-submitted doc (exactly what changing `status` after
		submission is) is classified as "update_after_submit" and runs
		*this* hook instead (see Document.run_before_save_methods). This is
		the one and only place approval eligibility is enforced; the
		Workflow transitions themselves are deliberately wide open (allowed
		to any Employee) since Workflow can't express "whichever role
		governs this specific record's current step" — see DECISIONS.md.
		"""
		previous_status = frappe.db.get_value("Loan Application", self.name, "status")
		if previous_status == "Returned" and self.status == "Returned":
			# Applicant edited and resaved after a Return: re-resolve the
			# chain fresh and route straight back to Pending Approval. This
			# cannot go via an intermediate "Draft" status — the Workflow's
			# own docstatus bookkeeping treats "Draft" as doc_status 0 and
			# refuses to define a transition from a submitted state back to
			# it (Workflow.validate_docstatus), and this document's docstatus
			# never actually left 1 in the first place.
			self._resolve_and_route_to_approval()
		else:
			self._handle_workflow_transition(previous_status)

	def before_submit(self):
		self._resolve_and_route_to_approval()

	def _resolve_and_route_to_approval(self):
		rule_name = get_matching_rule(
			self.company, self.loan_type, self.employee_grade, self.requested_amount
		)
		rule = frappe.get_cached_doc("Loan Approval Rule", rule_name)
		if rule.require_board_resolution and not self.board_resolution_ref:
			frappe.throw(_("This amount band requires a board resolution reference."))

		self.approval_rule = rule_name
		self.current_step = 1
		self.status = "Pending Approval"

	def _handle_workflow_transition(self, previous_status):
		"""Fires whenever `status` is actually changing from Pending Approval
		to Approved/Rejected/Returned in this save — regardless of whether
		that change came from the Workflow's Approve/Reject/Return buttons or
		a script setting the field directly (see apply_action() below). This
		is the one and only place approval eligibility is enforced; the
		Workflow transitions themselves are deliberately wide open (allowed
		to any Employee) since Workflow can't express "whichever role
		governs this specific record's current step" — see DECISIONS.md.
		"""
		if self.status not in ("Approved", "Rejected", "Returned"):
			return
		if previous_status != "Pending Approval" or self.status == previous_status:
			return

		requested_status = self.status
		comment = self.action_comment
		if requested_status in ("Rejected", "Returned") and not comment:
			frappe.throw(_("A comment is required to reject or return an application."))

		employee_doc = frappe.get_cached_doc("Employee", self.employee)
		step_row = approval.get_step_row(self.approval_rule, self.current_step)
		if not step_row:
			frappe.throw(_("No approval step configured for sequence {0}").format(self.current_step))

		acting_user = frappe.session.user
		approval.guard_self_and_repeat(self, employee_doc, acting_user)
		if not approval.user_is_eligible_for_step(acting_user, step_row, employee_doc):
			frappe.throw(_("You are not eligible to act on this step."))

		approver_value, acted_for_value = approval.resolve_acting_user(step_row, employee_doc, acting_user)
		total_steps = len(frappe.get_cached_doc("Loan Approval Rule", self.approval_rule).approvers)
		is_final_approval = requested_status == "Approved" and self.current_step >= total_steps
		to_status = requested_status if (requested_status != "Approved" or is_final_approval) else "Pending Approval"

		self.append("approval_log", {
			"step": self.current_step,
			"approver": approver_value,
			"acted_for": acted_for_value,
			"action": requested_status,
			"from_status": "Pending Approval",
			"to_status": to_status,
			"comment": comment,
			"acted_on": now_datetime(),
		})

		if requested_status == "Approved" and not is_final_approval:
			self.current_step += 1
			self.status = "Pending Approval"
		else:
			self.status = to_status

		self.action_comment = None

		if self.status == "Approved":
			self.create_loan()

	def apply_action(self, action, comment=None):
		"""Scripting/testing convenience — sets the field and saves exactly
		like clicking the Workflow button would; _handle_workflow_transition
		does the real work either way.
		"""
		if action not in ("Approved", "Rejected", "Returned"):
			frappe.throw(_("Invalid action"))
		self.action_comment = comment
		self.status = action
		self.save(ignore_permissions=True)
		return self.status

	def create_loan(self):
		if self.loan:
			return self.loan
		if not self.sanctioned_amount:
			self.sanctioned_amount = self.requested_amount

		loan_type = frappe.get_cached_doc("Loan Type", self.loan_type)
		loan = frappe.new_doc("Loan")
		loan.update({
			"loan_application": self.name,
			"employee": self.employee,
			"company": self.company,
			"loan_type": self.loan_type,
			"interest_mode": loan_type.interest_mode,
			"rate_of_interest": loan_type.rate_of_interest,
			"tenure_months": self.tenure_months,
			"sanctioned_amount": self.sanctioned_amount,
			"first_deduction_month": self.first_deduction_month,
			"loan_account": loan_type.loan_account,
			"interest_account": loan_type.interest_account,
			"writeoff_account": loan_type.writeoff_account,
		})
		loan.insert(ignore_permissions=True)
		loan.submit()

		self.loan = loan.name
		return loan.name
