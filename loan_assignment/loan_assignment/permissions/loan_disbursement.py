import frappe

from loan_assignment.loan_assignment.permissions.common import (
	employee_scope_condition,
	has_full_access,
	user_can_see_employee_row,
)

TREASURY_VISIBLE_STATUSES = ("Finance Verified", "Treasury Released", "Disbursed")


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	base = employee_scope_condition("Loan Disbursement", "employee", user)

	if not has_full_access(user) and "Treasury Officer" in frappe.get_roles(user):
		statuses = ", ".join(frappe.db.escape(s) for s in TREASURY_VISIBLE_STATUSES)
		treasury_condition = f"`tabLoan Disbursement`.status in ({statuses})"
		return treasury_condition if not base else f"({base}) or ({treasury_condition})"

	return base


def has_permission(doc, user=None, permission_type=None):
	if not doc:
		return True
	user = user or frappe.session.user
	if has_full_access(user):
		return True
	if "Treasury Officer" in frappe.get_roles(user) and doc.status in TREASURY_VISIBLE_STATUSES:
		return True
	return user_can_see_employee_row(doc.employee, user)
