import frappe
from frappe.model.document import Document


class LoanType(Document):
	def validate(self):
		if self.interest_mode not in ("Flat", "Reducing Balance"):
			frappe.throw(frappe._("Interest Mode must be Flat or Reducing Balance"))
		if self.max_tenure_months and self.max_tenure_months <= 0:
			frappe.throw(frappe._("Max Tenure must be a positive number of months"))
