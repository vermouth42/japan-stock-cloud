import copy
import unittest
from datetime import date, datetime, timedelta

from astra.market_data import JST, calculate
from astra.sheet_rows import prepare
from astra.sync_audit import audit


class SyncAuditTest(unittest.TestCase):
    def setUp(self):
        self.codes = [str(1000 + i) for i in range(100)]
        self.cutoff = date(2026, 11, 9)
        self.now = datetime(2026, 11, 10, 10, tzinfo=JST)
        days = [self.cutoff - timedelta(days=i) for i in range(80)]
        days = sorted(d for d in days if d.weekday() < 5)[-45:]
        bars = [dict(date=d.isoformat(), open=100, high=110, low=95, close=105, volume=10000) for d in days]
        self.rows = [r for code in self.codes for r in calculate(code, bars)]
        self.sheet = prepare(self.rows, self.codes, self.cutoff)
        self.status = dict(updated_at_jst=self.now.isoformat(), latest_market_date=self.cutoff.isoformat(),
                           stocks_total=100, stocks_success=100, stocks_failed=0, rows=4500, failed=[])

    def check(self, **changes):
        values = dict(status=self.status, repo_rows=self.rows, sheet_rows=self.sheet, codes=self.codes, now=self.now)
        values.update(changes)
        return audit(**values)

    def test_exact_match_is_data_ready_but_never_go(self):
        result = self.check(sheet_rows=list(reversed(self.sheet)))
        self.assertTrue(result["data_ready"])
        self.assertTrue(result["exact_import_match"])
        self.assertFalse(result["go_allowed"])

    def test_both_sources_matching_but_stale_blocks(self):
        result = self.check(now=self.now + timedelta(days=1))
        self.assertTrue(result["exact_import_match"])
        self.assertFalse(result["data_ready"])
        self.assertIn("REPOSITORY_STALE_OR_FUTURE", result["reasons"])
        self.assertIn("SHEET_STALE_OR_FUTURE", result["reasons"])

    def test_partial_stock_freshness_and_duplicates_block(self):
        changed = copy.deepcopy(self.sheet)
        changed[0] = copy.deepcopy(changed[100])
        result = self.check(sheet_rows=changed)
        self.assertFalse(result["data_ready"])
        self.assertIn("SHEET_INTEGRITY", result["reasons"])
        self.assertIn("SHEET_STALE_OR_FUTURE", result["reasons"])

    def test_price_change_despite_matching_counts_blocks(self):
        changed = copy.deepcopy(self.sheet)
        changed[0][6] = 999
        self.assertIn("IMPORT_CONTENT_MISMATCH", self.check(sheet_rows=changed)["reasons"])

    def test_invalid_inputs_fail_closed(self):
        for change in (dict(sheet_rows=[]), dict(sheet_rows=[[True, None, 1000]]),
                       dict(status={**self.status, "stocks_success": 99}),
                       dict(status={**self.status, "updated_at_jst": (self.now + timedelta(days=1)).isoformat()}),
                       dict(codes=self.codes[:-1]), dict(now=self.now.replace(year=2028))):
            with self.subTest(change=list(change)):
                self.assertFalse(self.check(**change)["data_ready"])

    def test_invalid_ohlcv_cannot_be_blessed_by_matching_sheet(self):
        changed = copy.deepcopy(self.rows)
        changed[0][5] = "nan"
        self.assertIn("REPOSITORY_INVALID", self.check(repo_rows=changed)["reasons"])


if __name__ == "__main__":
    unittest.main()
