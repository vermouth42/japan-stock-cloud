"""fetch_history（Yahoo の JSON 解析・分割調整）と、地合いフィルター・ポートフォリオ検証のテスト。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import fetch_history as fh  # noqa: E402
from jpstock.backtest import MarketFilter, Trade, simulate_portfolio  # noqa: E402
from jpstock.rules import Bar  # noqa: E402


def ts(day: str) -> int:
    # 東証の日足は 9:00 JST（= 0:00 UTC）のタイムスタンプで返る
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def chart(days, opens, highs, lows, closes, volumes, splits=None):
    result = {
        "timestamp": [ts(d) for d in days],
        "indicators": {"quote": [{"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}]},
    }
    if splits:
        result["events"] = {"splits": {str(ts(d)): {"date": ts(d), "numerator": n, "denominator": 1} for d, n in splits}}
    return {"chart": {"result": [result], "error": None}}


class ParseChartTest(unittest.TestCase):
    def test_skips_null_rows_and_reads_splits(self):
        data = chart(
            ["2024-03-28", "2024-03-29", "2024-04-01"],
            [100, None, 10.2], [101, None, 10.5], [99, None, 10.0], [100, None, 10.3], [1000, None, 9000],
            splits=[("2024-04-01", 10)],
        )
        rows, splits = fh.parse_chart(data)
        self.assertEqual([r["date"] for r in rows], ["2024-03-28", "2024-04-01"])
        self.assertEqual(splits, [("2024-04-01", 10.0)])

    def test_no_result_raises(self):
        with self.assertRaises(RuntimeError):
            fh.parse_chart({"chart": {"result": None, "error": {"code": "Not Found"}}})


class AdjustSplitsTest(unittest.TestCase):
    def test_unadjusted_prices_are_divided(self):
        rows = [
            {"date": "2024-03-28", "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1000.0, "volume": 100.0},
            {"date": "2024-04-01", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 1000.0},
        ]
        notes = fh.adjust_splits(rows, [("2024-04-01", 10.0)])
        self.assertEqual(rows[0]["close"], 100.0)
        self.assertEqual(rows[0]["volume"], 1000.0)
        self.assertIn("を調整", notes[0])

    def test_already_adjusted_prices_are_kept(self):
        rows = [
            {"date": "2024-03-28", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
            {"date": "2024-04-01", "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 1000.0},
        ]
        notes = fh.adjust_splits(rows, [("2024-04-01", 10.0)])
        self.assertEqual(rows[0]["close"], 100.0)
        self.assertIn("調整済み", notes[0])


class MarketFilterTest(unittest.TestCase):
    def test_uses_previous_index_day(self):
        closes = [100, 100, 100, 130, 70]
        bars = [Bar(f"2026-01-0{i + 1}", c, c, c, c, 0) for i, c in enumerate(closes)]
        mf = MarketFilter(bars, ma_len=3)
        # 1/3 までは移動平均が計算できない → 通す
        self.assertTrue(mf.allows("2026-01-03", 1))
        self.assertTrue(mf.allows("2026-01-03", -1))
        # 1/5 のシグナルは 1/4（終値130 > MA110）で判定 → 買いのみ
        self.assertTrue(mf.allows("2026-01-05", 1))
        self.assertFalse(mf.allows("2026-01-05", -1))
        # 1/6 は 1/5（終値70 < MA100）で判定 → 売りのみ
        self.assertFalse(mf.allows("2026-01-06", 1))
        self.assertTrue(mf.allows("2026-01-06", -1))


def _trade(code, entry, exit_, r, score=0):
    t = Trade(code, "long", "PB", entry, entry, 100.0, 90.0, 10.0, score=score)
    t.exit_date = exit_
    t.r_multiple = r
    return t


class PortfolioTest(unittest.TestCase):
    def test_compounds_and_limits_positions(self):
        trades = [
            _trade("A", "2026-01-05", "2026-01-10", 2.0, score=50),
            _trade("B", "2026-01-05", "2026-01-08", -1.0, score=90),  # 同じ日はスコアが高い方から
            _trade("C", "2026-01-06", "2026-01-07", 1.0),             # 枠（2銘柄）が埋まっていて見送り
            _trade("D", "2026-01-12", "2026-01-15", 1.0),
        ]
        pr = simulate_portfolio(trades, start="2026-01-01", end="2026-12-31",
                                initial=1_000_000, risk_pct=1.0, max_positions=2)
        self.assertEqual((pr.taken, pr.skipped, pr.max_concurrent), (3, 1, 2))
        # A,B は 100万円の1%（1万円）で入る: −1万 + 2万 → 101万。D は 101万の1% × 1R
        self.assertAlmostEqual(pr.final, 1_010_000 + 10_100)
        self.assertAlmostEqual(pr.max_dd_pct, 1.0)


if __name__ == "__main__":
    unittest.main()
