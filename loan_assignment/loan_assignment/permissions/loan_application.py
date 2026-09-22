import frappe

from loan_assignment.loan_assignment.permissions.common import employee_scope_condition, user_can_see_employee_row


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	return employee_scope_condition("Loan Application", "employee", user)


def has_permission(doc, user=None, permission_type=None):
	if not doc:
		# Doctype-level "can this user read this doctype at all" check (no
		# specific document) — say yes here and let
		# get_permission_query_conditions do the real row-level scoping,
		# same as every other has_permission hook in this app.
		return True
	user = user or frappe.session.user
	return user_can_see_employee_row(doc.employee, user)
