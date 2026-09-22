import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, today

from loan_assignment.loan_assignment.permissions.common import employee_scope_condition


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	return columns, data, None, chart


def get_columns():
	return [
		{"label": _("Month"), "fieldname": "month", "fieldtype": "Data", "width": 100},
		{"label": _("Scheduled"), "fieldname": "scheduled", "fieldtype": "Currency", "width": 120},
		{"label": _("Actually Recovered"), "fieldname": "recovered", "fieldtype": "Currency", "width": 130},
		{"label": _("Period Shortfall"), "fieldname": "shortfall", "fieldtype": "Currency", "width": 120},
		{"label": _("Running Shortfall"), "fieldname": "running_shortfall", "fieldtype": "Currency", "width": 130},
	]


def get_data(filters):
	conditions = ["s.is_current = 1"]
	values = {}

	from_date = filters.from_date or add_months(today(), -18)
	to_date = filters.to_date or today()
	conditions.append("s.due_date between %(from_date)s and %(to_date)s")
	values["from_date"] = from_date
	values["to_date"] = to_date

	if filters.company:
		conditions.append("l.company = %(company)s")
		values["company"] = filters.company
	if filters.department:
		conditions.append("e.department = %(department)s")
		values["department"] = filters.department
	if filters.loan_type:
		conditions.append("l.loan_type = %(loan_type)s")
		values["loan_type"] = filters.loan_type

	scope = employee_scope_condition("Loan", "employee", frappe.session.user)
	if scope == "1=0":
		return []
	if scope:
		conditions.append(f"({scope})")

	rows = frappe.db.sql(f"""
		select
			date_format(s.due_date, '%%Y-%%m') as month,
			sum(s.instalment_amount) as scheduled,
			sum(s.recovered_amount) as recovered
		from `tabLoan Repayment Schedule` s
		join `tabLoan` l on l.name = s.loan
		join `tabEmployee` e on e.name = l.employee
		where {" and ".join(conditions)}
		group by date_format(s.due_date, '%%Y-%%m')
		order by month asc
	""", values, as_dict=True)

	running = 0
	for row in rows:
		shortfall = flt(row.scheduled) - flt(row.recovered)
		running += shortfall
		row["shortfall"] = shortfall
		row["running_shortfall"] = running
	return rows


def get_chart(data):
	if not data:
		return None
	return {
		"data": {
			"labels": [r.month for r in data],
			"datasets": [
				{"name": _("Scheduled"), "values": [r.scheduled for r in data]},
				{"name": _("Recovered"), "values": [r.recovered for r in data]},
			],
		},
		"type": "line",
	}
