"""スイング戦略を prices.csv の全銘柄でバックテストする。

使い方:
    python python/backtest.py                    # 買いのみ
    python python/backtest.py --short            # 信用売りも含める
    python python/backtest.py --trades trades.csv  # 全トレードを CSV 出力
    python python/backtest.py --stop-atr 1.5 --tp-r 3

注意: prices.csv は約240日分しかないため（75日線の計算に75本使う）、検証期間は短い。
本番前に TradingView のストラテジーテスターで長期間を確認すること。
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jpstock.backtest import Trade, backtest_swing, summarize  # noqa: E402
from jpstock.data import load_prices  # noqa: E402
from jpstock.rules import SETUP_LABELS, SwingParams  # noqa: E402


def print_summary(title: str, stats: Dict[str, float]) -> None:
    print(f"■ {title}")
    if not stats.get("trades"):
        print("  トレードなし")
        return
    pf = stats["profit_factor"]
    print(
        f"  回数 {stats['trades']:>4} | 勝率 {stats['win_rate']:5.1f}% | 平均 {stats['avg_r']:+.2f}R"
        f" | 中央値 {stats['median_r']:+.2f}R | PF {'∞' if pf == float('inf') else f'{pf:.2f}'}"
    )
    print(
        f"  平均勝ち {stats['avg_win_r']:+.2f}R | 平均負け {stats['avg_loss_r']:+.2f}R"
        f" | 合計 {stats['total_r']:+.1f}R | 最大DD {stats['max_dd_r']:.1f}R"
        f" | 最大連敗 {stats['max_losing_streak']} | 平均保有 {stats['avg_bars']:.1f}日"
        f" | 平均損益 {stats['avg_return_pct']:+.2f}%"
    )


def write_trades(trades: List[Trade], path: str) -> None:
    fields = [f.name for f in dataclasses.fields(Trade)]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            writer.writerow(dataclasses.asdict(t))


def main(argv: Optional[List[str]] = None) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    defaults = SwingParams()
    parser = argparse.ArgumentParser(description="日本株スイング戦略のバックテスト")
    parser.add_argument("--prices", default=os.path.join(root, "prices.csv"))
    parser.add_argument("--short", action="store_true", help="信用売り（BD/RS）も含める")
    parser.add_argument("--no-long", action="store_true", help="買いを含めない")
    parser.add_argument("--stop-atr", type=float, default=defaults.stop_atr)
    parser.add_argument("--trail-atr", type=float, default=defaults.trail_atr)
    parser.add_argument("--tp-r", type=float, default=defaults.tp_r)
    parser.add_argument("--tp-pct", type=float, default=defaults.tp_pct)
    parser.add_argument("--max-hold", type=int, default=defaults.max_hold)
    parser.add_argument("--no-ma-exit", action="store_true", help="25日線割れの手仕舞いを使わない")
    parser.add_argument("--ma-exit-atr", type=float, default=defaults.ma_exit_atr,
                        help="25日線割れ判定の余裕（ATR 倍）")
    parser.add_argument("--slippage", type=float, default=1.0, help="成行・逆指値のスリッページ（円）")
    parser.add_argument("--trades", default="", help="全トレードの出力先 CSV")
    args = parser.parse_args(argv)

    p = dataclasses.replace(
        defaults,
        stop_atr=args.stop_atr,
        trail_atr=args.trail_atr,
        tp_r=args.tp_r,
        tp_pct=args.tp_pct,
        max_hold=args.max_hold,
        ma_exit=not args.no_ma_exit,
        ma_exit_atr=args.ma_exit_atr,
    )
    data = load_prices(args.prices)
    trades: List[Trade] = []
    for code, bars in sorted(data.items()):
        trades.extend(
            backtest_swing(
                code, bars, p,
                allow_long=not args.no_long,
                allow_short=args.short,
                slippage=args.slippage,
            )
        )
    trades.sort(key=lambda t: (t.entry_date, t.code))

    first = min((b[0].date for b in data.values() if b), default="-")
    last = max((b[-1].date for b in data.values() if b), default="-")
    print(f"期間 {first} 〜 {last} / {len(data)} 銘柄 / 損切り {p.stop_atr}ATR・トレール {p.trail_atr}ATR"
          f"・利確 {p.tp_r}R×{p.tp_pct:.0f}%・最大 {p.max_hold}日")
    print_summary("全体", summarize(trades))
    for setup, label in SETUP_LABELS.items():
        subset = [t for t in trades if t.setup == setup]
        if subset:
            print_summary(f"{label}（{setup}）", summarize(subset))
    closed = [t for t in trades if not t.open_at_end]
    if len(closed) != len(trades):
        print_summary("決済済みのみ（期末の評価分を除く）", summarize(closed))

    reasons: Dict[str, int] = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print("■ 決済理由: " + " / ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])))

    if args.trades:
        write_trades(trades, args.trades)
        print(f"トレード一覧 → {args.trades}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
