# Loan Assignment

Employee Loan Lifecycle module for ERPNext/HRMS — application, approval, sanction,
disbursement, payroll/direct repayment, adjustment and closure of staff loans, with a full
audit trail. Built for Frappe v16 / ERPNext v16 / HRMS v16.

See `DECISIONS.md` for the design rationale and what was cut.

## Install

On a bench already running Frappe v16 / ERPNext v16 / HRMS v16:

```bash
bench get-app loan_assignment <path-or-url-to-this-repo>
bench --site <site-name> install-app loan_assignment
bench --site <site-name> migrate
```

`migrate` applies the schema patches, syncs the fixtures (the `loan_recoveries` custom field
and section on Salary Slip, the `Loan Recovery` Salary Component, and the five roles this
module uses: Reporting Manager, HR Manager, Finance Manager, Treasury Officer, CFO), and
creates the three Frappe Workflows this app ships (Loan Disbursement, Loan Restructure
Request, Loan Write Off) via an idempotent post-model-sync patch — safe to run repeatedly.

Restart the bench (`bench restart`, or just `bench start` if running in dev mode) afterwards
so the Salary Slip `override_doctype_class` takes effect.

## Set up before using it

This module intentionally does not seed business data — only schema, fixtures and workflows.
Before applying for a loan, an Administrator/HR Manager needs to create, per company:

1. **Chart of accounts**: an Employee Loan Receivable (Asset), an Interest Income (Income) and
   a Staff Loan Written Off (Expense) account.
2. At least one **Loan Type** (Personal / Housing / Emergency, or your own), pointing at those
   three accounts. Saving a Loan Type automatically wires the `Loan Recovery` Salary
   Component's account mapping for that company — no manual payroll accounting setup needed.
3. At least one **Loan Approval Rule** per amount band your company needs (the seeded example
   in the brief was B1–B4), each with its ordered `Loan Approval Step` chain. This is data, not
   code — no deployment needed to add a band.
4. The five roles above assigned to the right users (Reporting Manager is assigned per actual
   manager, not company-wide; the rest are ordinary role assignments).

## Running tests

```bash
bench --site <site-name> run-tests --app loan_assignment
```

`test_schedule_math.py` is pure logic (no DB, no fixtures) and covers both interest modes
against the brief's own illustrative amortisation table. `doctype/loan/test_loan.py` is an
integration test that stands up its own minimal Company/Employee/Loan Type fixtures and
exercises sanction → disbursement → the segregation-of-duty rule end to end.

## Module layout

- `loan_assignment/doctype/` — all DocTypes (masters, the application/loan/disbursement/
  repayment documents, and the stretch doctypes: interest accrual, restructure, write-off).
- `loan_assignment/utils.py`, `schedule.py` — interest-schedule math and the single
  schedule-regeneration function reused by tranche disbursement, restructure and retrospective
  correction.
- `loan_assignment/approval.py`, `eligibility.py` — the Loan Application matrix engine.
- `loan_assignment/permissions/` — row-level visibility (`permission_query_conditions` /
  `has_permission`) shared by the documents and both reports.
- `loan_assignment/ledger.py`, `retrospective.py` — GL postings via Payment Entry / Journal
  Entry for disbursement, direct repayment, write-off, and the retrospective catch-up.
- `loan_assignment/payroll.py`, `overrides/salary_slip.py` — the payroll deduction channel.
- `loan_assignment/setup.py` — idempotent Workflow creation (called from a post-model-sync
  patch, safe on every `bench migrate`).
- `loan_assignment/report/` — the two required Script Reports.

## License

mit
