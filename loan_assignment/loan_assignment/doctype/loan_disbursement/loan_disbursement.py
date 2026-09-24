import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from loan_assignment.loan_assignment import ledger
from loan_assignment.loan_assignment.schedule import get_next_pending_period, rebuild_from_period


class LoanDisbursement(Document):
	def validate(self):
		loan = frappe.get_doc("Loan", self.loan)
		if loan.docstatus != 1:
			frappe.throw(_("Loan must be submitted (sanctioned) before it can be disbursed."))

		result = frappe.db.sql(
			"select sum(amount) from `tabLoan Disbursement` where loan=%s and docstatus=1 and name!=%s",
			(self.loan, self.name),
		)
		already_disbursed = flt(result[0][0]) if result and result[0][0] else 0
		if already_disbursed + flt(self.amount) > flt(loan.sanctioned_amount):
			frappe.throw(_(
				"Total disbursed ({0}) would exceed the sanctioned amount ({1})."
			).format(already_disbursed + flt(self.amount), loan.sanctioned_amount))

		if self.status == "Finance Verified" and not self.verified_by:
			self.verified_by = frappe.session.user
			self.verified_on = now_datetime()

		if self.status == "Treasury Released":
			if self.verified_by == frappe.session.user and frappe.session.user != "Administrator":
				frappe.throw(_("The Treasury Officer releasing funds cannot be the same user who verified this disbursement."))
			if not self.released_by:
				self.released_by = frappe.session.user
				self.released_on = now_datetime()

	def before_submit(self):
		# The Workflow's "Confirm Disbursement" transition already sets
		# `status` to "Disbursed" before calling submit() — self.status is
		# the target value at this point, not the state being left, so the
		# state actually being left has to be read straight from the DB.
		previous_status = frappe.db.get_value("Loan Disbursement", self.name, "status")
		if previous_status != "Treasury Released":
			frappe.throw(_("Disbursement must complete Finance verification and Treasury release before it can be submitted."))
		if (self.verified_by and self.released_by and self.verified_by == self.released_by
				and frappe.session.user != "Administrator"):
			frappe.throw(_("Verifier and releaser must be different users."))

		self.tranche_no = frappe.db.count("Loan Disbursement", {"loan": self.loan, "docstatus": 1}) + 1
		self.status = "Disbursed"

	def on_submit(self):
		loan = frappe.get_doc("Loan", self.loan)
		payment_entry = ledger.create_disbursement_payment_entry(self, loan)
		self.db_set("payment_entry", payment_entry)

		loan.db_set("disbursed_amount", flt(loan.disbursed_amount) + flt(self.amount))
		loan.reload()
		next_period = get_next_pending_period(loan.name)
		from_period = next_period.period_no if next_period else 1
		periods_done = frappe.db.count("Loan Repayment Schedule", {
			"loan": loan.name, "is_current": 1, "status": "Recovered",
		})
		rebuild_from_period(
			loan, from_period,
			principal=flt(loan.disbursed_amount) - flt(loan.principal_recovered),
			tenure_remaining=loan.tenure_months - periods_done,
			note=f"Rebuilt after disbursement {self.name} (tranche {self.tranche_no})",
		)
		loan.reload()
		loan.update_status()

	def on_cancel(self):
		loan = frappe.get_doc("Loan", self.loan)
		ledger.cancel_payment_entry(self.payment_entry)

		loan.db_set("disbursed_amount", flt(loan.disbursed_amount) - flt(self.amount))
		loan.reload()
		next_period = get_next_pending_period(loan.name)
		from_period = next_period.period_no if next_period else 1
		periods_done = frappe.db.count("Loan Repayment Schedule", {
			"loan": loan.name, "is_current": 1, "status": "Recovered",
		})
		rebuild_from_period(
			loan, from_period,
			principal=flt(loan.disbursed_amount) - flt(loan.principal_recovered),
			tenure_remaining=loan.tenure_months - periods_done,
			note=f"Rebuilt after cancelling disbursement {self.name}",
		)
		loan.reload()
		loan.update_status()
		self.db_set("status", "Cancelled")
