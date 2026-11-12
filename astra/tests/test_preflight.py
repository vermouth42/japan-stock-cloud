import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from astra.preflight import JST, Snapshot, evaluate


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 10, 0, tzinfo=JST)
        self.codes = {str(1000 + i) for i in range(100)}
        self.s = Snapshot(symbol="1000", rank_symbol="1000", sbi_symbol="1000", rank="A", rank_checked_at=self.now, market_date=date(2026, 11, 9), expected_market_date=date(2026, 11, 9), trading_day_verified=True, data_verified=True, stocks=100, rows=4500, duplicates=0, sbi_checked_at=self.now, event_checked_at=self.now, event_business_days=4, event_evidence="synthetic-fixture", current_price=1000, vwap=1000, stop=950, allowed_loss=10000, remaining_risk_budget=10000, available_cash=1000000, recommended_shares=300, cost_per_share=2, volume_ok=True, wick_ok=True, gap_ok=True, board_ok=True)

    def test_defaults_block(self):
        self.assertFalse(evaluate(Snapshot(), self.now, self.codes).go_allowed)

    def test_equal_vwap_and_lot_risk_limit(self):
        d = evaluate(self.s, self.now, self.codes)
        self.assertTrue(d.go_allowed)
        self.assertEqual((d.shares, d.planned_loss), (100, 5200))

    def test_a_plus(self):
        self.assertTrue(evaluate(replace(self.s, rank="A+"), self.now, self.codes).go_allowed)

    def test_each_missing_or_invalid_condition_blocks(self):
        cases = [dict(rank="B"), dict(rank="C"), dict(rank=""), dict(rank_symbol="1001"), dict(sbi_symbol="1001"), dict(rank_checked_at=self.now-timedelta(days=1)), dict(sbi_checked_at=None), dict(sbi_checked_at=self.now-timedelta(minutes=6)), dict(sbi_checked_at=self.now+timedelta(seconds=1)), dict(sbi_checked_at=datetime(2026,11,10,10)), dict(market_date=date(2026,11,6)), dict(market_date=date(2026,11,10), expected_market_date=date(2026,11,10)), dict(trading_day_verified=False), dict(data_verified=False), dict(stocks=99), dict(rows=4499), dict(duplicates=1), dict(event_business_days=None), dict(event_business_days=0), dict(event_business_days=3), dict(event_evidence=""), dict(event_checked_at=None), dict(recommended_shares=99), dict(recommended_shares=150), dict(current_price=999), dict(stop=1000), dict(stop=-1), dict(current_price=float("nan")), dict(vwap=float("inf")), dict(cost_per_share=-1), dict(allowed_loss=100), dict(remaining_risk_budget=100), dict(available_cash=99999), dict(volume_ok=False), dict(wick_ok=False), dict(gap_ok=False), dict(board_ok=False)]
        for fields in cases:
            with self.subTest(fields=fields):
                self.assertFalse(evaluate(replace(self.s, **fields), self.now, self.codes).go_allowed)

    def test_watchlist_must_be_complete(self):
        self.assertFalse(evaluate(self.s, self.now, {"1000"}).go_allowed)

    def test_risk_is_recalculated_at_current_price(self):
        self.assertFalse(evaluate(replace(self.s, current_price=1100), self.now, self.codes).go_allowed)

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(self.s, self.now.replace(tzinfo=None), self.codes)


if __name__ == "__main__":
    unittest.main()
