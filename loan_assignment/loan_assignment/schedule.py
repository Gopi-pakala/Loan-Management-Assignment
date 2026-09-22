import frappe
from frappe.utils import add_months, flt, getdate

from loan_assignment.loan_assignment.utils import (
	compute_flat_schedule,
	compute_reducing_schedule,
	get_precision_for_company,
)


def generate_initial_schedule(loan):
	"""Called once, at Loan submit (Sanction). Builds the planned schedule
	against the sanctioned amount, before any money has actually moved.
	"""
	_insert_rows(loan, from_period_no=1, principal=loan.sanctioned_amount,
		tenure_remaining=loan.tenure_months, due_date=loan.first_deduction_month, version=1)


def rebuild_from_period(loan, from_period_no, principal, tenure_remaining, note=None, due_date_override=None):
	"""Regenerates the NOT-YET-recovered tail of the schedule from
	`from_period_no` forward, on `principal` amortised over
	`tenure_remaining` periods at the loan's original rate/mode. Periods
	before `from_period_no` — anything already recovered — are left
	completely untouched. The periods being replaced are not deleted; they
	are flagged `is_current=0` so both the old and new schedule remain in
	the audit trail, and the loan's schedule_version is bumped.
	"""
	old_rows = frappe.get_all(
		"Loan Repayment Schedule",
		filters={"loan": loan.name, "is_current": 1, "period_no": [">=", from_period_no]},
		fields=["name", "period_no", "due_date"],
		order_by="period_no asc",
	)
	due_date = due_date_override or (old_rows[0].due_date if old_rows else _next_due_date(loan, from_period_no))

	for row in old_rows:
		frappe.db.set_value("Loan Repayment Schedule", row.name, "is_current", 0)

	new_version = (loan.schedule_version or 1) + 1
	if tenure_remaining > 0 and flt(principal) > 0:
		_insert_rows(loan, from_period_no, principal, tenure_remaining, due_date, new_version, note)
	loan.db_set("schedule_version", new_version)
	_recompute_loan_aggregates(loan)


def _insert_rows(loan, from_period_no, principal, tenure_remaining, due_date, version, note=None):
	precision = get_precision_for_company(loan.company)
	compute = compute_flat_schedule if loan.interest_mode == "Flat" else compute_reducing_schedule
	rows = compute(principal, loan.rate_of_interest, tenure_remaining, due_date, precision)

	for offset, row in enumerate(rows):
		frappe.get_doc({
			"doctype": "Loan Repayment Schedule",
			"loan": loan.name,
			"period_no": from_period_no + offset,
			"due_date": row.due_date,
			"schedule_version": version,
			"is_current": 1,
			"opening_balance": row.opening_balance,
			"instalment_amount": row.instalment_amount,
			"interest_amount": row.interest_amount,
			"principal_amount": row.principal_amount,
			"closing_balance": row.closing_balance,
			"recovered_amount": 0,
			"recovered_principal": 0,
			"recovered_interest": 0,
			"status": "Pending",
			"note": note,
		}).insert(ignore_permissions=True)


def _next_due_date(loan, from_period_no):
	if from_period_no <= 1:
		return loan.first_deduction_month
	last_current = frappe.get_all(
		"Loan Repayment Schedule",
		filters={"loan": loan.name, "is_current": 1, "period_no": ["<", from_period_no]},
		fields=["due_date"],
		order_by="period_no desc",
		limit=1,
	)
	if last_current:
		return add_months(getdate(last_current[0].due_date), 1)
	return loan.first_deduction_month


def get_next_pending_period(loan_name):
	rows = frappe.get_all(
		"Loan Repayment Schedule",
		filters={"loan": loan_name, "is_current": 1, "status": ["in", ["Pending", "Partially Recovered"]]},
		fields=["name", "period_no", "due_date", "opening_balance", "instalment_amount",
			"interest_amount", "principal_amount", "closing_balance", "recovered_amount",
			"recovered_principal", "recovered_interest"],
		order_by="period_no asc",
		limit=1,
	)
	return rows[0] if rows else None


def _recompute_loan_aggregates(loan):
	totals = frappe.db.sql(
		"""select sum(recovered_principal), sum(recovered_interest)
			from `tabLoan Repayment Schedule` where loan=%s and is_current=1""",
		loan.name,
	)
	principal_recovered = flt(totals[0][0]) if totals else 0
	interest_recovered = flt(totals[0][1]) if totals else 0

	outstanding_principal = flt(loan.disbursed_amount) - principal_recovered

	# Outstanding interest is accrual-basis: only periods due so far have
	# actually been recognised as income (via the monthly accrual JE), so
	# only those can be "outstanding" against the receivable. Interest on
	# periods that have not yet come due isn't on the books yet — matches
	# the Employee Loan Receivable control-account tie in the aging report.
	due_remaining = frappe.db.sql(
		"""select sum(interest_amount) - sum(recovered_interest)
			from `tabLoan Repayment Schedule`
			where loan=%s and is_current=1 and due_date<=%s and status!='Recovered'""",
		(loan.name, frappe.utils.nowdate()),
	)
	outstanding_interest = flt(due_remaining[0][0]) if due_remaining and due_remaining[0][0] else 0

	arrears = frappe.db.sql(
		"""select sum(instalment_amount) - sum(recovered_amount)
			from `tabLoan Repayment Schedule`
			where loan=%s and is_current=1 and due_date<%s
				and status in ('Pending', 'Partially Recovered', 'Skipped')""",
		(loan.name, frappe.utils.nowdate()),
	)

	loan.db_set({
		"principal_recovered": principal_recovered,
		"interest_recovered": interest_recovered,
		"outstanding_principal": outstanding_principal,
		"outstanding_interest": outstanding_interest,
		"arrears_amount": flt(arrears) if arrears else 0,
	})


def recompute_loan_aggregates(loan):
	_recompute_loan_aggregates(loan)
