import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from loan_assignment.loan_assignment import ledger
from loan_assignment.loan_assignment.repayment import allocate_repayment
from loan_assignment.loan_assignment.schedule import recompute_loan_aggregates


class LoanWriteOff(Document):
	def validate(self):
		loan = frappe.get_doc("Loan", self.loan)
		if loan.docstatus != 1:
			frappe.throw(_("Loan must be submitted before it can be written off."))

		outstanding = flt(loan.outstanding_principal) + flt(loan.outstanding_interest)
		if flt(self.recovered_from_settlement) > outstanding:
			frappe.throw(_("Recovered-from-settlement cannot exceed the outstanding balance {0}.").format(outstanding))

		residual = outstanding - flt(self.recovered_from_settlement)
		self.interest_written_off = min(flt(loan.outstanding_interest), residual)
		self.principal_written_off = residual - self.interest_written_off

	def before_submit(self):
		# The Workflow's "Approve" transition already sets `status` to
		# "Written Off" before calling submit() — self.status is the target
		# value at this point, not the state being left, so the state
		# actually being left has to be read straight from the DB.
		previous_status = frappe.db.get_value("Loan Write Off", self.name, "status")
		if previous_status != "Pending CFO":
			frappe.throw(_("Write-off must complete Finance endorsement before CFO approval."))
		self.status = "Written Off"

	def on_submit(self):
		loan = frappe.get_doc("Loan", self.loan)

		if flt(self.recovered_from_settlement) > 0:
			allocate_repayment(loan, self.recovered_from_settlement)
			loan.reload()

		if flt(self.principal_written_off) + flt(self.interest_written_off) > 0:
			je = ledger.create_writeoff_journal_entry(self, loan)
			self.db_set("journal_entry", je)

		frappe.db.set_value("Loan Repayment Schedule", {
			"loan": loan.name, "is_current": 1, "status": ["in", ["Pending", "Partially Recovered"]],
		}, "status", "Skipped")

		loan.db_set({"status": "Written Off", "closure_date": self.write_off_date})
		recompute_loan_aggregates(loan)

	def before_cancel(self):
		frappe.throw(_(
			"A Loan Write Off cannot be cancelled once submitted — it is a closing statutory event. "
			"Reverse its effect with a fresh accounting adjustment if this was made in error."
		))
