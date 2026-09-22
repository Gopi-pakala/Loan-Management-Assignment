import frappe
from frappe.utils import flt, getdate

from loan_assignment.loan_assignment import retrospective
from loan_assignment.loan_assignment.schedule import recompute_loan_aggregates
from loan_assignment.loan_assignment.utils import get_precision_for_company


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

		# A correction doesn't need to fully re-settle a period to
		# invalidate later periods' already-recognised interest — any
		# actual change to what was recovered this period (`took > 0`)
		# shifts the true balance those later periods should have been
		# computed against, whether or not this period ends up fully
		# settled by it.
		if retrospective.has_later_recovered_periods(loan_doc.name, period.period_no):
			retrospective.apply_catchup_adjustment(loan_doc, period.period_no)

	recompute_loan_aggregates(loan_doc)
	loan_doc.reload()
	loan_doc.update_status()

	return {
		"allocated_interest": allocated_interest,
		"allocated_principal": allocated_principal,
		"allocated_arrears": allocated_arrears,
		"unallocated": remaining,
	}


def compute_foreclosure_payoff(loan, as_of_date):
	"""Payoff figure per the brief: outstanding principal, interest accrued
	to `as_of_date`, any pre-closure charge on the Loan Type, less a rebate
	on unaccrued interest.

	This design already computes `outstanding_interest` strictly on an
	accrual/due basis (see `schedule.py::_recompute_loan_aggregates`) —
	interest on periods not yet due was never added to what's payable in
	the first place, so there is nothing to subtract for those. The rebate
	is real, though: it is exactly the interest scheduled on the periods
	*after* `as_of_date` that foreclosing waives outright. It is reported
	here for disclosure/audit even though, structurally, it was never part
	of the payable total to begin with — the payoff total below already
	excludes it.
	"""
	precision = get_precision_for_company(loan.company)
	as_of_date = getdate(as_of_date)

	rows = frappe.get_all(
		"Loan Repayment Schedule",
		filters={"loan": loan.name, "is_current": 1, "status": ["in", ["Pending", "Partially Recovered"]]},
		fields=["name", "due_date", "interest_amount", "principal_amount", "recovered_interest", "recovered_principal"],
		order_by="period_no asc",
	)

	interest_accrued_to_date = 0.0
	rebate_on_unaccrued_interest = 0.0
	for row in rows:
		due_interest = flt(row.interest_amount) - flt(row.recovered_interest)
		if getdate(row.due_date) <= as_of_date:
			interest_accrued_to_date += due_interest
		else:
			rebate_on_unaccrued_interest += due_interest

	outstanding_principal = flt(loan.disbursed_amount) - flt(loan.principal_recovered)
	loan_type_charge_percent = flt(frappe.db.get_value("Loan Type", loan.loan_type, "pre_closure_charge_percent"))
	pre_closure_charge = flt(outstanding_principal * loan_type_charge_percent / 100, precision)

	total_payoff = flt(
		outstanding_principal + flt(interest_accrued_to_date, precision) + pre_closure_charge, precision
	)

	return frappe._dict({
		"outstanding_principal": flt(outstanding_principal, precision),
		"interest_accrued_to_date": flt(interest_accrued_to_date, precision),
		"pre_closure_charge": pre_closure_charge,
		"rebate_on_unaccrued_interest": flt(rebate_on_unaccrued_interest, precision),
		"total_payoff": total_payoff,
	})


def close_out_foreclosure(loan_doc, as_of_date, payoff):
	"""Applies a foreclosure payoff: due periods are recovered in full,
	periods after `as_of_date` are closed as `Skipped` with their principal
	treated as recovered (it's covered by the lump-sum payoff) and their
	interest left un-recovered — that gap *is* the rebate, and is what
	brings `outstanding_interest`/`outstanding_principal` to zero without
	ever having charged for it.
	"""
	as_of_date = getdate(as_of_date)
	rows = frappe.get_all(
		"Loan Repayment Schedule",
		filters={"loan": loan_doc.name, "is_current": 1, "status": ["in", ["Pending", "Partially Recovered"]]},
		fields=["name", "due_date", "interest_amount", "principal_amount", "recovered_interest", "recovered_principal"],
		order_by="period_no asc",
	)

	for row in rows:
		principal_due = flt(row.principal_amount) - flt(row.recovered_principal)
		if getdate(row.due_date) <= as_of_date:
			interest_due = flt(row.interest_amount) - flt(row.recovered_interest)
			frappe.db.set_value("Loan Repayment Schedule", row.name, {
				"recovered_principal": flt(row.recovered_principal) + principal_due,
				"recovered_interest": flt(row.recovered_interest) + interest_due,
				"recovered_amount": flt(row.recovered_principal) + principal_due + flt(row.recovered_interest) + interest_due,
				"status": "Recovered",
			})
		else:
			frappe.db.set_value("Loan Repayment Schedule", row.name, {
				"recovered_principal": flt(row.recovered_principal) + principal_due,
				"recovered_amount": flt(row.recovered_principal) + principal_due,
				"status": "Skipped",
			})

	recompute_loan_aggregates(loan_doc)
	loan_doc.reload()
	loan_doc.update_status()


def reverse_foreclosure_skip(loan_name):
	"""Undoes the `Skipped` (waived-interest) side of a foreclosure
	close-out — the `Recovered` (due-and-collected) side is undone
	separately by the normal `reverse_repayment` LIFO walk, since a
	foreclosure only ever touches Pending/Partially Recovered periods and
	so never collides with an independently-recovered period.
	"""
	rows = frappe.get_all(
		"Loan Repayment Schedule",
		filters={"loan": loan_name, "is_current": 1, "status": "Skipped"},
		fields=["name"],
	)
	for row in rows:
		frappe.db.set_value("Loan Repayment Schedule", row.name, {
			"recovered_principal": 0,
			"recovered_amount": 0,
			"status": "Pending",
		})


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
