import frappe


def sync_salary_component_account(doc, method=None):
	"""Keeps the "Loan Recovery" Salary Component's per-company account
	mapping pointed at this company's Employee Loan Receivable account, so
	HRMS's own payroll accounting (Payroll Entry / Salary Slip accrual JE)
	posts the recovery credit to the receivable automatically — no custom
	ledger code needed for the payroll channel.
	"""
	if not doc.company or not doc.loan_account:
		return

	component = frappe.get_doc("Salary Component", "Loan Recovery")
	for row in component.accounts:
		if row.company == doc.company:
			if row.account != doc.loan_account:
				row.account = doc.loan_account
				component.flags.ignore_permissions = True
				component.save()
			return

	component.append("accounts", {"company": doc.company, "account": doc.loan_account})
	component.flags.ignore_permissions = True
	component.save()
