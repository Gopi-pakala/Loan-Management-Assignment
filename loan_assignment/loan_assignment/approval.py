import frappe
from frappe import _


def get_step_row(approval_rule, sequence):
	rule = frappe.get_cached_doc("Loan Approval Rule", approval_rule)
	for row in rule.approvers:
		if row.sequence == sequence:
			return row
	return None


def get_reports_to_user(employee):
	manager = frappe.db.get_value("Employee", employee, "reports_to")
	if not manager:
		return None
	return frappe.db.get_value("Employee", manager, "user_id")


def is_department_manager(user, department, company):
	"""True if `user`'s own Employee record is attached to `department` or to
	an ancestor of it in the Department tree (same company) — i.e. they are
	responsible for that part of the org, not just a holder of the role
	somewhere else in the company.
	"""
	if not department:
		return False
	manager_department = frappe.db.get_value(
		"Employee", {"user_id": user, "company": company}, "department"
	)
	if not manager_department:
		return False
	if manager_department == department:
		return True

	lft, rgt = frappe.db.get_value("Department", manager_department, ["lft", "rgt"])
	target_lft = frappe.db.get_value("Department", department, "lft")
	return lft <= target_lft <= rgt


def user_is_eligible_for_step(user, step_row, employee_doc):
	"""Is `user` allowed to act on this step at all, ignoring the
	self-approval / repeat-actor rules (checked separately since those apply
	uniformly regardless of how the step names its approver)?
	"""
	if user == "Administrator":
		return True

	if frappe.db.exists("Has Role", {"parent": user, "role": "System Manager"}):
		# Operational safety valve: a stuck chain (e.g. the only eligible
		# approver is the applicant) still needs a way forward. Documented
		# in DECISIONS.md.
		return True

	if step_row.approver_user:
		if user == step_row.approver_user:
			return True
		if step_row.delegate_user and user == step_row.delegate_user and _is_on_approved_leave(
			step_row.approver_user, employee_doc.company
		):
			return True
		return False

	role = step_row.approver_role
	if not frappe.db.exists("Has Role", {"parent": user, "role": role}):
		return False

	if role == "Reporting Manager":
		return user == get_reports_to_user(employee_doc.name)

	if role == "HR Manager":
		return is_department_manager(user, employee_doc.department, employee_doc.company)

	# Finance Manager / CFO / Treasury Officer: any enabled holder, company-wide.
	return True


def _is_on_approved_leave(user, company):
	employee = frappe.db.get_value("Employee", {"user_id": user, "company": company}, "name")
	if not employee:
		return False
	return bool(frappe.db.exists(
		"Leave Application",
		{
			"employee": employee,
			"status": "Approved",
			"docstatus": 1,
			"from_date": ["<=", frappe.utils.nowdate()],
			"to_date": [">=", frappe.utils.nowdate()],
		},
	))


def guard_self_and_repeat(application_doc, employee_doc, acting_user):
	if acting_user == "Administrator":
		# Administrator overrides self-approval and repeat-actor rules.
		return

	if acting_user == application_doc.owner or acting_user == employee_doc.user_id:
		frappe.throw(_("You cannot approve your own application."))

	already_acted = {row.approver for row in application_doc.approval_log}
	if acting_user in already_acted:
		frappe.throw(_("You have already acted on an earlier step of this approval chain."))


def resolve_acting_user(step_row, employee_doc, acting_user):
	"""Returns (approver_field_value, acted_for_value) for the log row —
	`acted_for` is set only when a delegate is acting in the named
	approver's place.
	"""
	if (
		step_row.approver_user
		and acting_user == step_row.delegate_user
		and acting_user != step_row.approver_user
	):
		return acting_user, step_row.approver_user
	return acting_user, None
