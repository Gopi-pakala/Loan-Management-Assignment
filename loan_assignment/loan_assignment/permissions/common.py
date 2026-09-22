import frappe

FULL_ACCESS_ROLES = {"System Manager", "Finance Manager", "CFO"}


def has_full_access(user):
	return bool(FULL_ACCESS_ROLES & set(frappe.get_roles(user)))


def get_employee_for_user(user):
	return frappe.db.get_value("Employee", {"user_id": user}, "name")


def get_subordinate_employees(manager_employee):
	"""Direct + indirect reports, via iterative BFS on Employee.reports_to.
	Small org depth in practice; a handful of queries, each indexed.
	"""
	seen = set()
	frontier = [manager_employee]
	while frontier:
		children = frappe.get_all("Employee", filters={"reports_to": ["in", frontier]}, pluck="name")
		fresh = [c for c in children if c not in seen]
		seen.update(fresh)
		frontier = fresh
	return list(seen)


def get_department_subtree(department):
	lft, rgt = frappe.db.get_value("Department", department, ["lft", "rgt"])
	return frappe.get_all(
		"Department", filters={"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name"
	)


def employee_scope_condition(doctype, employee_fieldname, user):
	"""Row-level condition for any doctype that carries an `employee`-like
	link field: Employee sees only their own; Reporting Manager sees their
	reporting tree; HR Manager sees their department subtree; everyone in
	FULL_ACCESS_ROLES sees everything. Returns "" for no restriction,
	never a falsy value for a genuine deny (always "1=0" there) — an empty
	or None condition is treated by the framework as "allow everything",
	not "restrict to nothing".
	"""
	if has_full_access(user):
		return ""

	roles = set(frappe.get_roles(user))
	own_employee = get_employee_for_user(user)

	if "HR Manager" in roles and own_employee:
		department = frappe.db.get_value("Employee", own_employee, "department")
		if department:
			departments = get_department_subtree(department)
			if departments:
				dept_list = ", ".join(frappe.db.escape(d) for d in departments)
				return (
					f"`tab{doctype}`.{employee_fieldname} in "
					f"(select name from `tabEmployee` where department in ({dept_list}))"
				)

	if "Reporting Manager" in roles and own_employee:
		subordinates = get_subordinate_employees(own_employee)
		scope = subordinates + [own_employee]
		emp_list = ", ".join(frappe.db.escape(e) for e in scope)
		return f"`tab{doctype}`.{employee_fieldname} in ({emp_list})"

	if own_employee:
		return f"`tab{doctype}`.{employee_fieldname} = {frappe.db.escape(own_employee)}"

	return "1=0"


def user_can_see_employee_row(employee, user):
	if has_full_access(user):
		return True

	roles = set(frappe.get_roles(user))
	own_employee = get_employee_for_user(user)
	if not own_employee:
		return False
	if employee == own_employee:
		return True

	if "HR Manager" in roles:
		department = frappe.db.get_value("Employee", own_employee, "department")
		target_department = frappe.db.get_value("Employee", employee, "department")
		if department and target_department:
			if target_department in get_department_subtree(department):
				return True

	if "Reporting Manager" in roles:
		if employee in get_subordinate_employees(own_employee):
			return True

	return False
