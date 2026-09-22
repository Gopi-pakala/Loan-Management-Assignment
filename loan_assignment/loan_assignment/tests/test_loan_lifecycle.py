import frappe
from frappe.tests import IntegrationTestCase, set_user
from frappe.utils import add_months, today

from loan_assignment.loan_assignment.setup import create_workflows

TEST_COMPANY = "_Test Company"


class TestLoanLifecycle(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_workflows()
		cls.company = _ensure_company("Loan Test Co")
		cls.employee = _ensure_employee(cls.company)
		cls.loan_type = _ensure_loan_type(cls.company)
		_ensure_approval_rule(cls.company, cls.loan_type)

	def test_sanction_disbursement_and_repayment(self):
		loan = frappe.get_doc({
			"doctype": "Loan",
			"employee": self.employee,
			"company": self.company,
			"loan_type": self.loan_type,
			"interest_mode": "Reducing Balance",
			"rate_of_interest": 8,
			"tenure_months": 12,
			"sanctioned_amount": 120000,
			"first_deduction_month": add_months(today(), 1),
			"loan_account": frappe.db.get_value("Loan Type", self.loan_type, "loan_account"),
			"interest_account": frappe.db.get_value("Loan Type", self.loan_type, "interest_account"),
			"writeoff_account": frappe.db.get_value("Loan Type", self.loan_type, "writeoff_account"),
		})
		loan.insert()
		loan.submit()

		schedule = frappe.get_all(
			"Loan Repayment Schedule", filters={"loan": loan.name, "is_current": 1},
			fields=["principal_amount"], order_by="period_no",
		)
		self.assertEqual(len(schedule), 12)
		self.assertAlmostEqual(sum(r.principal_amount for r in schedule), 120000.0, places=2)

		bank_account = _ensure_bank_account(self.company)
		disbursement = frappe.get_doc({
			"doctype": "Loan Disbursement",
			"loan": loan.name,
			"disbursement_date": today(),
			"amount": 120000,
			"bank_account": bank_account,
			"status": "Draft",
		})
		disbursement.insert()

		finance_user = _ensure_user("finance@example.com", "Finance Manager")
		treasury_user = _ensure_user("treasury@example.com", "Treasury Officer")

		with set_user(finance_user):
			doc = frappe.get_doc("Loan Disbursement", disbursement.name)
			doc.status = "Finance Verified"
			doc.save(ignore_permissions=True)

		with set_user(treasury_user):
			doc = frappe.get_doc("Loan Disbursement", disbursement.name)
			doc.status = "Treasury Released"
			doc.save(ignore_permissions=True)
			doc.submit()

		loan.reload()
		self.assertEqual(loan.disbursed_amount, 120000)
		self.assertEqual(loan.status, "Disbursed")

	def test_same_user_cannot_verify_and_release(self):
		loan = self._make_sanctioned_loan()
		bank_account = _ensure_bank_account(self.company)
		disbursement = frappe.get_doc({
			"doctype": "Loan Disbursement",
			"loan": loan.name,
			"disbursement_date": today(),
			"amount": 50000,
			"bank_account": bank_account,
			"status": "Draft",
		})
		disbursement.insert()

		user = _ensure_user("both-roles@example.com", "Finance Manager", "Treasury Officer")
		with set_user(user):
			doc = frappe.get_doc("Loan Disbursement", disbursement.name)
			doc.status = "Finance Verified"
			doc.save(ignore_permissions=True)

			doc.status = "Treasury Released"
			with self.assertRaises(frappe.ValidationError):
				doc.save(ignore_permissions=True)

	def _make_sanctioned_loan(self):
		loan = frappe.get_doc({
			"doctype": "Loan",
			"employee": self.employee,
			"company": self.company,
			"loan_type": self.loan_type,
			"interest_mode": "Flat",
			"rate_of_interest": 8,
			"tenure_months": 6,
			"sanctioned_amount": 60000,
			"first_deduction_month": add_months(today(), 1),
			"loan_account": frappe.db.get_value("Loan Type", self.loan_type, "loan_account"),
			"interest_account": frappe.db.get_value("Loan Type", self.loan_type, "interest_account"),
			"writeoff_account": frappe.db.get_value("Loan Type", self.loan_type, "writeoff_account"),
		})
		loan.insert()
		loan.submit()
		return loan


class TestPayrollRecovery(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		create_workflows()
		cls.employee = _ensure_employee(TEST_COMPANY, name="Test Payroll Loan Employee")
		cls.loan_type = _ensure_loan_type(TEST_COMPANY, name="Payroll Test Loan")
		_ensure_approval_rule(TEST_COMPANY, cls.loan_type, code="B1-PAYROLL-TEST")
		_ensure_holiday_list(cls.employee, TEST_COMPANY)
		cls.salary_structure = _ensure_salary_structure(cls.employee)

	def test_deduction_appears_and_reverses_on_cancel(self):
		from hrms.payroll.doctype.salary_structure.salary_structure import make_salary_slip

		loan = frappe.get_doc({
			"doctype": "Loan",
			"employee": self.employee,
			"company": TEST_COMPANY,
			"loan_type": self.loan_type,
			"interest_mode": "Flat",
			"rate_of_interest": 12,
			"tenure_months": 10,
			"sanctioned_amount": 10000,
			"first_deduction_month": today(),
			"loan_account": frappe.db.get_value("Loan Type", self.loan_type, "loan_account"),
			"interest_account": frappe.db.get_value("Loan Type", self.loan_type, "interest_account"),
			"writeoff_account": frappe.db.get_value("Loan Type", self.loan_type, "writeoff_account"),
		})
		loan.insert()
		loan.submit()
		loan.db_set("disbursed_amount", 10000)
		loan.db_set("status", "Disbursed")

		first_period = frappe.get_all(
			"Loan Repayment Schedule", filters={"loan": loan.name, "is_current": 1},
			fields=["instalment_amount"], order_by="period_no", limit=1,
		)[0]

		slip = make_salary_slip(self.salary_structure, employee=self.employee)
		slip.insert()

		recoveries = [r for r in slip.loan_recoveries if r.loan == loan.name]
		self.assertEqual(len(recoveries), 1)
		self.assertAlmostEqual(recoveries[0].total_recovered, first_period.instalment_amount, places=2)
		deduction_rows = [d for d in slip.deductions if d.salary_component == "Loan Recovery"]
		self.assertEqual(len(deduction_rows), 1)
		self.assertAlmostEqual(deduction_rows[0].amount, first_period.instalment_amount, places=2)

		slip.submit()
		loan.reload()
		self.assertAlmostEqual(loan.principal_recovered + loan.interest_recovered, first_period.instalment_amount, places=2)

		slip.cancel()
		loan.reload()
		self.assertEqual(loan.principal_recovered, 0)
		self.assertEqual(loan.interest_recovered, 0)

		schedule_row = frappe.get_all(
			"Loan Repayment Schedule", filters={"loan": loan.name, "is_current": 1},
			fields=["status", "recovered_amount"], order_by="period_no", limit=1,
		)[0]
		self.assertEqual(schedule_row.status, "Pending")
		self.assertEqual(schedule_row.recovered_amount, 0)


def _ensure_holiday_list(employee, company):
	# v16 resolves an employee's effective holiday list via a submitted
	# "Holiday List Assignment" record (hrms.utils.holiday_list), not the
	# plain Employee.holiday_list field used in earlier versions.
	name = "Loan Assignment Test Holiday List"
	if not frappe.db.exists("Holiday List", name):
		frappe.get_doc({
			"doctype": "Holiday List", "holiday_list_name": name,
			"from_date": add_months(today(), -12), "to_date": add_months(today(), 12),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Holiday List Assignment", {"assigned_to": company, "docstatus": 1}):
		frappe.get_doc({
			"doctype": "Holiday List Assignment", "applicable_for": "Company", "assigned_to": company,
			"holiday_list": name, "from_date": add_months(today(), -12),
		}).insert(ignore_permissions=True).submit()


def _ensure_salary_structure(employee):
	# Hand-built rather than via HRMS's test_salary_structure helpers:
	# those cascade into ERPNext's BootStrapTestData, which hits the same
	# multi-currency Price List collision noted in DECISIONS.md — unrelated
	# to this app's own logic, and a plain doctype insert sidesteps it.
	name = "Loan Assignment Test Salary Structure"

	if not frappe.db.exists("Salary Component", "Basic Test"):
		frappe.get_doc({
			"doctype": "Salary Component", "salary_component": "Basic Test", "type": "Earning",
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Salary Structure", name):
		frappe.get_doc({
			"doctype": "Salary Structure", "name": name, "company": TEST_COMPANY,
			"is_active": "Yes", "currency": "INR", "payroll_frequency": "Monthly",
			"earnings": [{"salary_component": "Basic Test", "amount": 30000}],
			"deductions": [],
		}).insert(ignore_permissions=True).submit()

	if not frappe.db.exists("Salary Structure Assignment", {"employee": employee, "docstatus": 1}):
		frappe.get_doc({
			"doctype": "Salary Structure Assignment", "employee": employee,
			"salary_structure": name, "company": TEST_COMPANY, "currency": "INR",
			"from_date": add_months(today(), -6), "base": 30000,
		}).insert(ignore_permissions=True).submit()

	return name


def _ensure_company(name):
	# Reuse whatever company already exists (e.g. from before_tests' setup
	# wizard) rather than creating a second one — ERPNext's Company
	# controller bootstraps global-name records (default Price Lists) on
	# creation that aren't re-check-before-insert, so a second company
	# collides with the first one's.
	existing = frappe.db.get_value("Company", {}, "name")
	if existing:
		return existing
	company = frappe.get_doc({
		"doctype": "Company", "company_name": name, "default_currency": "INR", "country": "India",
	}).insert(ignore_permissions=True)
	return company.name


def _ensure_bank_account(company):
	abbr = frappe.get_cached_value("Company", company, "abbr")
	bank_gl = frappe.db.get_value("Account", {"company": company, "account_name": "Bank Account"})
	if not bank_gl:
		bank_gl = frappe.get_doc({
			"doctype": "Account", "account_name": "Bank Account", "company": company,
			"parent_account": frappe.db.get_value("Account", {"company": company, "is_group": 1, "account_type": "Bank"}),
			"account_type": "Bank",
		}).insert(ignore_permissions=True).name
	if not frappe.db.exists("Bank", "Test Bank"):
		frappe.get_doc({"doctype": "Bank", "bank_name": "Test Bank"}).insert(ignore_permissions=True)

	if not frappe.db.exists("Bank Account", {"company": company}):
		return frappe.get_doc({
			"doctype": "Bank Account", "account_name": f"Test Bank - {abbr}", "company": company,
			"account": bank_gl, "bank": "Test Bank",
		}).insert(ignore_permissions=True).name
	return frappe.db.get_value("Bank Account", {"company": company})


def _ensure_employee(company, name="Test Loan Employee"):
	existing = frappe.db.get_value("Employee", {"company": company, "employee_name": name})
	if existing:
		return existing
	employee = frappe.get_doc({
		"doctype": "Employee", "employee_name": name, "first_name": name,
		"company": company, "date_of_joining": add_months(today(), -36),
		"date_of_birth": add_months(today(), -360), "gender": "Other", "status": "Active",
	}).insert(ignore_permissions=True)
	return employee.name


def _ensure_loan_type(company, name="Personal Test"):
	if frappe.db.exists("Loan Type", name):
		return name
	receivable_parent = frappe.db.get_value("Account", {"company": company, "root_type": "Asset", "is_group": 1})
	income_parent = frappe.db.get_value("Account", {"company": company, "root_type": "Income", "is_group": 1})
	expense_parent = frappe.db.get_value("Account", {"company": company, "root_type": "Expense", "is_group": 1})

	loan_account = frappe.get_doc({
		"doctype": "Account", "account_name": f"{name} Receivable", "company": company,
		"parent_account": receivable_parent, "account_type": "Receivable",
	}).insert(ignore_permissions=True).name
	interest_account = frappe.get_doc({
		"doctype": "Account", "account_name": f"{name} Interest Income", "company": company,
		"parent_account": income_parent,
	}).insert(ignore_permissions=True).name
	writeoff_account = frappe.get_doc({
		"doctype": "Account", "account_name": f"{name} Written Off", "company": company,
		"parent_account": expense_parent,
	}).insert(ignore_permissions=True).name

	return frappe.get_doc({
		"doctype": "Loan Type", "loan_type_name": name, "company": company,
		"interest_mode": "Reducing Balance", "rate_of_interest": 8, "max_tenure_months": 60,
		"max_multiple_of_gross": 10, "min_service_months": 0, "max_active_loans": 5,
		"loan_account": loan_account, "interest_account": interest_account, "writeoff_account": writeoff_account,
	}).insert(ignore_permissions=True).name


def _ensure_approval_rule(company, loan_type, code="B1-TEST"):
	if frappe.db.exists("Loan Approval Rule", code):
		return
	frappe.get_doc({
		"doctype": "Loan Approval Rule", "rule_code": code, "company": company,
		"min_amount": 0, "max_amount": 99999999,
		"approvers": [{"sequence": 1, "approver_role": "HR Manager"}],
	}).insert(ignore_permissions=True)


def _ensure_user(email, *roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc({
			"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0,
		}).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	for role in roles:
		if role not in [r.role for r in user.roles]:
			user.append("roles", {"role": role})
	user.save(ignore_permissions=True)
	return email
