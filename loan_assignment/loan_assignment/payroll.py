import frappe
from frappe.utils import flt

from loan_assignment.loan_assignment import retrospective
from loan_assignment.loan_assignment.schedule import recompute_loan_aggregates

LOAN_RECOVERY_COMPONENT = "Loan Recovery"


def get_active_loans(employee, company, as_of_date):
	return frappe.get_all(
		"Loan",
		filters={
			"employee": employee,
			"company": company,
			"docstatus": 1,
			"status": ["in", ["Partially Disbursed", "Disbursed", "Repaying"]],
			"first_deduction_month": ["<=", as_of_date],
		},
		fields=["name"],
	)


def get_due_periods(loan_name, as_of_date):
	return frappe.get_all(
		"Loan Repayment Schedule",
		filters={
			"loan": loan_name, "is_current": 1, "due_date": ["<=", as_of_date],
			"status": ["in", ["Pending", "Partially Recovered"]],
		},
		fields=["name", "period_no", "instalment_amount", "interest_amount", "principal_amount",
			"recovered_amount", "recovered_principal", "recovered_interest"],
		order_by="period_no asc",
	)


def recover_against_available_funds(salary_slip, available):
	remaining = flt(available)
	detail_rows = []

	for loan in get_active_loans(salary_slip.employee, salary_slip.company, salary_slip.end_date):
		if remaining <= 0:
			break
		periods = get_due_periods(loan.name, salary_slip.end_date)
		loan_doc = None

		for idx, period in enumerate(periods):
			if remaining <= 0:
				break
			instalment_due = flt(period.instalment_amount) - flt(period.recovered_amount)
			if instalment_due <= 0:
				continue

			take = min(instalment_due, remaining)
			interest_due = flt(period.interest_amount) - flt(period.recovered_interest)
			interest_share = min(take, interest_due)
			principal_share = take - interest_share

			new_recovered_amount = flt(period.recovered_amount) + take
			new_recovered_principal = flt(period.recovered_principal) + principal_share
			new_recovered_interest = flt(period.recovered_interest) + interest_share
			is_settled = new_recovered_amount >= flt(period.instalment_amount) - 0.005
			frappe.db.set_value("Loan Repayment Schedule", period.name, {
				"recovered_amount": new_recovered_amount,
				"recovered_principal": new_recovered_principal,
				"recovered_interest": new_recovered_interest,
				"status": "Recovered" if is_settled else "Partially Recovered",
			})

			is_backlog = idx > 0
			detail_rows.append({
				"loan": loan.name,
				"schedule_period": period.period_no,
				"principal_amount": 0 if is_backlog else principal_share,
				"interest_amount": 0 if is_backlog else interest_share,
				"arrears_amount": take if is_backlog else 0,
				"shortfall_amount": flt(instalment_due - take, 2),
				"total_recovered": take,
			})

			remaining -= take

			# A correction doesn't need to fully re-settle the period to
			# invalidate later periods' recognised interest — even a
			# partial change to how much principal was actually recovered
			# this period shifts the true balance those later periods
			# should have been computed against (e.g. a corrected slip
			# that recovers *less* than before, because it turns out the
			# employee took unpaid leave that month).
			if take > 0 and retrospective.has_later_recovered_periods(loan.name, period.period_no):
				loan_doc = loan_doc or frappe.get_doc("Loan", loan.name)
				retrospective.apply_catchup_adjustment(loan_doc, period.period_no)

		loan_doc = loan_doc or frappe.get_doc("Loan", loan.name)
		recompute_loan_aggregates(loan_doc)
		loan_doc.reload()
		loan_doc.update_status()

	return detail_rows


def reverse_recovery_for_slip(salary_slip_name):
	rows = frappe.get_all(
		"Loan Recovery Detail",
		filters={"parenttype": "Salary Slip", "parent": salary_slip_name},
		fields=["loan", "schedule_period", "principal_amount", "interest_amount", "arrears_amount", "total_recovered"],
	)
	touched_loans = set()
	for row in rows:
		schedule_name = frappe.db.get_value(
			"Loan Repayment Schedule",
			{"loan": row.loan, "period_no": row.schedule_period, "is_current": 1},
		)
		if not schedule_name:
			continue
		current = frappe.db.get_value(
			"Loan Repayment Schedule", schedule_name,
			["recovered_amount", "recovered_principal", "recovered_interest", "instalment_amount"], as_dict=True,
		)
		new_recovered_amount = flt(current.recovered_amount) - flt(row.total_recovered)
		new_recovered_principal = flt(current.recovered_principal) - flt(row.principal_amount)
		new_recovered_interest = flt(current.recovered_interest) - flt(row.interest_amount)
		status = "Pending" if new_recovered_amount <= 0.005 else "Partially Recovered"
		frappe.db.set_value("Loan Repayment Schedule", schedule_name, {
			"recovered_amount": max(new_recovered_amount, 0),
			"recovered_principal": max(new_recovered_principal, 0),
			"recovered_interest": max(new_recovered_interest, 0),
			"status": status,
		})
		touched_loans.add(row.loan)

	for loan_name in touched_loans:
		loan_doc = frappe.get_doc("Loan", loan_name)
		recompute_loan_aggregates(loan_doc)
		loan_doc.reload()
		loan_doc.update_status()


def ensure_loan_recovery_deduction_row(salary_slip, amount):
	salary_slip.append("deductions", {
		"salary_component": LOAN_RECOVERY_COMPONENT,
		"amount": amount,
		"default_amount": amount,
	})
