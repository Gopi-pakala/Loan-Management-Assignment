import frappe
from frappe.tests import IntegrationTestCase, set_user
from frappe.utils import add_months, flt, today

from loan_assignment.loan_assignment.payroll import recover_against_available_funds
from loan_assignment.loan_assignment.repayment import compute_foreclosure_payoff
from loan_assignment.loan_assignment.setup import create_workflows
from loan_assignment.loan_assignment.tests.test_loan_lifecycle import (
	_ensure_approval_rule,
	_ensure_bank_account,
	_ensure_employee,
	_ensure_loan_type,
	_ensure_user,
)

TEST_COMPANY = "_Test Company"


class TestMoneyPaths(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_workflows()
		cls.employee = _ensure_employee(TEST_COMPANY, name="Test Money Path Employee")
		cls.loan_type = _ensure_loan_type(TEST_COMPANY, name="Money Path Test Loan")
		_ensure_approval_rule(TEST_COMPANY, cls.loan_type, code="B1-MONEYPATH-TEST")
		cls.bank_account = _ensure_bank_account(TEST_COMPANY)
		cls.finance_user = _ensure_user("mp-finance@example.com", "Finance Manager")
		cls.treasury_user = _ensure_user("mp-treasury@example.com", "Treasury Officer")
		cls.hr_user = _ensure_user("mp-hr@example.com", "HR Manager")
		cls.cfo_user = _ensure_user("mp-cfo@example.com", "CFO")

	def _make_disbursed_loan(self, sanctioned_amount, tenure_months=12, months_back=0, interest_mode="Reducing Balance"):
		loan = frappe.get_doc({
			"doctype": "Loan",
			"employee": self.employee,
			"company": TEST_COMPANY,
			"loan_type": self.loan_type,
			"interest_mode": interest_mode,
			"rate_of_interest": 8,
			"tenure_months": tenure_months,
			"sanctioned_amount": sanctioned_amount,
			"first_deduction_month": add_months(today(), -months_back),
			"loan_account": frappe.db.get_value("Loan Type", self.loan_type, "loan_account"),
			"interest_account": frappe.db.get_value("Loan Type", self.loan_type, "interest_account"),
			"writeoff_account": frappe.db.get_value("Loan Type", self.loan_type, "writeoff_account"),
		})
		loan.insert()
		loan.submit()

		disbursement = frappe.get_doc({
			"doctype": "Loan Disbursement",
			"loan": loan.name,
			"disbursement_date": today(),
			"amount": sanctioned_amount,
			"bank_account": self.bank_account,
			"status": "Draft",
		})
		disbursement.insert()

		with set_user(self.finance_user):
			doc = frappe.get_doc("Loan Disbursement", disbursement.name)
			doc.status = "Finance Verified"
			doc.save(ignore_permissions=True)

		with set_user(self.treasury_user):
			doc = frappe.get_doc("Loan Disbursement", disbursement.name)
			doc.status = "Treasury Released"
			doc.save(ignore_permissions=True)
			doc.submit()

		loan.reload()
		return loan

	def test_interest_accrual_posts_and_reverses(self):
		"""Regression test for the party-type bug: `interest_account` here
		has no `account_type` (Income, not Receivable/Payable) — a party on
		that leg makes ERPNext's GL Entry reject the JE outright. This must
		actually submit against a real chart of accounts, not a synthetic
		one, to mean anything.
		"""
		loan = self._make_disbursed_loan(20000, months_back=1)
		period = frappe.get_all(
			"Loan Repayment Schedule", filters={"loan": loan.name, "is_current": 1, "period_no": 1},
			fields=["due_date", "interest_amount"], limit=1,
		)[0]

		accrual = frappe.get_doc({
			"doctype": "Loan Interest Accrual",
			"loan": loan.name,
			"period_start": add_months(period.due_date, -1),
			"period_end": period.due_date,
			"interest_amount": period.interest_amount,
		})
		accrual.insert()
		accrual.submit()

		self.assertTrue(accrual.journal_entry)
		je = frappe.get_doc("Journal Entry", accrual.journal_entry)
		self.assertEqual(je.docstatus, 1)
		debit_accounts = {row.account: row.debit for row in je.accounts if row.debit}
		credit_accounts = {row.account: row.credit for row in je.accounts if row.credit}
		self.assertAlmostEqual(debit_accounts.get(loan.loan_account, 0), period.interest_amount, places=2)
		self.assertAlmostEqual(credit_accounts.get(loan.interest_account, 0), period.interest_amount, places=2)

		accrual.cancel()
		je.reload()
		self.assertEqual(je.docstatus, 2)

	def test_write_off_through_workflow_posts_journal_entry(self):
		loan = self._make_disbursed_loan(15000, months_back=0)
		# principal_written_off/interest_written_off are read-only, taken
		# from the loan's actual outstanding at write-off time — not
		# whatever's passed in, so assert against the loan, not a guess.
		expected_write_off = flt(loan.outstanding_principal) + flt(loan.outstanding_interest)

		write_off = frappe.get_doc({
			"doctype": "Loan Write Off",
			"loan": loan.name,
			"employee": self.employee,
			"write_off_date": today(),
			"recovered_from_settlement": 0,
			"reason": "test: employee exit, unrecoverable residual",
			"status": "Draft",
		})
		write_off.insert()

		with set_user(self.finance_user):
			doc = frappe.get_doc("Loan Write Off", write_off.name)
			doc.status = "Pending Finance"
			doc.save(ignore_permissions=True)
			doc.status = "Pending CFO"
			doc.save(ignore_permissions=True)

		with set_user(self.cfo_user):
			doc = frappe.get_doc("Loan Write Off", write_off.name)
			doc.submit()

		self.assertTrue(doc.journal_entry)
		je = frappe.get_doc("Journal Entry", doc.journal_entry)
		self.assertEqual(je.docstatus, 1)
		credit_accounts = {row.account: row.credit for row in je.accounts if row.credit}
		self.assertAlmostEqual(credit_accounts.get(loan.loan_account, 0), expected_write_off, places=2)

		loan.reload()
		self.assertEqual(loan.status, "Written Off")

	def test_restructure_regenerates_only_from_effective_date_forward(self):
		loan = self._make_disbursed_loan(60000, tenure_months=6, months_back=2)

		old_rows = frappe.get_all(
			"Loan Repayment Schedule", filters={"loan": loan.name, "is_current": 1},
			fields=["name", "period_no"], order_by="period_no",
		)
		self.assertEqual(len(old_rows), 6)
		# Recovered-period boundary: with 2 months of history, periods 1-2
		# are due; leave them alone and restructure from period 3.
		effective_from = frappe.get_all(
			"Loan Repayment Schedule", filters={"loan": loan.name, "is_current": 1, "period_no": 3},
			fields=["due_date"],
		)[0].due_date

		restructure = frappe.get_doc({
			"doctype": "Loan Restructure Request",
			"loan": loan.name,
			"request_type": "Re-tenure",
			"effective_from": effective_from,
			"new_tenure_months": 8,
			"reason": "test: re-spread remaining balance",
			"status": "Draft",
		})
		restructure.insert()

		with set_user(self.hr_user):
			doc = frappe.get_doc("Loan Restructure Request", restructure.name)
			doc.status = "Pending HR"
			doc.save(ignore_permissions=True)
			doc.status = "Pending Finance"
			doc.save(ignore_permissions=True)

		with set_user(self.finance_user):
			doc = frappe.get_doc("Loan Restructure Request", restructure.name)
			doc.submit()

		# Old rows for periods 1-2 (already due) must still be `is_current=1`,
		# untouched; periods 3+ must have been superseded (`is_current=0`,
		# kept for the audit trail) by a fresh, longer tail.
		for row in old_rows[:2]:
			self.assertEqual(frappe.db.get_value("Loan Repayment Schedule", row.name, "is_current"), 1)
		for row in old_rows[2:]:
			self.assertEqual(frappe.db.get_value("Loan Repayment Schedule", row.name, "is_current"), 0)

		new_current = frappe.get_all(
			"Loan Repayment Schedule", filters={"loan": loan.name, "is_current": 1},
			fields=["principal_amount"],
		)
		# 2 untouched due periods + a new, longer re-spread tail.
		self.assertGreater(len(new_current), 6)
		loan.reload()
		self.assertAlmostEqual(
			sum(r.principal_amount for r in new_current), loan.sanctioned_amount, places=2
		)

	def test_direct_repayment_allocates_interest_before_principal(self):
		loan = self._make_disbursed_loan(24000, tenure_months=12, months_back=1)
		period = frappe.get_all(
			"Loan Repayment Schedule", filters={"loan": loan.name, "is_current": 1, "period_no": 1},
			fields=["interest_amount", "principal_amount"], limit=1,
		)[0]
		partial = flt(period.interest_amount) / 2

		repayment = frappe.get_doc({
			"doctype": "Loan Repayment",
			"loan": loan.name,
			"repayment_date": today(),
			"repayment_type": "Regular",
			"amount": partial,
			"bank_account": self.bank_account,
		})
		repayment.insert()
		repayment.submit()

		self.assertAlmostEqual(repayment.allocated_interest, partial, places=2)
		self.assertAlmostEqual(repayment.allocated_principal, 0, places=2)

	def test_foreclosure_payoff_excludes_unaccrued_future_interest(self):
		loan = self._make_disbursed_loan(50000, tenure_months=12, months_back=2)
		payoff = compute_foreclosure_payoff(loan, today())

		self.assertAlmostEqual(payoff.outstanding_principal, 50000, places=2)
		self.assertGreater(payoff.interest_accrued_to_date, 0)
		self.assertGreater(payoff.rebate_on_unaccrued_interest, 0)
		# The payoff total must never include the future periods' interest
		# it rebates — only principal + interest actually due + any charge.
		self.assertAlmostEqual(
			payoff.total_payoff,
			payoff.outstanding_principal + payoff.interest_accrued_to_date + payoff.pre_closure_charge,
			places=2,
		)

		foreclosure = frappe.get_doc({
			"doctype": "Loan Repayment",
			"loan": loan.name,
			"repayment_date": today(),
			"repayment_type": "Foreclosure",
			"amount": payoff.total_payoff,
			"bank_account": self.bank_account,
		})
		foreclosure.insert()
		foreclosure.submit()

		loan.reload()
		self.assertEqual(loan.status, "Closed")
		self.assertEqual(loan.outstanding_principal, 0)
		self.assertEqual(loan.outstanding_interest, 0)

		still_open = frappe.db.count(
			"Loan Repayment Schedule",
			{"loan": loan.name, "is_current": 1, "status": ["in", ["Pending", "Partially Recovered"]]},
		)
		self.assertEqual(still_open, 0)

		foreclosure.cancel()
		loan.reload()
		self.assertEqual(loan.status, "Disbursed")
		self.assertGreater(loan.outstanding_principal, 0)

	def test_retrospective_correction_posts_catchup_journal_entry(self):
		"""Reproduces the brief's own scenario directly against
		`payroll.recover_against_available_funds` (which only reads
		employee/company/end_date off its `salary_slip` argument — a real
		Salary Slip isn't needed to exercise this): period 1 and period 2
		both get recovered in order (normal payroll), then period 1's
		"slip" is cancelled and resubmitted with different figures — after
		period 2 is already `Recovered` — which must trigger the
		retrospective catch-up adjustment rather than silently leaving
		period 2's now-stale recognised interest untouched.
		"""
		loan = self._make_disbursed_loan(60000, tenure_months=12, months_back=2)
		periods = frappe.get_all(
			"Loan Repayment Schedule", filters={"loan": loan.name, "is_current": 1, "period_no": ["in", [1, 2]]},
			fields=["name", "period_no", "due_date", "instalment_amount"], order_by="period_no asc",
		)
		period1, period2 = periods[0], periods[1]

		slip1 = frappe._dict(employee=self.employee, company=TEST_COMPANY, end_date=period1.due_date)
		recover_against_available_funds(slip1, period1.instalment_amount)
		slip2 = frappe._dict(employee=self.employee, company=TEST_COMPANY, end_date=period2.due_date)
		recover_against_available_funds(slip2, period2.instalment_amount)

		self.assertEqual(frappe.db.get_value("Loan Repayment Schedule", period1.name, "status"), "Recovered")
		self.assertEqual(frappe.db.get_value("Loan Repayment Schedule", period2.name, "status"), "Recovered")

		je_count_before = frappe.db.count("Journal Entry", {"voucher_type": "Journal Entry"})

		# Simulate "cancel the period-1 slip": revert it to Pending, as
		# `payroll.reverse_recovery_for_slip` would.
		frappe.db.set_value("Loan Repayment Schedule", period1.name, {
			"recovered_amount": 0, "recovered_principal": 0, "recovered_interest": 0, "status": "Pending",
		})
		# Resubmit with different figures: a corrected slip recovers a
		# different amount (e.g. it now accounts for unpaid leave that
		# period). Period 2 is untouched and still `Recovered`, so this is
		# exactly the retrospective scenario.
		corrected_amount = flt(period1.instalment_amount) * 0.8
		recover_against_available_funds(slip1, corrected_amount)

		je_count_after = frappe.db.count("Journal Entry", {"voucher_type": "Journal Entry"})
		self.assertGreater(je_count_after, je_count_before, "Expected a retrospective catch-up Journal Entry to post.")

		catchup_je = frappe.get_all(
			"Journal Entry", filters={"user_remark": ["like", f"%Retrospective correction catch-up for {loan.name}%"]},
			fields=["name", "docstatus"], limit=1,
		)
		self.assertTrue(catchup_je, "Expected a Journal Entry with the retrospective-correction remark.")
		self.assertEqual(catchup_je[0].docstatus, 1)
