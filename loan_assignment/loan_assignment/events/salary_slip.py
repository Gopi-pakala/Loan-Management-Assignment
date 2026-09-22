from loan_assignment.loan_assignment.payroll import reverse_recovery_for_slip


def on_cancel(doc, method=None):
	reverse_recovery_for_slip(doc.name)
