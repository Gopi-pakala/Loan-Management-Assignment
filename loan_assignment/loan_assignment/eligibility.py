import frappe
from frappe import _
from frappe.utils import flt, getdate, month_diff, nowdate

from loan_assignment.loan_assignment.utils import is_on_probation, is_serving_notice


class HardBlock(Exception):
	pass


def check_hard_blocks(employee_doc):
	"""Probation / notice-period blocks. No override escape hatch — these
	are absolute per the brief, unlike the soft checks below.
	"""
	if is_on_probation(employee_doc):
		raise HardBlock(_("Employees on probation cannot apply for a loan."))
	if is_serving_notice(employee_doc):
		raise HardBlock(_("Employees serving notice cannot apply for a loan."))


def run_soft_checks(application):
	"""Returns (status, notes) — Eligible/Ineligible plus a human-readable
	reason. Does not raise; the caller decides whether an override applies.
	"""
	loan_type = frappe.get_cached_doc("Loan Type", application.loan_type)
	notes = []

	max_amount = flt(application.monthly_gross) * flt(loan_type.max_multiple_of_gross)
	if loan_type.max_multiple_of_gross and flt(application.requested_amount) > max_amount:
		notes.append(_("Requested amount {0} exceeds {1}x monthly gross ({2}).").format(
			application.requested_amount, loan_type.max_multiple_of_gross, max_amount
		))

	if loan_type.max_tenure_months and application.tenure_months > loan_type.max_tenure_months:
		notes.append(_("Tenure {0} months exceeds the {1}-month ceiling for {2}.").format(
			application.tenure_months, loan_type.max_tenure_months, loan_type.loan_type_name
		))

	service_months = month_diff(getdate(nowdate()), getdate(
		frappe.db.get_value("Employee", application.employee, "date_of_joining")
	))
	if loan_type.min_service_months and service_months < loan_type.min_service_months:
		notes.append(_("Employee has {0} months of service; {1} required for {2}.").format(
			service_months, loan_type.min_service_months, loan_type.loan_type_name
		))

	active_count = frappe.db.count("Loan", {
		"employee": application.employee,
		"loan_type": application.loan_type,
		"docstatus": 1,
		"status": ["not in", ["Closed", "Written Off"]],
	})
	max_active = loan_type.max_active_loans or 1
	if active_count >= max_active:
		notes.append(_("Employee already holds {0} active {1} loan(s); limit is {2}.").format(
			active_count, loan_type.loan_type_name, max_active
		))

	if notes:
		return "Ineligible", " ".join(notes)
	return "Eligible", ""
