import frappe
from frappe.utils import get_first_day, get_last_day, nowdate

from loan_assignment.loan_assignment.schedule import recompute_loan_aggregates

ACTIVE_STATUSES = ["Partially Disbursed", "Disbursed", "Repaying"]


def accrue_monthly_interest():
	"""Re-running for a month already accrued is a no-op, not a fault —
	each (loan, period_start) pair is accrued at most once, checked before
	creating a new Loan Interest Accrual.
	"""
	period_start = get_first_day(nowdate())
	period_end = get_last_day(nowdate())

	loans = frappe.get_all("Loan", filters={"docstatus": 1, "status": ["in", ACTIVE_STATUSES]}, pluck="name")
	for loan_name in loans:
		if frappe.db.exists("Loan Interest Accrual", {
			"loan": loan_name, "period_start": period_start, "docstatus": 1,
		}):
			continue

		due_row = frappe.db.get_value(
			"Loan Repayment Schedule",
			{
				"loan": loan_name, "is_current": 1,
				"due_date": ["between", [period_start, period_end]],
			},
			["interest_amount"],
		)
		if not due_row:
			continue

		accrual = frappe.get_doc({
			"doctype": "Loan Interest Accrual",
			"loan": loan_name,
			"period_start": period_start,
			"period_end": period_end,
			"interest_amount": due_row,
		})
		accrual.flags.ignore_permissions = True
		accrual.insert()
		accrual.submit()


def update_arrears_ageing():
	loans = frappe.get_all("Loan", filters={"docstatus": 1, "status": ["in", ACTIVE_STATUSES]}, pluck="name")
	for loan_name in loans:
		loan = frappe.get_doc("Loan", loan_name)
		recompute_loan_aggregates(loan)
		loan.reload()
		loan.update_status()
