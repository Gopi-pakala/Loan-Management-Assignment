import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

from loan_assignment.loan_assignment.ledger import _elevated
from loan_assignment.loan_assignment.schedule import recompute_loan_aggregates


class LoanInterestAccrual(Document):
	def validate(self):
		if getdate(self.period_end) < getdate(self.period_start):
			frappe.throw(_("Period End cannot be before Period Start."))

		duplicate = frappe.db.exists("Loan Interest Accrual", {
			"loan": self.loan, "period_start": self.period_start,
			"docstatus": 1, "name": ["!=", self.name],
		})
		if duplicate:
			frappe.throw(_("Interest for this period is already accrued in {0}.").format(duplicate))

		# Defaults to the scheduled interest; a manually entered amount is kept.
		if not flt(self.interest_amount):
			self.interest_amount = get_scheduled_interest(self.loan, self.period_start, self.period_end)
		if flt(self.interest_amount) < 0:
			frappe.throw(_("Interest Amount cannot be negative."))
		if not flt(self.interest_amount):
			frappe.throw(_(
				"No instalment of {0} falls due between {1} and {2}, so there is no interest to accrue. "
				"Pick a period that contains an instalment due date, or enter the Interest Amount manually."
			).format(self.loan, frappe.format(self.period_start, "Date"), frappe.format(self.period_end, "Date")))

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
				# No party here: only a Receivable/Payable-type account may carry a
				# party (frappe.throw in ERPNext's GL Entry.validate_party otherwise) —
				# interest_account is Income-type, so this leg stays party-less.
				{"account": loan.interest_account, "credit_in_account_currency": self.interest_amount},
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


@frappe.whitelist()
def get_scheduled_interest(loan, period_start, period_end):
	"""Interest of the current-schedule instalments falling due in the period."""
	if not (loan and period_start and period_end):
		return 0
	frappe.has_permission("Loan", doc=loan, throw=True)
	result = frappe.db.sql(
		"""select sum(interest_amount) from `tabLoan Repayment Schedule`
			where loan=%s and is_current=1 and due_date between %s and %s""",
		(loan, period_start, period_end),
	)
	return flt(result[0][0], 2) if result and result[0][0] else 0
