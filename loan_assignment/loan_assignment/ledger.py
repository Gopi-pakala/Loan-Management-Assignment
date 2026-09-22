from contextlib import contextmanager

import frappe
from frappe.utils import flt


@contextmanager
def _elevated():
	"""These postings are system-initiated consequences of a document
	transition (a Treasury release, a repayment, a write-off), not a
	direct user action against Payment Entry/Journal Entry. Some of
	ERPNext's own internal helpers (e.g. get_account_details) call
	frappe.has_permission() directly, which neither respects a document's
	own `flags.ignore_permissions` nor `frappe.flags.ignore_permissions` —
	it only ever checks the acting session user's real role permissions.
	The reliable way to run a system-initiated posting regardless of the
	current user's own Payment Entry/Journal Entry access is to actually
	post it as Administrator, the same way a background job would.
	"""
	previous_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(previous_user)


def get_bank_gl_account(bank_account):
	account = frappe.db.get_value("Bank Account", bank_account, "account")
	if not account:
		frappe.throw(frappe._("Bank Account {0} has no linked GL Account").format(bank_account))
	return account


def create_disbursement_payment_entry(disbursement, loan):
	"""Dr Employee Loan Receivable / Cr Bank — money released to the employee."""
	pe = frappe.new_doc("Payment Entry")
	pe.update({
		"payment_type": "Pay",
		"company": loan.company,
		"posting_date": disbursement.disbursement_date,
		"party_type": "Employee",
		"party": disbursement.employee,
		"paid_from": get_bank_gl_account(disbursement.bank_account),
		"paid_to": loan.loan_account,
		"paid_amount": flt(disbursement.amount),
		"received_amount": flt(disbursement.amount),
		"reference_no": disbursement.reference_no or disbursement.name,
		"reference_date": disbursement.reference_date or disbursement.disbursement_date,
		"mode_of_payment": disbursement.mode_of_payment,
		"remarks": f"Loan disbursement {disbursement.name} against {loan.name} (tranche {disbursement.tranche_no})",
	})
	pe.flags.ignore_permissions = True
	with _elevated():
		pe.insert()
		pe.submit()
	return pe.name


def cancel_payment_entry(payment_entry):
	if not payment_entry:
		return
	pe = frappe.get_doc("Payment Entry", payment_entry)
	if pe.docstatus == 1:
		pe.flags.ignore_permissions = True
		with _elevated():
			pe.cancel()


def create_repayment_payment_entry(repayment, loan):
	"""Dr Bank / Cr Employee Loan Receivable — money received from the employee."""
	pe = frappe.new_doc("Payment Entry")
	pe.update({
		"payment_type": "Receive",
		"company": loan.company,
		"posting_date": repayment.repayment_date,
		"party_type": "Employee",
		"party": repayment.employee,
		"paid_from": loan.loan_account,
		"paid_to": get_bank_gl_account(repayment.bank_account) if repayment.get("bank_account") else _default_bank_account(loan.company),
		"paid_amount": flt(repayment.amount),
		"received_amount": flt(repayment.amount),
		"reference_no": repayment.reference_no or repayment.name,
		"reference_date": repayment.repayment_date,
		"mode_of_payment": repayment.mode_of_payment,
		"remarks": f"Loan repayment {repayment.name} against {loan.name} ({repayment.repayment_type})",
	})
	pe.flags.ignore_permissions = True
	with _elevated():
		pe.insert()
		pe.submit()
	return pe.name


def _default_bank_account(company):
	account = frappe.db.get_value("Company", company, "default_bank_account")
	if account:
		return account
	frappe.throw(frappe._("No default bank account configured for {0}").format(company))


def create_writeoff_journal_entry(write_off, loan):
	"""Dr Staff Loan Written Off / Cr Employee Loan Receivable for the
	residual (principal + interest together) — matches the brief's
	Expected Postings table exactly.
	"""
	amount = flt(write_off.principal_written_off) + flt(write_off.interest_written_off)
	je = frappe.new_doc("Journal Entry")
	je.update({
		"voucher_type": "Journal Entry",
		"company": loan.company,
		"posting_date": write_off.write_off_date,
		"user_remark": f"Write-off of loan {loan.name} per {write_off.name}",
		"accounts": [
			{
				"account": loan.writeoff_account,
				"debit_in_account_currency": amount,
				"party_type": "Employee",
				"party": write_off.employee,
			},
			{
				"account": loan.loan_account,
				"credit_in_account_currency": amount,
				"party_type": "Employee",
				"party": write_off.employee,
			},
		],
	})
	je.flags.ignore_permissions = True
	with _elevated():
		je.insert()
		je.submit()
	return je.name
