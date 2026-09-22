import frappe
from frappe import _
from frappe.utils import flt, nowdate

from loan_assignment.loan_assignment.permissions.common import employee_scope_condition


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	summary = get_summary(filters, data)
	return columns, data, None, None, summary


def get_columns():
	return [
		{"label": _("Loan"), "fieldname": "loan", "fieldtype": "Link", "options": "Loan", "width": 110},
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 110},
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 150},
		{"label": _("Department"), "fieldname": "department", "fieldtype": "Link", "options": "Department", "width": 120},
		{"label": _("Loan Type"), "fieldname": "loan_type", "fieldtype": "Link", "options": "Loan Type", "width": 100},
		{"label": _("Sanctioned"), "fieldname": "sanctioned_amount", "fieldtype": "Currency", "width": 110},
		{"label": _("Disbursed"), "fieldname": "disbursed_amount", "fieldtype": "Currency", "width": 110},
		{"label": _("Principal Recovered"), "fieldname": "principal_recovered", "fieldtype": "Currency", "width": 120},
		{"label": _("Interest Recovered"), "fieldname": "interest_recovered", "fieldtype": "Currency", "width": 120},
		{"label": _("Outstanding Principal"), "fieldname": "outstanding_principal", "fieldtype": "Currency", "width": 130},
		{"label": _("Outstanding Interest"), "fieldname": "outstanding_interest", "fieldtype": "Currency", "width": 130},
		{"label": _("Arrears"), "fieldname": "arrears_amount", "fieldtype": "Currency", "width": 100},
		{"label": _("Aging Bucket"), "fieldname": "aging_bucket", "fieldtype": "Data", "width": 100},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
	]


def get_data(filters):
	conditions = ["l.docstatus = 1", "l.first_deduction_month <= %(as_on_date)s"]
	values = {"as_on_date": filters.as_on_date or nowdate()}

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

	# A correlated subquery here (one `Loan Repayment Schedule` scan per
	# `Loan` row) turns into O(loans x schedule rows) — fine at demo scale,
	# not at 2,000 loans. Pre-aggregating the oldest due date per loan into
	# its own grouped subquery, then joining it once, keeps this to a
	# single pass over each table.
	rows = frappe.db.sql(f"""
		select
			l.name as loan, l.employee, e.employee_name, e.department, l.loan_type,
			l.sanctioned_amount, l.disbursed_amount, l.principal_recovered, l.interest_recovered,
			l.outstanding_principal, l.outstanding_interest, l.arrears_amount, l.status,
			oldest.oldest_due_date
		from `tabLoan` l
		join `tabEmployee` e on e.name = l.employee
		left join (
			select loan, min(due_date) as oldest_due_date
			from `tabLoan Repayment Schedule`
			where is_current = 1 and due_date <= %(as_on_date)s
				and status in ('Pending', 'Partially Recovered')
			group by loan
		) oldest on oldest.loan = l.name
		where {" and ".join(conditions)}
		order by l.arrears_amount desc, l.name
	""", values, as_dict=True)

	for row in rows:
		row["aging_bucket"] = bucket(row.pop("oldest_due_date"), values["as_on_date"])
	return rows


def bucket(oldest_due_date, as_on_date):
	if not oldest_due_date:
		return "Current"
	days = (frappe.utils.getdate(as_on_date) - frappe.utils.getdate(oldest_due_date)).days
	if days <= 0:
		return "Current"
	if days <= 30:
		return "0-30"
	if days <= 60:
		return "31-60"
	if days <= 90:
		return "61-90"
	return "90+"


def get_summary(filters, data):
	# The control-account tie must cover every loan that can have money
	# against the receivable account — not just the rows the aging list
	# chooses to display. `get_data()` filters listed rows to
	# `first_deduction_month <= as_on_date` (this list is about repayment
	# having started); a loan can be disbursed, and so already sitting on
	# the receivable account, before its first deduction is due. Reusing
	# `data` here previously under-counted the computed side against
	# exactly that population, producing a false "break" — so this queries
	# the full outstanding total independently, scoped the same way the GL
	# side is (company/date only), not by the list's display filter.
	conditions = ["l.docstatus = 1"]
	values = {"as_on_date": filters.as_on_date or nowdate()}
	joins = ""
	if filters.company:
		conditions.append("l.company = %(company)s")
		values["company"] = filters.company
	if filters.loan_type:
		conditions.append("l.loan_type = %(loan_type)s")
		values["loan_type"] = filters.loan_type
	if filters.department:
		joins = "join `tabEmployee` e on e.name = l.employee"
		conditions.append("e.department = %(department)s")
		values["department"] = filters.department

	scope = employee_scope_condition("Loan", "employee", frappe.session.user)
	if scope == "1=0":
		computed_total = 0
	else:
		if scope:
			conditions.append(f"({scope})")
		result = frappe.db.sql(
			f"""select sum(l.outstanding_principal) + sum(l.outstanding_interest)
				from `tabLoan` l {joins} where {" and ".join(conditions)}""",
			values,
		)
		computed_total = flt(result[0][0]) if result and result[0][0] else 0

	gl_balance = 0
	if filters.company:
		account = frappe.db.get_value("Loan Type", {"company": filters.company}, "loan_account")
		if account:
			result = frappe.db.sql(
				"""select sum(debit) - sum(credit) from `tabGL Entry`
					where account=%s and company=%s and posting_date<=%s and is_cancelled=0""",
				(account, filters.company, filters.as_on_date or nowdate()),
			)
			gl_balance = flt(result[0][0]) if result and result[0][0] else 0

	return [
		{"label": _("Computed Outstanding (Principal + Interest)"), "value": computed_total, "datatype": "Currency"},
		{"label": _("GL Balance (Employee Loan Receivable)"), "value": gl_balance, "datatype": "Currency"},
		{"label": _("Difference"), "value": flt(gl_balance - computed_total), "datatype": "Currency"},
	]
