"""Retrospective-correction catch-up.

Policy (see DECISIONS.md): when a salary slip covering an *earlier* period
is cancelled and resubmitted with different figures after later periods
have already been recovered and posted, we never rewrite those later,
already-reported periods' schedule rows or their salary slips. Principal
self-heals automatically, since a loan's outstanding_principal is always
`disbursed_amount - actual principal recovered to date`, not a sum of
stale per-period projections.

Interest does not self-heal the same way: the later periods already
recognised a specific interest amount computed against the *old* (now
wrong) opening balance. This module computes the one-off difference
between what was recognised and what the original amortisation terms
would have produced given the corrected balance, and posts it as a single
journal adjustment dated in the current period — visible, dated when it
was discovered, and never touching a closed period's own records.
"""

import frappe
from frappe.utils import flt, nowdate

from loan_assignment.loan_assignment.ledger import _elevated
from loan_assignment.loan_assignment.utils import compute_flat_schedule, compute_reducing_schedule, get_precision_for_company


def has_later_recovered_periods(loan_name, period_no):
	return bool(frappe.db.exists("Loan Repayment Schedule", {
		"loan": loan_name, "is_current": 1, "period_no": [">", period_no], "status": "Recovered",
	}))


def apply_catchup_adjustment(loan, corrected_period_no):
	"""Called right after period `corrected_period_no` has been re-recovered
	with corrected figures. Recomputes what periods after it *should* show
	given the corrected closing balance, compares to what was actually
	recognised, and posts the net interest difference as one Journal Entry
	dated today.
	"""
	later_rows = frappe.get_all(
		"Loan Repayment Schedule",
		filters={"loan": loan.name, "is_current": 1, "period_no": [">", corrected_period_no], "status": "Recovered"},
		fields=["name", "period_no", "interest_amount", "principal_amount"],
		order_by="period_no asc",
	)
	if not later_rows:
		return

	corrected_row = frappe.db.get_value(
		"Loan Repayment Schedule",
		{"loan": loan.name, "is_current": 1, "period_no": corrected_period_no},
		["closing_balance"], as_dict=True,
	)
	if not corrected_row:
		return

	precision = get_precision_for_company(loan.company)
	compute = compute_flat_schedule if loan.interest_mode == "Flat" else compute_reducing_schedule
	recomputed = compute(
		corrected_row.closing_balance, loan.rate_of_interest, len(later_rows),
		frappe.utils.add_months(frappe.utils.nowdate(), 0), precision,
	)

	recognised_interest = sum(flt(r.interest_amount) for r in later_rows)
	true_interest = sum(flt(r.interest_amount) for r in recomputed)
	delta = flt(recognised_interest - true_interest, precision)

	if abs(delta) < (10 ** -precision):
		return

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = loan.company
	je.posting_date = nowdate()
	je.user_remark = (
		f"Retrospective correction catch-up for {loan.name}: period {corrected_period_no} was "
		f"recovered with revised figures after periods {later_rows[0].period_no}-{later_rows[-1].period_no} "
		f"had already been posted. Net interest adjustment {delta}."
	)
	if delta > 0:
		je.append("accounts", {"account": loan.interest_account, "debit_in_account_currency": delta,
			"party_type": "Employee", "party": loan.employee})
		je.append("accounts", {"account": loan.loan_account, "credit_in_account_currency": delta,
			"party_type": "Employee", "party": loan.employee})
	else:
		amount = abs(delta)
		je.append("accounts", {"account": loan.loan_account, "debit_in_account_currency": amount,
			"party_type": "Employee", "party": loan.employee})
		je.append("accounts", {"account": loan.interest_account, "credit_in_account_currency": amount,
			"party_type": "Employee", "party": loan.employee})

	je.flags.ignore_permissions = True
	with _elevated():
		je.insert()
		je.submit()

	loan.db_set("outstanding_interest", flt(loan.outstanding_interest) - delta)
	return je.name
