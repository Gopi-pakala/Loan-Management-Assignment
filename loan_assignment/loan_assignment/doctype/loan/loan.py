import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

from loan_assignment.loan_assignment.schedule import generate_initial_schedule


class Loan(Document):
	# applicant_type / repay_from_salary / is_term_loan (hidden, always
	# blank/0) exist only so hrms.hr.utils.validate_loan_repay_from_salary —
	# a doc_events hook HRMS registers globally on the doctype *name* "Loan"
	# for the separate lending app, which fires unconditionally even with
	# lending not installed — finds falsy values and no-ops, instead of an
	# AttributeError. See DECISIONS.md.
	def validate(self):
		if not self.disbursed_amount:
			self.disbursed_amount = 0
			self.principal_recovered = 0
			self.interest_recovered = 0
			self.outstanding_principal = 0
			self.outstanding_interest = 0
			self.arrears_amount = 0

	def on_submit(self):
		self.status = "Sanctioned"
		generate_initial_schedule(self)

	def on_cancel(self):
		if frappe.db.exists("Loan Disbursement", {"loan": self.name, "docstatus": 1}):
			frappe.throw(_("Cannot cancel a Loan that has submitted disbursements against it."))
		frappe.db.delete("Loan Repayment Schedule", {"loan": self.name})

	def update_status(self):
		if self.status == "Written Off":
			return

		if flt(self.outstanding_principal) <= 0 and flt(self.outstanding_interest) <= 0 and flt(self.disbursed_amount) > 0:
			self.status = "Closed"
			self.closure_date = self.closure_date or nowdate()
		elif flt(self.principal_recovered) > 0 or flt(self.interest_recovered) > 0:
			self.status = "Repaying"
		elif flt(self.disbursed_amount) >= flt(self.sanctioned_amount) and flt(self.disbursed_amount) > 0:
			self.status = "Disbursed"
		elif flt(self.disbursed_amount) > 0:
			self.status = "Partially Disbursed"
		else:
			self.status = "Sanctioned"

		self.db_update()
