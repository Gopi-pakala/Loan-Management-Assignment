import frappe

from loan_assignment.loan_assignment.permissions.common import (
	employee_scope_condition,
	has_full_access,
	user_can_see_employee_row,
)

# Per the brief's access-control table: Treasury Officer "sees: approved
# loans awaiting release" — i.e. the release queue, not every disbursement
# in every state. `TREASURY_QUEUE_STATUS` is that queue. Treasury also
# keeps sight of what they personally released (`released_by = them`) —
# without that, releasing funds would make a disbursement vanish from
# their own view the moment they act on it, which isn't what "awaiting
# release" is describing; it's a separate, narrower allowance than "every
# Treasury Released/Disbursed record", which the row-visibility rule
# below no longer grants.
TREASURY_QUEUE_STATUS = "Finance Verified"


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	base = employee_scope_condition("Loan Disbursement", "employee", user)

	if not has_full_access(user) and "Treasury Officer" in frappe.get_roles(user):
		queue_status = frappe.db.escape(TREASURY_QUEUE_STATUS)
		treasury_condition = (
			f"(`tabLoan Disbursement`.status = {queue_status} "
			f"or `tabLoan Disbursement`.released_by = {frappe.db.escape(user)})"
		)
		return treasury_condition if not base else f"({base}) or ({treasury_condition})"

	return base


def has_permission(doc, user=None, permission_type=None):
	if not doc:
		return True
	user = user or frappe.session.user
	if has_full_access(user):
		return True
	if "Treasury Officer" in frappe.get_roles(user) and (
		doc.status == TREASURY_QUEUE_STATUS or doc.released_by == user
	):
		return True
	return user_can_see_employee_row(doc.employee, user)
