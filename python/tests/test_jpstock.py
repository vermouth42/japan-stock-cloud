"""python -m unittest discover -s python/tests"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from jpstock import indicators as ta  # noqa: E402
from jpstock.backtest import backtest_swing, summarize  # noqa: E402
from jpstock.rules import Bar, SwingParams, analyze, technical_score  # noqa: E402


class IndicatorTest(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(ta.sma([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4])

    def test_sma_skips_window_with_none(self):
        self.assertEqual(ta.sma([None, 2, 4, 6], 2), [None, None, 3, 5])

    def test_rma_seeds_with_sma(self):
        self.assertEqual(ta.rma([1, 2, 3, 4], 2), [None, 1.5, 2.25, 3.125])

    def test_rsi_matches_hand_calculation(self):
        out = ta.rsi([1, 2, 1, 2, 1, 2], 2)
        self.assertEqual(out[:2], [None, None])
        for got, want in zip(out[2:], [50.0, 75.0, 37.5, 68.75]):
            self.assertAlmostEqual(got, want)

    def test_rsi_extremes(self):
        self.assertEqual(ta.rsi([1, 2, 3, 4, 5], 3)[-1], 100.0)
        self.assertEqual(ta.rsi([5, 4, 3, 2, 1], 3)[-1], 0.0)

    def test_true_range_and_atr(self):
        high = [10, 12, 11]
        low = [9, 10, 8]
        close = [9.5, 11, 9]
        self.assertEqual(ta.true_range(high, low, close), [1, 2.5, 3])
        self.assertEqual(ta.atr(high, low, close, 2), [None, 1.75, 2.375])

    def test_highest_lowest_shift(self):
        self.assertEqual(ta.highest([1, 3, 2, 5], 2), [None, 3, 3, 5])
        self.assertEqual(ta.lowest([1, 3, 2, 5], 2), [None, 1, 2, 2])
        self.assertEqual(ta.shift([1, 2, 3], 1), [None, 1, 2])
        self.assertEqual(ta.shift([1, 2], 5), [None, None])


def _bars(rows):
    return [Bar(f"2026-01-{i + 1:02d}", o, h, lo, c, 1000) for i, (o, h, lo, c) in enumerate(rows)]


def _analysis(n, setups, atr=10.0, ma_m=None):
    return {
        "setup": setups + [None] * (n - len(setups)),
        "atr": [atr] * n,
        "ma_m": ma_m if ma_m is not None else [None] * n,
    }


class BacktestTest(unittest.TestCase):
    params = SwingParams(stop_atr=2.0, trail_atr=3.0, tp_r=2.0, tp_pct=50.0, max_hold=0, ma_exit=False)

    def test_entry_next_open_and_stop_loss(self):
        # 0日目終値 1000 でシグナル → 1日目始値 1005 で買い、損切り 980（= 1000 − 2×10）
        bars = _bars([
            (990, 1001, 985, 1000),
            (1005, 1010, 975, 990),
            (990, 995, 985, 990),
        ])
        trades = backtest_swing("T", bars, self.params, analysis=_analysis(3, ["PB"]))
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual((t.entry_date, t.entry_price, t.init_stop), ("2026-01-02", 1005, 980))
        self.assertEqual((t.exit_price, t.exit_reason), (980, "損切り"))
        self.assertAlmostEqual(t.r_multiple, -25 / 20)

    def test_gap_down_through_stop_fills_at_open(self):
        bars = _bars([
            (990, 1001, 985, 1000),
            (1000, 1004, 996, 1001),
            (960, 970, 950, 965),
        ])
        t = backtest_swing("T", bars, self.params, analysis=_analysis(3, ["PB"]))[0]
        self.assertEqual(t.exit_price, 960)

    def test_partial_take_profit_then_breakeven(self):
        # 利確 1040（= 1000 + 2R）で半分、その後は建値 1000 のストップで残りを決済
        bars = _bars([
            (990, 1001, 985, 1000),
            (1000, 1045, 999, 1040),
            (1035, 1036, 995, 998),
        ])
        # トレール幅 5ATR（1045 − 50 = 995）は建値より下なので、建値ストップが効く
        p = SwingParams(stop_atr=2.0, trail_atr=5.0, tp_r=2.0, tp_pct=50.0, max_hold=0, ma_exit=False)
        t = backtest_swing("T", bars, p, analysis=_analysis(3, ["BO"]))[0]
        self.assertEqual((t.exit_price, t.exit_reason), (1020, "建値ストップ"))
        self.assertAlmostEqual(t.r_multiple, 0.5 * 40 / 20)

    def test_trailing_stop_follows_high(self):
        # 高値 1100 − 3×10 = 1070 にトレール
        bars = _bars([
            (990, 1001, 985, 1000),
            (1000, 1030, 999, 1025),
            (1030, 1100, 1028, 1090),
            (1085, 1086, 1060, 1065),
        ])
        t = backtest_swing("T", bars, self.params, analysis=_analysis(4, ["PB"]))[0]
        self.assertEqual(t.exit_reason, "トレーリング")
        self.assertAlmostEqual(t.r_multiple, (0.5 * 40 + 0.5 * 70) / 20)

    def test_ma_exit_fills_next_open(self):
        p = SwingParams(stop_atr=2.0, tp_r=5.0, max_hold=0, ma_exit=True, ma_exit_atr=1.0)
        bars = _bars([
            (990, 1001, 985, 1000),
            (1000, 1010, 995, 1005),
            (1004, 1006, 990, 992),  # 25日線 1005 − 1ATR = 995 を下回って引け
            (993, 1000, 990, 996),
        ])
        a = _analysis(4, ["PB"], ma_m=[1000, 1003, 1005, 1006])
        t = backtest_swing("T", bars, p, analysis=a)[0]
        self.assertEqual((t.exit_date, t.exit_price, t.exit_reason), ("2026-01-04", 993, "25日線割れ"))

    def test_short_side_mirrors_long(self):
        bars = _bars([
            (1010, 1015, 999, 1000),
            (995, 1025, 990, 1015),
        ])
        trades = backtest_swing(
            "T", bars, self.params, allow_long=False, allow_short=True, analysis=_analysis(2, ["RS"])
        )
        self.assertEqual(trades[0].side, "short")
        self.assertEqual(trades[0].exit_price, 1020)

    def test_summary(self):
        bars = _bars([(990, 1001, 985, 1000), (1005, 1010, 975, 990), (990, 995, 985, 990)])
        stats = summarize(backtest_swing("T", bars, self.params, analysis=_analysis(3, ["PB"])))
        self.assertEqual(stats["trades"], 1)
        self.assertEqual(stats["win_rate"], 0.0)


class RulesTest(unittest.TestCase):
    def test_pullback_signal_in_uptrend(self):
        # 緩やかな上昇トレンド → 25日線付近まで押す → 前日高値を超える陽線
        rows = []
        price = 1000.0
        for _ in range(100):
            price *= 1.004
            rows.append((price * 0.995, price * 1.01, price * 0.99, price))
        top = price
        for _ in range(6):
            price *= 0.985
            rows.append((price * 1.005, price * 1.01, price * 0.99, price))
        bars = _bars(rows)
        a = analyze(bars, SwingParams())
        ma_m = a["ma_m"][-1]
        # 25日線の少し上で反発する陽線
        last_close = max(ma_m * 1.01, bars[-1].high * 1.002)
        bars.append(Bar("2026-12-31", ma_m * 0.995, last_close * 1.001, ma_m * 0.99, last_close, 1500))
        a = analyze(bars, SwingParams())
        self.assertTrue(a["up_trend"][-1])
        self.assertEqual(a["setup"][-1], "PB", msg=f"rsi={a['rsi'][-1]} top={top}")

    def test_trend_ma_filter(self):
        rows = []
        price = 1000.0
        for _ in range(100):
            price *= 1.004
            rows.append((price * 0.995, price * 1.01, price * 0.99, price))
        for _ in range(6):
            price *= 0.985
            rows.append((price * 1.005, price * 1.01, price * 0.99, price))
        bars = _bars(rows)
        ma_m = analyze(bars, SwingParams())["ma_m"][-1]
        last_close = max(ma_m * 1.01, bars[-1].high * 1.002)
        bars.append(Bar("2026-12-31", ma_m * 0.995, last_close * 1.001, ma_m * 0.99, last_close, 1500))
        # 50日線より上 → シグナルはそのまま
        self.assertEqual(analyze(bars, SwingParams(trend_ma_len=50))["setup"][-1], "PB")
        # 200日分のデータがない → シグナルを出さない
        self.assertIsNone(analyze(bars, SwingParams(trend_ma_len=200))["setup"][-1])
        # 長期線より下（極端に長い線を高値圏に置いた想定）→ 買わない
        high_bars = [Bar(b.date, b.open * 3, b.high * 3, b.low * 3, b.close * 3, b.volume) for b in bars[:20]] + bars[20:]
        self.assertIsNone(analyze(high_bars, SwingParams(trend_ma_len=100))["setup"][-1])

    def test_score_bounds(self):
        hi = technical_score(close=110, ma_s=108, ma_m=105, ma_l=100, ma_m_prev=104,
                             rsi=60, vol_ratio=2.5, dev=5, hh=111)
        self.assertEqual(hi, 100)
        lo = technical_score(close=90, ma_s=92, ma_m=95, ma_l=100, ma_m_prev=96,
                             rsi=85, vol_ratio=0.5, dev=-5, hh=120)
        self.assertEqual(lo, 0)


if __name__ == "__main__":
    unittest.main()
