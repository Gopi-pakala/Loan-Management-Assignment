# DECISIONS.md — Employee Loan Lifecycle (`loan_assignment`)

## Environment: v15 host, v16 build

The provided-bench assumption in the brief (Frappe v16, Python 3.14, Node 24, pre-seeded
Docker) didn't match the machine this was built on, which runs a v15 bench with other
unrelated apps installed (including ERPNext's `lending` app — explicitly out of scope here).
Rather than upgrading that shared bench in place (real risk of breaking `lending`, `loans`,
`print_designer` and friends, and no reliable way back), a **fully separate v16 stack** was
built alongside it: `frappe`/`erpnext`/`hrms` on their real `version-16` branches, Python 3.14
via `uv`, Node 24 via `nvm`, and an isolated, user-owned MariaDB instance (the shared bench's
MariaDB root password wasn't available and there was no sudo access to reset it). This app was
developed and tested only against that real v16 stack — never against v15.

Two verified, source-confirmed v16 behaviour changes shaped the code directly:
- `override_doctype_class` now hard-enforces that the override literally subclasses the
  original controller (`frappe/model/base_document.py`) — `LoanAssignmentSalarySlip` subclasses
  `hrms...SalarySlip` directly, same as v15 practice, just now checked.
- `has_permission` hooks can only *narrow* access (return exactly `True`/`False`; a falsy
  return denies, a truthy one falls through to the standard grant) and
  `permission_query_conditions` must return `"1=0"` to deny-all — an empty/`None`/`False`
  return is treated as *no restriction*, which would silently widen access. Every permission
  hook in `loan_assignment/permissions/` follows this explicitly.
- Document hooks (`validate`, `on_submit`, `on_cancel`, ...) can no longer commit their own
  transaction (transaction control is disabled for the duration of the hook) — nothing in this
  app calls `frappe.db.commit()` from inside a hook; all ledger postings (`Payment Entry`,
  `Journal Entry`) are inserted/submitted inline and ride the same request transaction.
- Test files use `frappe.tests.IntegrationTestCase` / `UnitTestCase`, not the deprecated
  `FrappeTestCase`.

## 1. Workflow engine vs. custom approval code — used both, for different reasons

**Loan Application** (the matrix-driven chain) is a custom engine, not a Frappe Workflow,
because the chain is genuinely dynamic per record: `Loan Approval Rule` names a specific
*person* on some rows (not just a role), and the "Reporting Manager"/"HR Manager" role steps
must resolve to *this employee's actual manager* / *this department's HR manager*, not "anyone
holding that role company-wide". Frappe Workflow transitions gate on a role held by the acting
user against the whole document type — they cannot express "must equal `doc.reports_to`'s
user" or a named-user override. That resolution logic (`approval.py`) has to be code either way.

**Loan Disbursement, Loan Restructure Request and Loan Write Off** *are* real Frappe Workflows
(`setup.py::create_workflows`, run idempotently as a post-model-sync patch), reusing each
doctype's own `status` Select field as `workflow_state_field` rather than adding a redundant
`workflow_state` column. These are fixed, role-gated linear chains with no per-record
branching — exactly what the Workflow engine is for. The one thing a Workflow transition
can't cleanly guarantee — "the Treasury user releasing funds must differ from the Finance
user who verified" — is expressed twice: as a Workflow Transition `condition`
(`doc.verified_by != frappe.session.user`, which the framework documents as a supported
context) so the action is hidden/blocked through the normal UI, *and* as a hard check in
`LoanDisbursement.validate()`/`before_submit()`, because the brief is explicit that a
whitelisted/API path must not be able to route around it — the workflow condition guards the
UI, the controller guards the API.

## 1b. The doctype name "Loan" is not actually free, even with `lending` uninstalled

Discovered while running the test suite: HRMS registers a `doc_events` hook —
`hrms.hr.utils.validate_loan_repay_from_salary` — keyed to the doctype **name** `"Loan"` in
its own `hooks.py`, for the separate `lending` app's `Loan` schema (`applicant_type`,
`repay_from_salary`, ...). Frappe's hook dispatch matches purely by doctype name across every
installed app, so this hook fires against *this app's* `Loan` documents too, unconditionally —
not gated on whether `lending` is installed — and immediately throws `AttributeError` since
this schema has neither field. Renaming the doctype was the obvious fix but would drift from
the name Section 05 fixes; modifying HRMS is against the ground rules. Instead, `Loan` carries
three inert, hidden, always-falsy fields — `applicant_type` (Data), `repay_from_salary`
(Check) and `is_term_loan` (Check), all defaulting to blank/0 — for no reason other than making
that hook's two independent conditions both evaluate to `False` and no-op, which is exactly
what the plain "no lending app" behaviour should be. It's a three-field compatibility shim on
this app's own schema, not a workaround touching HRMS.

## 2. Salary Slip: `override_doctype_class`, not a doc_event

The loan deduction needs to change `net_pay` itself (add a component, then re-derive totals),
not just react after the fact — a `doc_events` hook fires around an already-computed document
and can't feed a new deduction back into HRMS's own `set_net_pay()` cleanly. `override_doctype_class`
lets `LoanAssignmentSalarySlip.validate()` call `super().validate()` for the normal computation,
then strip/recompute the loan deduction and call `set_net_pay()` again — one coherent pass.

HRMS's core `Salary Slip` already calls `set_loan_repayment()` inside `calculate_net_pay()`,
but that function is decorated `@if_lending_app_installed` and is a genuine no-op unless the
separate `lending` app is installed — which it deliberately isn't here — so there's no
collision with this app's own `Loan` doctype despite the name being reused.

The payroll-side GL posting deliberately does **not** hand-roll a Journal Entry. Instead, the
"Loan Recovery" Salary Component's per-company account mapping is kept pointed at each
company's Employee Loan Receivable account automatically (`events/loan_type.py`, triggered off
`Loan Type.on_update`), so HRMS's own, already-built payroll accounting (Payroll Entry / Salary
Slip accrual JE) credits the receivable for whatever is recovered — no custom ledger code for
that channel. Disbursement, direct repayment and write-off *do* create documents explicitly
(`ledger.py`), because there's no equivalent existing document-generation path for those.

## 3. Schedule integrity across tranches, cancellations and adjustments

`Loan Repayment Schedule` is a standalone doctype (not a child table of `Loan`) with
`schedule_version` + `is_current` flags. A single function, `schedule.rebuild_from_period`,
is the *only* thing that ever regenerates schedule rows, and it is reused by every event that
can change the future of a schedule: the first tranche disbursement, every subsequent tranche,
a restructure (moratorium/re-tenure), and the retrospective-correction catch-up. It always:
flags the old *not-yet-recovered* rows `is_current = 0` (kept, never deleted — that's the audit
trail) and inserts a fresh, incremented-version tail from the given period forward on the
current true outstanding balance. Periods already recovered are never touched by anything.
A standalone doctype (over a child table) was chosen specifically so both the old and new
schedule can coexist and be queried directly for the aging/schedule-vs-actual reports without
walking every version out of a child table.

## 4. A month where net pay can't cover the instalment

Chosen rule: **never push net pay negative.** The loan deduction is computed last, capped at
`max(net_pay_before_loan, 0)`. Any shortfall is not "spread" or estimated — it simply isn't
recovered that period, and the schedule row is left `Partially Recovered`/`Pending`, which is
exactly what feeds the arrears figure and the aging report. On a later payslip with headroom,
`payroll.recover_against_available_funds` sweeps oldest-due periods first (interest before
principal within a period, same ordering as direct repayment, for one consistent rule
everywhere), so a real backlog does get caught up automatically rather than needing a separate
manual arrears sweep.

Retrospective correction (a two-months-ago slip cancelled and resubmitted with different
figures, after later periods already recovered and posted): the chosen policy is **adjust in
the current period, never rewrite a closed one.** Cancelling reverses only that period's own
row. The later, already-recovered periods' rows and their salary slips are never touched — they
are the historical record of what was actually posted and reported. Principal self-heals for
free (outstanding principal is always `disbursed − actual principal recovered to date`, not a
sum of stale per-period projections). Interest does not self-heal the same way, since the later
periods already recognised a specific interest figure against the old, now-wrong balance;
`retrospective.apply_catchup_adjustment` recomputes what those periods' interest *should* have
been against the corrected balance and posts the one-off net difference as a single Journal
Entry dated today, with a remark naming the loan and the periods involved. This was chosen over
restating those periods (would mean reopening closed payroll) or reversing-and-replaying them
(would mean re-issuing payslips that already paid someone) specifically because it never
touches a period Finance has already closed and reported on — the only new entry an auditor
sees is dated the day the correction was made, with a clear paper trail to why.

## 5. Report type and the control-account tie

Both required reports are Script Reports (raw SQL), not list views, specifically for the
sub-3-second target across the full dataset and because "Schedule vs Actual" needs a grouped,
running-total shape a list view can't produce. Both apply the same row-level scope helper the
permission hooks use (`permissions/common.py::employee_scope_condition`), so a Reporting
Manager running either report only ever sees their own reporting line, never the company.

The aging report's control-account tie deliberately computes `outstanding_interest` on an
**accrual basis** — only interest on periods already *due* counts as outstanding, not the full
remaining-tenure interest — because the GL only ever reflects interest that an actual accrual
Journal Entry has posted (`Loan Interest Accrual`, run monthly). That accrual job is the one
piece of this design most likely to drift from the GL in practice: if it hasn't run yet for the
latest due period, the computed total and the GL balance will disagree for that loan until the
next run catches up. That's the mechanism behind the deliberately-seeded break mentioned below
— it is a real, inherent property of accrual-basis accounting with a periodic job, not a bug to
paper over, and the report surfaces it (GL balance, computed total, difference) rather than
hiding it.

## 6. What I'd reuse from ERPNext's `lending` app

Never installed or vendored, per the brief, but it's worth being specific about what's worth
reusing if this were greenfield without that constraint: its **interest accrual and demand
generation engine** (`process_loan_interest_accrual_and_demand`) is materially more complete
than this build's `tasks.accrue_monthly_interest` — it handles moratoriums, penalty interest,
and partial-period accrual on disbursement/foreclosure dates properly, where this build accrues
one flat figure per calendar month per loan. Its `Loan Security`/collateral model and
multi-tranche disbursement schedule editor are also more mature than what's here. What I would
*not* reuse as-is is its payroll integration (`salary_slip_loan_utils.py`) — it's built for
loans the employee actively opts into and manages themselves via a self-service portal, not for
an HR/Finance-driven sanction-and-recover flow gated by an approval matrix, and its `Loan`
schema (`applicant`, `repay_from_salary`, tied to `Loan Product`) doesn't carry the
company/board-resolution/employee-grade shape this brief actually needs.

## 7. What was cut, and why

Built and working: Loan Application + eligibility + approval matrix; Loan + both interest
modes; Disbursement with tranches and ledger postings; the Salary Slip payroll deduction split
principal/interest; direct repayment; retrospective correction; both required reports; all
three access-control layers; Restructure, Write-off, and monthly interest accrual (all listed
as stretch, all implemented, none deeply battle-tested under adversarial data).

Cut or thin, in order of how much I'd want it back:
- **Approver delegation on leave** — the lookup (`approval._is_on_approved_leave`) is written
  and wired, but I did not get to write a dedicated test forcing the leave-overlap path, so
  treat it as unverified rather than trusted.
- **Inter-company transfer mid-loan** — not implemented. This needs a real settlement
  mechanism between two companies' books (an inter-company journal or a formal transfer
  document), which is its own sub-project; I didn't want to ship a half-modelled version of it.
- **The two optional reports** (Loan Portfolio Exposure, Interest Accrued vs Collected) — not
  built. Both are additive on top of data this app already tracks; there was nothing novel to
  design, it was purely a time cut.
- **REST endpoint, print format, notifications** — not built; nothing in the core lifecycle
  depends on them.
- **Foreclosure's interest rebate on unaccrued interest** — `Loan Write Off`'s settlement path
  and direct repayment's `Foreclosure` type both exist, but the *rebate* calculation specifically
  (crediting back interest that hasn't accrued yet) is not separately implemented — a
  foreclosure today pays off exactly the accrued-to-date outstanding, with no rebate line.
- Test coverage: schedule math (both interest modes against the brief's own illustrative
  table), loan sanction, the disbursement segregation-of-duty rule, and the full payroll
  recovery channel (deduction appears with the correct principal/interest split, and reverses
  cleanly on cancel) are all covered by passing integration tests against a real v16 stack.
  Writing the payroll test surfaced a genuine bug worth recording: `validate()` runs again at
  submit time on the same in-memory document that was already saved as a draft, and by then
  the schedule period it recovered against is already `Recovered` — a second recompute found
  nothing due, stripped the already-correct `loan_recoveries`/deduction rows, and never put
  them back, silently orphaning the deduction on every real submit. Fixed by recomputing only
  on the document's first save or a genuine amend (`self.is_new() or self.amended_from`), never
  on a plain draft→submit transition. What still lacks a dedicated test: the retrospective-
  correction catch-up JE, restructure/moratorium, write-off, and the interest accrual job —
  built and manually reasoned through, not test-locked. That's the first thing I'd add with
  another day.
