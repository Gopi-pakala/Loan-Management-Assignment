import frappe
from frappe.model.document import Document


class LoanApprovalStep(Document):
	def validate(self):
		if bool(self.approver_role) == bool(self.approver_user):
			frappe.throw(
				frappe._("Row #{0}: set exactly one of Approver Role or Approver User").format(self.idx)
			)
