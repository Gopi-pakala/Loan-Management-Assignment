import frappe
from frappe.model.document import Document

from loan_assignment.loan_assignment.ledger import _elevated
from loan_assignment.loan_assignment.schedule import recompute_loan_aggregates


class LoanInterestAccrual(Document):
	def on_submit(self):
		loan = frappe.get_doc("Loan", self.loan)
		je = frappe.new_doc("Journal Entry")
		je.update({
			"voucher_type": "Journal Entry",
			"company": loan.company,
			"posting_date": self.period_end,
			"user_remark": f"Monthly interest accrual for {loan.name}, {self.period_start} to {self.period_end}",
			"accounts": [
				{"account": loan.loan_account, "debit_in_account_currency": self.interest_amount,
					"party_type": "Employee", "party": loan.employee},
				{"account": loan.interest_account, "credit_in_account_currency": self.interest_amount,
					"party_type": "Employee", "party": loan.employee},
			],
		})
		je.flags.ignore_permissions = True
		with _elevated():
			je.insert()
			je.submit()
		self.db_set("journal_entry", je.name)
		recompute_loan_aggregates(loan)

	def on_cancel(self):
		if self.journal_entry:
			je = frappe.get_doc("Journal Entry", self.journal_entry)
			if je.docstatus == 1:
				je.flags.ignore_permissions = True
				with _elevated():
					je.cancel()
		loan = frappe.get_doc("Loan", self.loan)
		recompute_loan_aggregates(loan)
