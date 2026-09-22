from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip

from loan_assignment.loan_assignment.payroll import (
	LOAN_RECOVERY_COMPONENT,
	ensure_loan_recovery_deduction_row,
	recover_against_available_funds,
)


class LoanAssignmentSalarySlip(SalarySlip):
	"""Extends HRMS's Salary Slip (per hooks.py override_doctype_class) to
	deduct live loan instalments. HRMS core already calls set_loan_repayment()
	inside calculate_net_pay(), but that is a no-op unless the separate
	`lending` app is installed (see salary_slip_loan_utils.py) — which the
	brief explicitly puts out of scope — so it never runs here and there is
	no interference with this module's own "Loan" doctype.
	"""

	def validate(self):
		super().validate()
		# validate() runs again at submit time on the same in-memory doc
		# that was already inserted as a draft. By then the schedule row
		# this slip recovered against is already "Recovered", so a second
		# recompute would find nothing due, strip the already-correct
		# loan_recoveries/deduction rows, and never put them back. Recompute
		# only on the very first save and on a genuine resubmission
		# (amend) — never on a plain draft -> submit transition.
		if self.is_new() or self.amended_from:
			self.apply_loan_recoveries()

	def apply_loan_recoveries(self):
		self._strip_loan_rows()
		self.set_net_pay()

		available = max(self.net_pay, 0)
		detail_rows = recover_against_available_funds(self, available)

		if detail_rows:
			total = sum(row["total_recovered"] for row in detail_rows)
			ensure_loan_recovery_deduction_row(self, total)
			for row in detail_rows:
				self.append("loan_recoveries", row)
			self.set_net_pay()

	def _strip_loan_rows(self):
		self.deductions = [d for d in self.deductions if d.salary_component != LOAN_RECOVERY_COMPONENT]
		self.set("loan_recoveries", [])
