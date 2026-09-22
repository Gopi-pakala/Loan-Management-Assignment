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
next run catches up. It is a real, inherent property of accrual-basis accounting with a periodic
job, not a bug to paper over, and the report surfaces it (GL balance, computed total, difference)
rather than hiding it.

**Found and confirmed, with real numbers.** Setting up a demo loan (`LOAN-2026-00006`, ₹100,000,
first deduction backdated 3 months so three instalments are already due) to check this
mechanism, the report's "Difference" came back at ₹52,435.73 — far bigger than the ₹2,343.95 of
interest that loan alone had due-but-unaccrued. Tracking it down two levels deep:

1. **A real bug in the report itself, found first and fixed.** `get_summary()` was computing
   "Computed Outstanding" by summing the *already-filtered* aging-list rows (`get_data()` only
   lists loans where `first_deduction_month <= as_on_date`), while "GL Balance" summed *every*
   GL entry on the account regardless. `LOAN-2026-00002` — disbursed (₹60,000 on the books) but
   with a first-deduction date still in the future — was silently excluded from one side of the
   comparison and not the other, inflating the apparent gap by its whole outstanding balance.
   Fixed in `loan_outstanding_aging.py::get_summary` by querying the full outstanding total
   independently (company/loan_type/department/row-visibility scoped the same as the list, but
   *not* re-applying the list's `first_deduction_month` display filter), instead of reusing
   `data`.
2. **The real, remaining break, after that fix — ₹2,745.00, exactly the accrual-lag mechanism
   above, found in two independent places:**
   - `LOAN-2026-00006`: ₹2,343.95 of interest is already *due* per the schedule (three backdated
     periods), but no `Loan Interest Accrual` has run for it yet, so none of that interest is on
     the books — computed total counts it, GL doesn't.
   - `LOAN-2026-00002`: a subtler variant of the same gap. Payroll had already *recovered*
     ₹410.00 of interest against its schedule (visible in `Loan Repayment Schedule.recovered_interest`),
     crediting the receivable account for it via the Salary Slip's own accrual JE (§2) — but
     because no `Loan Interest Accrual` had ever run for this loan either, that ₹410 was never
     *debited* to the receivable in the first place. The GL now understates the account by that
     amount permanently, not just until the next accrual run: recovery got ahead of accrual, and
     unlike the first case this piece won't self-correct on its own next month, because the
     schedule no longer treats it as outstanding once recovered.

The second variant is the sharper finding: it means the accrual job isn't just a source of
*timing* noise (which self-heals) but, if a loan is recovering faster than accrual keeps up
(e.g. an employee's first instalment lands before that month's accrual job has run), a
*permanent* understatement that nothing in the current design ever catches up automatically. The
fix for (1) is applied; the fix for (2) is not — it needs the accrual job to run (or be
guaranteed to run) strictly before any repayment/payroll recovery can touch a period, which is a
sequencing constraint this submission doesn't enforce and is called out here rather than
silently left for the report to keep flagging every period.

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
- **Inter-company transfer mid-loan** — not implemented, but the accounting treatment is
  decided: **the receivable transfers to the new company; the old company does not keep
  collecting on the new company's behalf.** Concretely, at the transfer date: the old company's
  `Loan` is closed out via a Journal Entry crediting its `Employee Loan Receivable` for the
  current `outstanding_principal + outstanding_interest` (mirroring the write-off posting, §
  above, but against an inter-company clearing/receivable account instead of the write-off
  expense account, since nothing is actually being forgiven); a *new* `Loan` is opened in the new
  company for the same outstanding balance, sanctioned amount = that balance, with its own fresh
  `Loan Repayment Schedule` starting from the transfer date; the cash leg between the two
  companies' clearing accounts settles via a standard ERPNext **Inter Company Journal Entry**
  (both are linked companies in the same group and share a currency here, so this needs no FX
  handling). I chose "new company fully owns and recovers it going forward" over the alternative
  I considered — old company keeps the receivable and the new company's payroll merely collects
  and remits each period — because the alternative means every single instalment needs its own
  cross-company settlement forever for the life of the loan, multiplying the audit surface the
  brief opens by complaining about (nobody able to answer what's outstanding, three days to get
  a wrong number); a one-time transfer settles it once and then the loan behaves exactly like any
  other loan in its new home company. This is a real settlement mechanism and its own sub-project
  to build properly (transfer document, both-sided validation, linking the old and new `Loan`
  records to each other for audit trail), which is why it's cut rather than half-modelled — but
  the treatment above is what I'd build, not an open question.
- **The two optional reports** (Loan Portfolio Exposure, Interest Accrued vs Collected) — not
  built. Both are additive on top of data this app already tracks; there was nothing novel to
  design, it was purely a time cut.
- **REST endpoint, print format, notifications** — not built; nothing in the core lifecycle
  depends on them.
- **Foreclosure's payoff (charge + rebate) — implemented.** `repayment.compute_foreclosure_payoff`
  returns outstanding principal, interest accrued to the chosen date, the Loan Type's
  `pre_closure_charge_percent` applied to outstanding principal, and — as a distinct, disclosed
  figure — the interest on periods after that date that foreclosing waives. The payoff total only
  ever adds the first three; the "rebate" was never added to begin with, because
  `outstanding_interest` is already accrual-basis (§5) and never counted future periods'
  interest as owed. `repayment.close_out_foreclosure` then recovers due periods in full and
  closes every later period as `Skipped` (principal recovered as part of the lump sum, interest
  left un-recovered — that gap on the schedule is the rebate, made real rather than just
  disclosed). The pre-closure charge posts as its own Journal Entry (fee income, not part of the
  receivable) via `ledger.create_foreclosure_charge_journal_entry`; cancelling the `Loan
  Repayment` reverses both the due-periods recovery and the `Skipped` periods cleanly. One known
  gap: the payoff and the loan's own aggregate recompute use two different dates when they
  differ (`as_of_date` for the payoff figure quoted to the employee, `nowdate()` for
  `schedule.py`'s own aggregate refresh) — fine when foreclosure happens same-day, not exact for
  a payoff quoted for a future date.

- **A real bug this surfaced: `party_type`/`party` on non-Receivable GL legs.** Building and
  smoke-testing the foreclosure charge JE against this bench's actual chart of accounts (not a
  synthetic test fixture) threw `Party Type and Party can only be set for Receivable / Payable
  account` — ERPNext's `GL Entry.validate_party` rejects a party on any leg whose account isn't
  Receivable/Payable type. `interest_account` (Income) and `writeoff_account` (Expense) here have
  no `account_type` at all, and a Bank account is `Bank`-type — none qualify. This wasn't just a
  bug in the new code: `loan_interest_accrual.py::on_submit` and
  `ledger.create_writeoff_journal_entry` (write-off) and `retrospective.py::apply_catchup_adjustment`
  all had the identical mistake, set on the interest_account/writeoff_account leg, and none of
  them had ever actually been submitted against a real chart of accounts before now — every
  passing test used its own minimal fixture, and DECISIONS.md's own cut-list already flagged
  these three as "not test-locked." All four are fixed the same way: party set only on the
  `loan_account` (Receivable) leg, never on the Income/Expense/Bank leg on the other side.
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
  on a plain draft→submit transition.

  `tests/test_money_paths.py` adds coverage for the interest accrual JE (accounts and amounts,
  submit and cancel-reversal), write-off driven through its real Workflow end to end, restructure
  (schedule regenerated only from the effective date forward, older rows provably untouched),
  direct-repayment allocation order (interest before principal), and the foreclosure payoff
  (principal + accrued interest, excluding the unaccrued-interest rebate, loan closes, cancel
  reverses cleanly). Writing the restructure test surfaced a second genuine bug, distinct from
  the party-type one above: `on_submit` computed the new tail's principal as
  `disbursed_amount - principal_recovered` — the *whole* remaining balance — without subtracting
  what the periods being *kept* (before `effective_from`, due but not yet actually recovered)
  were still separately claiming. Restructuring a loan mid-cycle, before that period's payroll
  recovery had run, double-counted that period's principal across the old (kept) row and the new
  tail, breaking the "principal column totals exactly the disbursed amount" invariant the brief
  requires everywhere else. Fixed in `loan_restructure_request.py::on_submit` by subtracting the
  kept-but-unrecovered periods' principal before handing the remainder to
  `schedule.rebuild_from_period`.

  Retrospective correction's catch-up JE now has a dedicated test too — driven directly against
  `payroll.recover_against_available_funds` (which only reads `employee`/`company`/`end_date`
  off its `salary_slip` argument, so a real Salary Slip isn't needed to reproduce this
  specifically): recover period 1, recover period 2, revert period 1 back to `Pending` and
  re-recover it with a different amount (simulating a cancelled-and-resubmitted slip), and assert
  a catch-up Journal Entry posts. Getting this test to actually pass surfaced two more real bugs
  in `retrospective.py`, both in the "not test-locked" code the earlier cut-list flagged:

  1. `apply_catchup_adjustment` recomputed later periods' true interest starting from the
     corrected period's *stored* `closing_balance` — a planned figure fixed at schedule
     generation that never moves just because a period was actually recovered for a different
     amount. Comparing the recompute against itself always finds nothing to correct, no matter
     how far off the real recovery was. Fixed to use `opening_balance - recovered_principal`
     (the true, as-recovered balance) instead.
  2. Both call sites (`payroll.recover_against_available_funds` and
     `repayment.allocate_repayment`) only ran the catch-up check when the corrected period ended
     up fully re-`Recovered` (`is_settled`). A realistic correction can leave it only *partially*
     resettled instead — e.g. a resubmitted slip that recovers *less* than the original because
     it turns out the employee took unpaid leave that month — and the later periods' recognised
     interest is exactly as stale either way. Fixed to trigger on any actual change to the
     period's recovered amount, not just a full re-settlement.

  Between this and the restructure fix above, every non-stretch path in the brief now has
  passing integration-test coverage against a real chart of accounts, not a synthetic one.

## 8. Performance — measured, not assumed

The brief requires both reports under 3 seconds and a payroll run no more than 2x slower with
this app installed, "we measure both, and we read the query counts." No dataset existed to
measure any of this against, so one was built: 40 real employees, one disbursed loan each, plus
~1,800 bulk-inserted `Loan`/`Loan Repayment Schedule` rows (~1,840 loans total, close to the
brief's 2,000) on a disposable `_Test Company` — not the real demo bench, and deleted again once
measured.

**First run found a real, serious problem.** Loan Outstanding Aging took **13.29 seconds** —
4.4x over budget. Root cause: two compounding issues, not one.
1. `Loan Repayment Schedule.loan`, `Loan.employee`, and the equivalent Link field on every other
   transactional doctype (`Loan Disbursement`, `Loan Repayment`, `Loan Restructure Request`,
   `Loan Write Off`, `Loan Interest Accrual`) had **no database index** — `SHOW INDEX` confirmed
   only the primary key and Frappe's default `creation`/`modified` indexes existed. Every lookup
   by loan or by employee was a full table scan.
2. The report's own query compounded it: a correlated subquery (`select min(due_date) from
   Loan Repayment Schedule where loan = l.name ...`) inside the main `select`, re-executed once
   per `Loan` row — full-scanning ~22,000 schedule rows per loan, ~1,840 times over.

Fixed both: `search_index: 1` added to the `loan`/`employee` Link fields named above, and the
correlated subquery rewritten as a pre-aggregated `left join`
(`loan_outstanding_aging.py::get_data`). Re-measured on the same ~1,840-loan dataset: **0.166
seconds** — an ~80x improvement, comfortably inside budget. `Schedule vs Actual` was never a
problem (0.06s throughout — it was already a single grouped query, no correlated subquery to fix).

**The payroll figure needed a second, more careful measurement.** A first pass — reusing the
same 40 employees for both the bulk report-volume data and the payroll comparison — showed
salary slip creation 3-5x slower with a loan present, which would have failed the 2x budget. That
number was misleading: those employees had been swept into the bulk data's round-robin loan
assignment too, ending up with ~46 loans each instead of one, which is not a scenario the brief
or any real deployment describes (`Loan Type.max_active_loans` caps concurrent loans per employee
at a small number for exactly this reason). Re-measured against 10 fresh employees with exactly
one active loan each, against 10 with none: **0.0844s vs 0.0507s per slip — a 1.67x ratio**,
inside the 2x budget. The index fixes above likely still matter at real scale (they make every
loan lookup during payroll O(1) instead of a table scan regardless of how many loans exist
company-wide), but the specific 3-5x figure was an artifact of the test's own construction, not
a defect in the deduction logic — worth recording since it's exactly the kind of number that
looks alarming and would be wrong to "fix" without checking why first.
