"""CLI.

  python -m keiba demo                         合成データで全工程を実演
  python -m keiba backtest --data races.csv    実データでウォークフォワード検証
  python -m keiba predict --data races.csv --card card.csv   出馬表の買い目を出力
"""
from __future__ import annotations

import argparse
import json

import pandas as pd

from .backtest import walk_forward, summarize, factor_ablation
from .betting import Strategy, select_bets, optimize_strategy
from .features import build_features, FEATURE_GROUPS
from .model import TwoStageModel
from .schema import load_csv, normalize


def _report(per, bets, preds, model, ablation=None):
    s = summarize(per, bets, preds)
    print("\n================ 検証結果 (完全アウトオブサンプル) ================")
    print(f"参考) 全馬均等買い ROI : {s.get('roi_all_horses', float('nan')):.3f}")
    print(f"参考) 1番人気買い ROI  : {s.get('roi_favorite', float('nan')):.3f}")
    print(f"買い目数 / レース数   : {s['bets']} / {s['races_bet']}")
    if s["bets"]:
        lo, hi = s["roi_ci95"]
        print(f"的中率                : {s['hit_rate']:.3f}")
        print(f"均等買い ROI          : {s['roi_flat']:.3f}  (95%CI {lo:.3f}〜{hi:.3f})")
        print(f"1/4ケリー 資金倍率    : {s['kelly_final_multiple']:.2f}x  最大DD {s['kelly_max_drawdown']:.1%}")
        print("\nオッズ帯別:")
        print(s["by_odds"].round(3).to_string())
    print("\nファンダメンタル係数 (上位):")
    print(model.coefficients().head(15).round(3).to_string())
    print("\n市場融合ウェイト:", {k: round(v, 3) for k, v in model.market_weights().items()})
    if ablation is not None:
        print("\nファクター群の寄与 (外すと失われる対数尤度/レース):")
        print(ablation.round(4).to_string(index=False))


def cmd_backtest(df, args):
    F, cols = build_features(df)
    base = Strategy(slippage=args.slippage)
    print("ウォークフォワード検証中...")
    per, bets, preds, model = walk_forward(F, cols, base_strategy=base)
    abl = None if args.no_ablation else factor_ablation(F, FEATURE_GROUPS)
    _report(per, bets, preds, model, abl)
    if args.out:
        bets.to_csv(args.out, index=False)
        print(f"\n買い目明細 -> {args.out}")


def cmd_demo(args):
    from .synthetic import generate
    print("合成データ生成中 (控除率20%・本命大穴バイアス付き市場)...")
    df = generate(n_years=args.years, races_per_week=66, seed=args.seed)
    print(f"  {df['race_id'].nunique()} レース / {len(df)} 出走")
    cmd_backtest(df, args)


def cmd_predict(args):
    hist = load_csv(args.data)
    card = normalize(pd.read_csv(args.card))
    allr = pd.concat([hist, card], ignore_index=True)
    F, cols = build_features(allr)
    cut = card["date"].min()
    past = F[F["date"] < cut]
    c0 = cut - pd.DateOffset(months=6)
    model = TwoStageModel(cols).fit(past[past["date"] < c0], past[past["date"] >= c0])
    strat = Strategy(slippage=args.slippage)
    if args.optimize:
        strat, _ = optimize_strategy(model.predict(past[past["date"] >= c0]), strat)
    pred = model.predict(F[F["race_id"].isin(card["race_id"])])
    bets = select_bets(pred, strat)
    pd.set_option("display.width", 200)
    print("戦略:", json.dumps(strat.to_dict(), ensure_ascii=False))
    cols_out = ["race_id", "horse_no", "odds_win", "p_mkt", "p_fund", "p", "ev"]
    print(pred[cols_out].sort_values(["race_id", "p"], ascending=[True, False]).round(3).to_string(index=False))
    print("\n=== 買い目 (kelly = 資金に対する賭け比率) ===")
    print(bets[cols_out + ["kelly"]].round(4).to_string(index=False) if len(bets) else "該当なし (見送り)")


def main():
    ap = argparse.ArgumentParser(prog="keiba")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo"); d.add_argument("--years", type=int, default=6); d.add_argument("--seed", type=int, default=1)
    b = sub.add_parser("backtest"); b.add_argument("--data", required=True)
    for p in (d, b):
        p.add_argument("--slippage", type=float, default=0.0)
        p.add_argument("--no-ablation", action="store_true")
        p.add_argument("--out")
    p = sub.add_parser("predict"); p.add_argument("--data", required=True); p.add_argument("--card", required=True)
    p.add_argument("--slippage", type=float, default=0.0); p.add_argument("--optimize", action="store_true")
    a = ap.parse_args()
    if a.cmd == "demo":
        cmd_demo(a)
    elif a.cmd == "backtest":
        cmd_backtest(load_csv(a.data), a)
    else:
        cmd_predict(a)


if __name__ == "__main__":
    main()
