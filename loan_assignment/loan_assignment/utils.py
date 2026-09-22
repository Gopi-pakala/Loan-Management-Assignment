import frappe
from frappe.utils import add_months, flt, getdate, nowdate


def get_precision_for_company(company):
	"""Company currency precision — never hard-code decimal places. System
	Settings' `currency_precision` is an explicit site-wide override some
	sites set; when unset, precision is derived from the company currency's
	own number format.
	"""
	from frappe.utils.number_format import NumberFormat

	override = frappe.db.get_single_value("System Settings", "currency_precision")
	if override:
		return int(override)

	currency = company and frappe.get_cached_value("Company", company, "default_currency")
	number_format = currency and frappe.get_cached_value("Currency", currency, "number_format")
	if number_format:
		return NumberFormat.from_string(number_format).precision
	return 2


def get_monthly_gross(employee):
	"""Best-effort monthly gross salary for an employee, snapshotted at
	application time (never a live/recurring lookup per period).

	Preference order: latest submitted Salary Slip's gross pay, falling back
	to the active Salary Structure Assignment's base. Returns 0 if neither
	exists (eligibility checks then fail closed since 0 x multiple = 0).
	"""
	slip = frappe.db.get_value(
		"Salary Slip",
		{"employee": employee, "docstatus": 1},
		["gross_pay"],
		order_by="end_date desc",
	)
	if slip:
		return flt(slip)

	assignment = frappe.db.get_value(
		"Salary Structure Assignment",
		{"employee": employee, "docstatus": 1, "from_date": ["<=", nowdate()]},
		["base"],
		order_by="from_date desc",
	)
	if assignment:
		return flt(assignment)

	return 0.0


def is_on_probation(employee):
	"""Employee doc/dict. ERPNext's Employee has `final_confirmation_date`
	which HR sets once probation is confirmed; if it is unset or still in
	the future, the employee is treated as being on probation. Documented
	assumption — see DECISIONS.md.
	"""
	final_confirmation_date = employee.get("final_confirmation_date")
	if not final_confirmation_date:
		return True
	return getdate(final_confirmation_date) > getdate(nowdate())


def is_serving_notice(employee):
	"""ERPNext sets `relieving_date` once an employee's exit/resignation is
	accepted, well before their `status` actually flips to Left. Treat a
	future relieving_date as "serving notice". Documented assumption.
	"""
	relieving_date = employee.get("relieving_date")
	if not relieving_date:
		return False
	return getdate(relieving_date) >= getdate(nowdate())


def compute_flat_schedule(principal, annual_rate, tenure_months, first_due_date, precision=2):
	"""Flat-rate schedule: interest computed once on the original principal
	for the full tenure, divided evenly. Rounding residual absorbed by the
	final period so the principal column totals exactly `principal`.
	"""
	principal = flt(principal, precision)
	total_interest = flt(principal * flt(annual_rate) / 100 * flt(tenure_months) / 12, precision)
	base_principal_per_period = flt(principal / tenure_months, precision)
	base_interest_per_period = flt(total_interest / tenure_months, precision)

	rows = []
	opening = principal
	principal_booked = 0.0
	interest_booked = 0.0
	for period_no in range(1, tenure_months + 1):
		is_last = period_no == tenure_months
		if is_last:
			principal_amount = flt(principal - principal_booked, precision)
			interest_amount = flt(total_interest - interest_booked, precision)
		else:
			principal_amount = base_principal_per_period
			interest_amount = base_interest_per_period

		principal_booked = flt(principal_booked + principal_amount, precision)
		interest_booked = flt(interest_booked + interest_amount, precision)
		closing = flt(opening - principal_amount, precision)

		rows.append(_row(
			period_no, first_due_date, opening, principal_amount, interest_amount, closing, precision,
		))
		opening = closing

	return rows


def compute_reducing_schedule(principal, annual_rate, tenure_months, first_due_date, precision=2):
	"""Reducing-balance schedule: interest accrues on the outstanding
	balance; instalment is the standard amortisation payment. Zero-rate
	loans (e.g. Emergency) degrade to a straight-line split. The final
	instalment is recomputed (never the EMI) so the schedule closes to
	exactly zero.
	"""
	principal = flt(principal, precision)
	r = flt(annual_rate) / 100 / 12

	if r == 0:
		emi = flt(principal / tenure_months, precision)
	else:
		factor = (1 + r) ** tenure_months
		emi = flt(principal * r * factor / (factor - 1), precision)

	rows = []
	opening = principal
	for period_no in range(1, tenure_months + 1):
		is_last = period_no == tenure_months
		interest_amount = flt(opening * r, precision)

		if is_last:
			principal_amount = flt(opening, precision)
			instalment_amount = flt(principal_amount + interest_amount, precision)
			closing = 0.0
		else:
			principal_amount = flt(emi - interest_amount, precision)
			instalment_amount = emi
			closing = flt(opening - principal_amount, precision)

		rows.append(_row(
			period_no, first_due_date, opening, principal_amount, interest_amount, closing, precision,
			instalment_amount=instalment_amount,
		))
		opening = closing

	return rows


def _row(period_no, first_due_date, opening, principal_amount, interest_amount, closing, precision, instalment_amount=None):
	return frappe._dict(
		period_no=period_no,
		due_date=add_months(getdate(first_due_date), period_no - 1),
		opening_balance=flt(opening, precision),
		principal_amount=flt(principal_amount, precision),
		interest_amount=flt(interest_amount, precision),
		instalment_amount=flt(instalment_amount if instalment_amount is not None else principal_amount + interest_amount, precision),
		closing_balance=flt(closing, precision),
	)
