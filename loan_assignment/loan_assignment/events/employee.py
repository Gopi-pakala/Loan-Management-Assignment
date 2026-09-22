import frappe
from frappe import _


def block_left_with_outstanding_loans(doc, method=None):
	if doc.status != "Left":
		return

	previous_status = frappe.db.get_value("Employee", doc.name, "status") if not doc.is_new() else None
	if previous_status == "Left":
		return

	outstanding = frappe.get_all(
		"Loan",
		filters={
			"employee": doc.name,
			"docstatus": 1,
			"status": ["not in", ["Closed", "Written Off"]],
		},
		fields=["name", "outstanding_principal", "outstanding_interest"],
	)
	if not outstanding:
		return

	details = ", ".join(
		f"{loan.name} ({frappe.utils.fmt_money(loan.outstanding_principal + loan.outstanding_interest)})"
		for loan in outstanding
	)
	frappe.throw(_(
		"{0} cannot be marked as Left while loans are outstanding: {1}. "
		"Recover the balance through final settlement or route it through Loan Write Off first."
	).format(doc.employee_name or doc.name, details))
