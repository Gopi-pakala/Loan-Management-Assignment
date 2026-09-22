import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from loan_assignment.loan_assignment import ledger
from loan_assignment.loan_assignment.repayment import (
	allocate_repayment,
	close_out_foreclosure,
	compute_foreclosure_payoff,
	reverse_foreclosure_skip,
	reverse_repayment,
)


class LoanRepayment(Document):
	def validate(self):
		loan = frappe.get_cached_doc("Loan", self.loan)
		if loan.docstatus != 1:
			frappe.throw(_("Loan must be submitted before repayments can be recorded against it."))

		if self.repayment_type == "Foreclosure":
			payoff = compute_foreclosure_payoff(loan, self.repayment_date)
			if flt(self.amount) < payoff.total_payoff - 0.5:
				frappe.throw(_(
					"Foreclosure amount {0} is short of the payoff figure as of {1}: {2} "
					"(outstanding principal {3} + interest accrued to date {4} + pre-closure charge {5})."
				).format(self.amount, self.repayment_date, payoff.total_payoff,
					payoff.outstanding_principal, payoff.interest_accrued_to_date, payoff.pre_closure_charge))
			return

		outstanding = flt(loan.outstanding_principal) + flt(loan.outstanding_interest)
		if flt(self.amount) > outstanding + 0.5:
			frappe.throw(_("Amount {0} exceeds the outstanding balance {1} on this loan.").format(
				self.amount, outstanding
			))

	def on_submit(self):
		loan = frappe.get_doc("Loan", self.loan)

		if self.repayment_type == "Foreclosure":
			payoff = compute_foreclosure_payoff(loan, self.repayment_date)
			self.db_set("allocated_principal", payoff.outstanding_principal)
			self.db_set("allocated_interest", payoff.interest_accrued_to_date)
			self.db_set("allocated_charge", payoff.pre_closure_charge)
			self.db_set("rebated_interest", payoff.rebate_on_unaccrued_interest)
			self.db_set("allocated_arrears", 0)

			payment_entry = ledger.create_repayment_payment_entry(
				self, loan, amount=payoff.outstanding_principal + payoff.interest_accrued_to_date
			)
			self.db_set("payment_entry", payment_entry)
			if payoff.pre_closure_charge:
				charge_je = ledger.create_foreclosure_charge_journal_entry(self, loan)
				self.db_set("charge_journal_entry", charge_je)

			close_out_foreclosure(loan, self.repayment_date, payoff)
			return

		result = allocate_repayment(loan, self.amount)

		self.db_set("allocated_interest", result["allocated_interest"])
		self.db_set("allocated_principal", result["allocated_principal"])
		self.db_set("allocated_arrears", result["allocated_arrears"])

		payment_entry = ledger.create_repayment_payment_entry(self, loan)
		self.db_set("payment_entry", payment_entry)

	def on_cancel(self):
		ledger.cancel_payment_entry(self.payment_entry)

		if self.repayment_type == "Foreclosure":
			if self.get("charge_journal_entry"):
				ledger.cancel_journal_entry(self.charge_journal_entry)
			reverse_foreclosure_skip(self.loan)

		reverse_repayment(self.loan, self.allocated_interest, self.allocated_principal, self.allocated_arrears)
