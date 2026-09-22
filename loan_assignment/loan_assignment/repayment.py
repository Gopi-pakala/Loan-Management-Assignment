import frappe
from frappe.utils import flt

from loan_assignment.loan_assignment import retrospective
from loan_assignment.loan_assignment.schedule import recompute_loan_aggregates


def allocate_repayment(loan_doc, amount):
	"""Interest-then-principal within a period, oldest period first (see
	DECISIONS.md for why). The first period touched is reported as a
	normal interest/principal split; anything beyond it is bucketed as
	`allocated_arrears` — the same "first period is regular, the rest is
	backlog" convention used for payroll recovery, kept uniform across
	both channels.
	"""
	periods = frappe.get_all(
		"Loan Repayment Schedule",
		filters={"loan": loan_doc.name, "is_current": 1, "status": ["in", ["Pending", "Partially Recovered"]]},
		fields=["name", "period_no", "instalment_amount", "interest_amount", "principal_amount",
			"recovered_amount", "recovered_principal", "recovered_interest"],
		order_by="period_no asc",
	)

	remaining = flt(amount)
	allocated_interest = 0.0
	allocated_principal = 0.0
	allocated_arrears = 0.0
	last_settled_period = None

	for idx, period in enumerate(periods):
		if remaining <= 0:
			break

		interest_due = flt(period.interest_amount) - flt(period.recovered_interest)
		take_interest = min(remaining, interest_due)
		remaining -= take_interest

		principal_due = flt(period.principal_amount) - flt(period.recovered_principal)
		take_principal = min(remaining, principal_due)
		remaining -= take_principal

		took = take_interest + take_principal
		if took <= 0:
			continue

		new_recovered_amount = flt(period.recovered_amount) + took
		new_recovered_interest = flt(period.recovered_interest) + take_interest
		new_recovered_principal = flt(period.recovered_principal) + take_principal
		is_settled = new_recovered_amount >= flt(period.instalment_amount) - 0.005

		frappe.db.set_value("Loan Repayment Schedule", period.name, {
			"recovered_amount": new_recovered_amount,
			"recovered_principal": new_recovered_principal,
			"recovered_interest": new_recovered_interest,
			"status": "Recovered" if is_settled else "Partially Recovered",
		})

		if idx == 0:
			allocated_interest += take_interest
			allocated_principal += take_principal
		else:
			allocated_arrears += took

		if is_settled:
			last_settled_period = period.period_no

	if last_settled_period and retrospective.has_later_recovered_periods(loan_doc.name, last_settled_period):
		retrospective.apply_catchup_adjustment(loan_doc, last_settled_period)

	recompute_loan_aggregates(loan_doc)
	loan_doc.reload()
	loan_doc.update_status()

	return {
		"allocated_interest": allocated_interest,
		"allocated_principal": allocated_principal,
		"allocated_arrears": allocated_arrears,
		"unallocated": remaining,
	}


def reverse_repayment(loan_name, allocated_interest, allocated_principal, allocated_arrears):
	"""Best-effort reversal on cancel: pulls the amounts back off the
	most-recently-recovered periods first (LIFO), mirroring how they were
	applied.
	"""
	to_reverse = flt(allocated_interest) + flt(allocated_principal) + flt(allocated_arrears)
	if to_reverse <= 0:
		return

	periods = frappe.get_all(
		"Loan Repayment Schedule",
		filters={"loan": loan_name, "is_current": 1, "status": ["in", ["Recovered", "Partially Recovered"]]},
		fields=["name", "period_no", "recovered_amount", "recovered_principal", "recovered_interest"],
		order_by="period_no desc",
	)

	remaining = to_reverse
	for period in periods:
		if remaining <= 0:
			break
		reverse_amt = min(remaining, flt(period.recovered_amount))
		if reverse_amt <= 0:
			continue
		ratio = reverse_amt / flt(period.recovered_amount) if period.recovered_amount else 0
		frappe.db.set_value("Loan Repayment Schedule", period.name, {
			"recovered_amount": flt(period.recovered_amount) - reverse_amt,
			"recovered_principal": flt(period.recovered_principal) * (1 - ratio),
			"recovered_interest": flt(period.recovered_interest) * (1 - ratio),
			"status": "Pending" if flt(period.recovered_amount) - reverse_amt <= 0.005 else "Partially Recovered",
		})
		remaining -= reverse_amt

	loan_doc = frappe.get_doc("Loan", loan_name)
	recompute_loan_aggregates(loan_doc)
	loan_doc.reload()
	loan_doc.update_status()
