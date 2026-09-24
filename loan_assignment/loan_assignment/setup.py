import frappe


def ensure_workflow_states(names):
	for name in names:
		if not frappe.db.exists("Workflow State", name):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": name}).insert(
				ignore_permissions=True
			)


def ensure_workflow_actions(names):
	for name in names:
		if not frappe.db.exists("Workflow Action Master", name):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": name}).insert(
				ignore_permissions=True
			)


def create_workflow(workflow_name, document_type, states, transitions):
	"""`states`: list of (state, doc_status, allow_edit_role).
	`transitions`: list of (state, action, next_state, allowed_role, condition).
	Idempotent — safe to call on every `bench migrate`.
	"""
	if frappe.db.exists("Workflow", workflow_name):
		return

	ensure_workflow_states([s[0] for s in states])
	ensure_workflow_actions({t[1] for t in transitions})

	workflow = frappe.get_doc({
		"doctype": "Workflow",
		"workflow_name": workflow_name,
		"document_type": document_type,
		"is_active": 1,
		"workflow_state_field": "status",
		"states": [
			{"state": s, "doc_status": doc_status, "allow_edit": role}
			for s, doc_status, role in states
		],
		"transitions": [
			{"state": s, "action": a, "next_state": n, "allowed": r, "condition": c}
			for s, a, n, r, c in transitions
		],
	})
	workflow.flags.ignore_permissions = True
	workflow.insert()


def create_loan_disbursement_workflow():
	create_workflow(
		"Loan Disbursement Workflow",
		"Loan Disbursement",
		states=[
			("Draft", "0", "Finance Manager"),
			("Finance Verified", "0", "Treasury Officer"),
			("Treasury Released", "0", "Treasury Officer"),
			("Disbursed", "1", "System Manager"),
			("Cancelled", "2", "System Manager"),
		],
		transitions=[
			("Draft", "Verify", "Finance Verified", "Finance Manager", ""),
			("Finance Verified", "Release Funds", "Treasury Released", "Treasury Officer",
				"doc.verified_by != frappe.session.user or frappe.session.user == 'Administrator'"),
			("Treasury Released", "Confirm Disbursement", "Disbursed", "Treasury Officer", ""),
			("Disbursed", "Cancel", "Cancelled", "Finance Manager", ""),
		],
	)


# Every role this app ever configures as a Loan Approval Step's approver —
# keep in sync with the Role fixture list in hooks.py. Used to keep the
# Approve/Reject/Return buttons off a plain applicant's screen (see below).
APPROVER_ROLES = ["Reporting Manager", "HR Manager", "Finance Manager", "CFO"]


def create_loan_application_workflow():
	# The chain's actual per-record shape (how many steps, which role or
	# named user governs each one) is resolved dynamically from the Loan
	# Approval Rule matrix — Workflow transitions can't be parameterised per
	# record, so a single fixed `allowed` role per transition can never be
	# exactly "whichever role governs this specific record's current step".
	# The next best thing: list every role this app ever uses as an
	# approver, so the buttons show for someone who *could* be an approver
	# somewhere, but never for a plain applicant (who only holds "Employee").
	# The real per-record eligibility/self-approval/no-repeat checks still
	# run in LoanApplication._handle_workflow_transition regardless of who
	# can see the button — see DECISIONS.md.
	transitions = [
		# Draft -> Pending Approval covers the plain Submit button: once a
		# Workflow governs `status`, Frappe's own workflow engine checks
		# every change to that field against a defined transition — even one
		# set by before_submit() rather than clicked from a workflow button —
		# so this row has to exist or the very first Submit is rejected with
		# WorkflowPermissionError.
		("Draft", "Submit for Approval", "Pending Approval", "Employee", ""),
	]
	for role in APPROVER_ROLES:
		transitions.append(("Pending Approval", "Approve", "Approved", role, ""))
		transitions.append(("Pending Approval", "Reject", "Rejected", role, ""))
		transitions.append(("Pending Approval", "Return", "Returned", role, ""))
	transitions.append(
		# LoanApplication.before_update_after_submit() routes a re-saved
		# Returned application straight back to Pending Approval (never
		# through "Draft" — that state's doc_status=0 and this document's
		# docstatus never left 1, so Frappe's own Workflow validation would
		# refuse a submitted->draft transition row outright).
		("Returned", "Resubmit", "Pending Approval", "Employee", "")
	)

	create_workflow(
		"Loan Application Workflow",
		"Loan Application",
		states=[
			("Draft", "0", "Employee"),
			("Pending Approval", "1", "System Manager"),
			("Approved", "1", "System Manager"),
			("Rejected", "1", "System Manager"),
			("Returned", "1", "Employee"),
		],
		transitions=transitions,
	)


def create_workflows():
	create_loan_application_workflow()
	create_loan_disbursement_workflow()
	create_loan_restructure_workflow()
	create_loan_writeoff_workflow()


def create_loan_restructure_workflow():
	if not frappe.db.exists("DocType", "Loan Restructure Request"):
		return
	create_workflow(
		"Loan Restructure Workflow",
		"Loan Restructure Request",
		states=[
			("Draft", "0", "Employee"),
			("Pending HR", "0", "HR Manager"),
			("Pending Finance", "0", "Finance Manager"),
			("Applied", "1", "System Manager"),
			("Rejected", "0", "System Manager"),
		],
		transitions=[
			("Draft", "Submit for HR Review", "Pending HR", "HR Manager", ""),
			("Pending HR", "Endorse", "Pending Finance", "HR Manager", ""),
			("Pending HR", "Reject", "Rejected", "HR Manager", ""),
			("Pending Finance", "Apply", "Applied", "Finance Manager", ""),
			("Pending Finance", "Reject", "Rejected", "Finance Manager", ""),
		],
	)


def create_loan_writeoff_workflow():
	if not frappe.db.exists("DocType", "Loan Write Off"):
		return
	create_workflow(
		"Loan Write Off Workflow",
		"Loan Write Off",
		states=[
			("Draft", "0", "Finance Manager"),
			("Pending Finance", "0", "Finance Manager"),
			("Pending CFO", "0", "CFO"),
			("Written Off", "1", "System Manager"),
			("Rejected", "0", "System Manager"),
		],
		transitions=[
			("Draft", "Submit for Finance Review", "Pending Finance", "Finance Manager", ""),
			("Pending Finance", "Endorse", "Pending CFO", "Finance Manager", ""),
			("Pending Finance", "Reject", "Rejected", "Finance Manager", ""),
			("Pending CFO", "Approve", "Written Off", "CFO", ""),
			("Pending CFO", "Reject", "Rejected", "CFO", ""),
		],
	)


def before_tests():
	frappe.clear_cache()
	create_workflows()
	frappe.db.commit()
