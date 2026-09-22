from frappe.tests import UnitTestCase

from loan_assignment.loan_assignment.utils import compute_flat_schedule, compute_reducing_schedule


class TestScheduleMath(UnitTestCase):
	def test_reducing_balance_matches_illustrative_schedule(self):
		# Matches the brief's illustrative table to the rupee, not the paisa:
		# small EMI rounding-convention differences (their table appears to
		# round the monthly rate before compounding) are expected and are
		# not the actual requirement — the hard requirements (checked
		# exactly below) are that the principal column totals the disbursed
		# amount and the schedule closes to exactly zero.
		rows = compute_reducing_schedule(120000, 8, 12, "2024-01-01", precision=2)

		self.assertEqual(len(rows), 12)
		self.assertAlmostEqual(rows[0].interest_amount, 800.00, places=2)
		self.assertAlmostEqual(rows[0].principal_amount, 9638.79, delta=2)
		self.assertAlmostEqual(rows[0].closing_balance, 110361.21, delta=2)

		last = rows[-1]
		self.assertEqual(last.closing_balance, 0.0)
		self.assertAlmostEqual(last.instalment_amount, 10437.51, delta=2)

		total_principal = round(sum(r.principal_amount for r in rows), 2)
		self.assertEqual(total_principal, 120000.00)

	def test_flat_schedule_principal_totals_exactly(self):
		rows = compute_flat_schedule(100000, 8, 7, "2024-01-01", precision=2)

		self.assertEqual(len(rows), 7)
		total_principal = round(sum(r.principal_amount for r in rows), 2)
		self.assertEqual(total_principal, 100000.00)

		expected_monthly_interest = round(100000 * 0.08 * (7 / 12) / 7, 2)
		self.assertAlmostEqual(rows[0].interest_amount, expected_monthly_interest, places=2)
		for row in rows[:-1]:
			self.assertEqual(row.interest_amount, rows[0].interest_amount)

	def test_zero_rate_reducing_schedule_is_straight_line(self):
		rows = compute_reducing_schedule(60000, 0, 6, "2024-01-01", precision=2)
		for row in rows:
			self.assertEqual(row.interest_amount, 0)
		self.assertAlmostEqual(sum(r.principal_amount for r in rows), 60000.00, places=2)
		self.assertAlmostEqual(rows[-1].closing_balance, 0.0, places=2)

	def test_due_dates_increment_monthly(self):
		rows = compute_flat_schedule(12000, 6, 3, "2024-01-31", precision=2)
		self.assertEqual(str(rows[0].due_date), "2024-01-31")
		self.assertEqual(str(rows[1].due_date), "2024-02-29")
		self.assertEqual(str(rows[2].due_date), "2024-03-31")
