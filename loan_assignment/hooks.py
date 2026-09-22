app_name = "loan_assignment"
app_title = "Loan Assignment"
app_publisher = "Gopinadh"
app_description = "Employee Loan Lifecycle module for ERPNext/HRMS"
app_email = "gopinadh.p@techbulls.co.in"
app_license = "mit"

required_apps = ["erpnext", "hrms"]

# Fixtures
# --------
# Roles, the custom field on Salary Slip, and the Salary Component used for
# the payroll deduction. All idempotent on `bench migrate`.
fixtures = [
	{"dt": "Role", "filters": [["name", "in", [
		"Reporting Manager", "HR Manager", "Finance Manager", "Treasury Officer", "CFO",
	]]]},
	{"dt": "Custom Field", "filters": [["name", "in", [
		"Salary Slip-loan_recoveries_section",
		"Salary Slip-loan_recoveries",
	]]]},
	{"dt": "Salary Component", "filters": [["name", "=", "Loan Recovery"]]},
	{"dt": "Workflow", "filters": [["name", "in", [
		"Loan Application Workflow",
		"Loan Disbursement Workflow",
		"Loan Restructure Workflow",
		"Loan Write Off Workflow",
	]]]},
]

# Document Events
# ----------------
doc_events = {
	"Employee": {
		"before_save": "loan_assignment.loan_assignment.events.employee.block_left_with_outstanding_loans",
	},
	"Salary Slip": {
		"on_cancel": "loan_assignment.loan_assignment.events.salary_slip.on_cancel",
	},
	"Loan Type": {
		"on_update": "loan_assignment.loan_assignment.events.loan_type.sync_salary_component_account",
	},
}

# Overriding Doctype Class
# ------------------------
override_doctype_class = {
	"Salary Slip": "loan_assignment.loan_assignment.overrides.salary_slip.LoanAssignmentSalarySlip",
}

# Permissions
# -----------
permission_query_conditions = {
	"Loan Application": "loan_assignment.loan_assignment.permissions.loan_application.get_permission_query_conditions",
	"Loan": "loan_assignment.loan_assignment.permissions.loan.get_permission_query_conditions",
	"Loan Disbursement": "loan_assignment.loan_assignment.permissions.loan_disbursement.get_permission_query_conditions",
	"Loan Repayment": "loan_assignment.loan_assignment.permissions.loan_repayment.get_permission_query_conditions",
}

has_permission = {
	"Loan Application": "loan_assignment.loan_assignment.permissions.loan_application.has_permission",
	"Loan": "loan_assignment.loan_assignment.permissions.loan.has_permission",
	"Loan Disbursement": "loan_assignment.loan_assignment.permissions.loan_disbursement.has_permission",
	"Loan Repayment": "loan_assignment.loan_assignment.permissions.loan_repayment.has_permission",
}

# Scheduled Tasks
# ---------------
scheduler_events = {
	"monthly": [
		"loan_assignment.loan_assignment.tasks.accrue_monthly_interest",
	],
	"daily": [
		"loan_assignment.loan_assignment.tasks.update_arrears_ageing",
	],
}

# Testing
# -------
before_tests = "loan_assignment.loan_assignment.setup.before_tests"
