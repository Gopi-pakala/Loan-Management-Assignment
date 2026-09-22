import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from loan_assignment.loan_assignment import ledger
from loan_assignment.loan_assignment.repayment import allocate_repayment, reverse_repayment


class LoanRepayment(Document):
	def validate(self):
		loan = frappe.get_cached_doc("Loan", self.loan)
		if loan.docstatus != 1:
			frappe.throw(_("Loan must be submitted before repayments can be recorded against it."))
		outstanding = flt(loan.outstanding_principal) + flt(loan.outstanding_interest)
		if flt(self.amount) > outstanding + 0.5:
			frappe.throw(_("Amount {0} exceeds the outstanding balance {1} on this loan.").format(
				self.amount, outstanding
			))

	def on_submit(self):
		loan = frappe.get_doc("Loan", self.loan)
		result = allocate_repayment(loan, self.amount)

		self.db_set("allocated_interest", result["allocated_interest"])
		self.db_set("allocated_principal", result["allocated_principal"])
		self.db_set("allocated_arrears", result["allocated_arrears"])

		payment_entry = ledger.create_repayment_payment_entry(self, loan)
		self.db_set("payment_entry", payment_entry)

	def on_cancel(self):
		ledger.cancel_payment_entry(self.payment_entry)
		reverse_repayment(self.loan, self.allocated_interest, self.allocated_principal, self.allocated_arrears)
