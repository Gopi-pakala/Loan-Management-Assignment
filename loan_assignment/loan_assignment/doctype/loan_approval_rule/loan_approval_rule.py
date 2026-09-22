import frappe
from frappe import _
from frappe.model.document import Document


class LoanApprovalRule(Document):
	def validate(self):
		self.validate_amount_band()
		self.validate_sequence()

	def validate_amount_band(self):
		if flt_gt(self.min_amount, self.max_amount):
			frappe.throw(_("Min Amount cannot be greater than Max Amount"))

	def validate_sequence(self):
		sequences = [row.sequence for row in self.approvers]
		if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
			frappe.throw(_("Approver sequence numbers must be unique and strictly increasing (1, 2, 3 ...)"))
		if sequences and sequences[0] != 1:
			frappe.throw(_("Approver sequence must start at 1"))


def flt_gt(a, b):
	return frappe.utils.flt(a) > frappe.utils.flt(b)


def get_matching_rule(company, loan_type, employee_grade, amount):
	"""Resolve the single most-specific Loan Approval Rule for this
	company/loan_type/employee_grade/amount. Specificity = how many of
	(loan_type, employee_grade) are pinned rather than left blank; a
	blank field on a rule means "matches every value". Ties (two rules of
	equal specificity whose amount bands both cover `amount`) are a matrix
	configuration error and are surfaced as such rather than silently
	picking one, since the matrix is data an admin controls, not code.
	"""
	rules = frappe.get_all(
		"Loan Approval Rule",
		filters={
			"company": company,
			"min_amount": ["<=", amount],
			"max_amount": [">=", amount],
		},
		fields=["name", "loan_type", "employee_grade"],
	)
	candidates = [
		r for r in rules
		if (not r.loan_type or r.loan_type == loan_type)
		and (not r.employee_grade or r.employee_grade == employee_grade)
	]
	if not candidates:
		frappe.throw(_(
			"No approval rule matches company {0}, loan type {1}, grade {2} for amount {3}. "
			"Ask the approval-matrix owner to add one."
		).format(company, loan_type, employee_grade, amount))

	for row in candidates:
		row["specificity"] = (1 if row.loan_type else 0) + (1 if row.employee_grade else 0)
	max_specificity = max(row["specificity"] for row in candidates)
	best = [row for row in candidates if row["specificity"] == max_specificity]

	if len(best) > 1:
		frappe.throw(_(
			"Approval matrix is ambiguous for this application: rules {0} are equally specific "
			"and overlap on amount {1}. Fix the Loan Approval Rule matrix."
		).format(", ".join(r.name for r in best), amount))

	return best[0].name
