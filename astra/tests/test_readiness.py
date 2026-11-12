import unittest
from datetime import date, datetime, timedelta

from astra.market_data import JST, calculate
from astra.readiness import assess, entry_session, parse_manual
from astra.sheet_rows import prepare


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 10, tzinfo=JST)
        self.codes = [str(1000 + i) for i in range(100)]
        cutoff = date(2026, 11, 9)
        days = sorted(cutoff - timedelta(days=i) for i in range(70))[-45:]
        bars = [dict(date=d.isoformat(), open=100, high=110, low=95, close=105, volume=10000) for d in days]
        self.rows = [r for code in self.codes for r in calculate(code, bars)]
        self.sheet = prepare(self.rows, self.codes, cutoff)
        self.status = dict(updated_at_jst="2026-11-10T08:00:00+09:00", latest_market_date="2026-11-09",
                           stocks_total=100, stocks_success=100, stocks_failed=0, rows=4500, failed=[])
        self.manual = dict(symbol="1000", rank_symbol="1000", sbi_symbol="1000", rank="A",
            rank_checked_at=self.now.isoformat(), sbi_checked_at=self.now.isoformat(),
            event_checked_at=self.now.isoformat(), event_business_days=4, event_evidence="synthetic-only",
            current_price=1000, vwap=1000, stop=950, allowed_loss=10000, remaining_risk_budget=10000,
            available_cash=1000000, recommended_shares=300, cost_per_share=2,
            volume_ok=True, wick_ok=True, gap_ok=True, board_ok=True)

    def check(self, **changes):
        args = dict(status=self.status, repo_rows=self.rows, sheet_rows=self.sheet,
                    codes=self.codes, manual=self.manual, now=self.now)
        args.update(changes)
        return assess(**args)

    def test_passing_rehearsal_never_grants_live_go(self):
        r = self.check()
        self.assertTrue(r["offline_checks_passed"])
        self.assertEqual((r["proposed_shares"], r["planned_loss"]), (100, 5200))
        self.assertFalse(r["go_allowed"])
        self.assertTrue(r["live_blockers"])

    def test_stale_sources_block_even_with_fresh_manual_checks(self):
        now = self.now + timedelta(days=1)
        manual = {**self.manual, **{k: now.isoformat() for k in
                  ("rank_checked_at", "sbi_checked_at", "event_checked_at")}}
        r = self.check(now=now, manual=manual)
        self.assertFalse(r["offline_checks_passed"])
        self.assertEqual(r["proposed_shares"], 0)
        self.assertIn("REPOSITORY_STALE_OR_FUTURE", r["reasons"])

    def test_cannot_supply_a_data_verified_override(self):
        r = self.check(manual={**self.manual, "data_verified": True})
        self.assertIn("MANUAL_INPUT_INVALID", r["reasons"])
        self.assertEqual(r["proposed_shares"], 0)

    def test_symbol_rank_cash_event_and_staleness_fail_closed(self):
        for change in (dict(sbi_symbol="1001"), dict(rank="B"), dict(rank="C"),
                       dict(available_cash=99999), dict(event_business_days=3),
                       dict(sbi_checked_at="2026-11-10T09:54:59+09:00"),
                       dict(current_price=999), dict(board_ok="TRUE"), dict(rank=[])):
            with self.subTest(change=change):
                r = self.check(manual={**self.manual, **change})
                self.assertFalse(r["offline_checks_passed"])
                self.assertFalse(r["go_allowed"])
                self.assertEqual(r["proposed_shares"], 0)

    def test_session_boundaries_and_holidays(self):
        for clock, expected in (("09:04:59", False), ("09:05:00", True),
                                ("11:24:59", True), ("11:25:00", False),
                                ("12:29:59", False), ("12:30:00", True),
                                ("14:54:59", True), ("14:55:00", False)):
            self.assertEqual(entry_session(datetime.fromisoformat(f"2026-11-10T{clock}+09:00")), expected)
        for day in ("2026-11-14", "2026-11-23"):
            self.assertFalse(entry_session(datetime.fromisoformat(f"{day}T10:00:00+09:00")))
        r = self.check(now=self.now.replace(hour=12))
        self.assertIn("OUTSIDE_VALIDATED_ENTRY_SESSION", r["reasons"])

    def test_bad_timestamps_and_empty_inputs(self):
        for manual in ({}, {**self.manual, "sbi_checked_at": "2026-11-10T10:00:00"},
                       {**self.manual, "sbi_checked_at": 42}):
            self.assertFalse(self.check(manual=manual)["offline_checks_passed"])
        with self.assertRaises(ValueError):
            parse_manual({"token": "never-accept-credentials"})


if __name__ == "__main__":
    unittest.main()
