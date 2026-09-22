import frappe

from loan_assignment.loan_assignment.permissions.common import employee_scope_condition, user_can_see_employee_row


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	return employee_scope_condition("Loan", "employee", user)


def has_permission(doc, user=None, permission_type=None):
	if not doc:
		return True
	user = user or frappe.session.user
	return user_can_see_employee_row(doc.employee, user)
