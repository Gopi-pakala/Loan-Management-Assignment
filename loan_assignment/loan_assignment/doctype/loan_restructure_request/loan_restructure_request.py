import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, flt, getdate

from loan_assignment.loan_assignment.schedule import rebuild_from_period


class LoanRestructureRequest(Document):
	def validate(self):
		loan = frappe.get_doc("Loan", self.loan)
		if loan.docstatus != 1 or loan.status in ("Closed", "Written Off"):
			frappe.throw(_("Loan must be active to be restructured."))
		if self.request_type == "Moratorium" and not self.moratorium_months:
			frappe.throw(_("Moratorium Months is required for a Moratorium request."))
		if self.request_type == "Re-tenure" and not self.new_tenure_months:
			frappe.throw(_("New Tenure is required for a Re-tenure request."))

	def before_submit(self):
		# The Workflow's "Apply" transition already sets `status` to
		# "Applied" before calling submit() — self.status is the target
		# value at this point, not the state being left, so the state
		# actually being left has to be read straight from the DB.
		previous_status = frappe.db.get_value("Loan Restructure Request", self.name, "status")
		if previous_status != "Pending Finance":
			frappe.throw(_("Restructure must complete HR endorsement before Finance approval."))
		self.status = "Applied"

	def on_submit(self):
		loan = frappe.get_doc("Loan", self.loan)
		effective_row = frappe.db.get_value(
			"Loan Repayment Schedule",
			{"loan": loan.name, "is_current": 1, "due_date": [">=", self.effective_from],
				"status": ["in", ["Pending", "Partially Recovered"]]},
			["period_no"], order_by="period_no asc",
		)
		if not effective_row:
			frappe.throw(_("No un-recovered period found on or after {0}.").format(self.effective_from))
		from_period = effective_row

		periods_recovered = frappe.db.count(
			"Loan Repayment Schedule", {"loan": loan.name, "is_current": 1, "status": "Recovered"}
		)
		true_outstanding = flt(loan.disbursed_amount) - flt(loan.principal_recovered)

		if self.request_type == "Moratorium":
			tenure_remaining = (loan.tenure_months - periods_recovered) + self.moratorium_months
			due_date = add_months(getdate(self.effective_from), self.moratorium_months)
		else:
			tenure_remaining = self.new_tenure_months
			due_date = getdate(self.effective_from)

		rebuild_from_period(
			loan, from_period, true_outstanding, tenure_remaining,
			note=f"Restructured by {self.name} ({self.request_type})",
			due_date_override=due_date,
		)
