"""CLI.

  python -m keiba demo                         合成データで全工程を実演
  python -m keiba backtest --data races.csv    実データでウォークフォワード検証
  python -m keiba predict --data races.csv --card card.csv   出馬表の買い目を出力

keiba_app の SQLite を直接使う場合 (keiba.db のあるフォルダで実行):
  python -m keiba dbcheck  --db keiba.db       年別の件数・列の充足率
  python -m keiba backtest --db keiba.db       検証 (2015年以降を読み込み、2017年以降で賭けを検証)
  python -m keiba predict  --db keiba.db       結果未確定レース (出馬表) の買い目
  python -m keiba explore  --db keiba.db       追加ファクター・非線形モデル・複勝/ワイド/馬連をまとめて検証
"""
from __future__ import annotations

import argparse
import json
import sys

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
        print(f"均等買い ROI          : {s['roi_flat']:.3f}  (95%CI {lo:.3f}~{hi:.3f})")
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
        bets.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n買い目明細 -> {args.out}")


def cmd_demo(args):
    from .synthetic import generate
    print("合成データ生成中 (控除率20%・本命大穴バイアス付き市場)...")
    df = generate(n_years=args.years, races_per_week=66, seed=args.seed)
    print(f"  {df['race_id'].nunique()} レース / {len(df)} 出走")
    cmd_backtest(df, args)


def _load(args, include_future=False):
    if args.db:
        from .sqlite_loader import load_db
        return load_db(args.db, since=args.since, odds_since=args.odds_since,
                       include_future=include_future)
    if not args.data:
        sys.exit("--data か --db のどちらかを指定してください")
    return load_csv(args.data)


def cmd_predict(args):
    allr = _load(args, include_future=True)
    if args.card:
        allr = pd.concat([allr, normalize(pd.read_csv(args.card))], ignore_index=True)
    F, cols = build_features(allr)
    future = ~F["finish_pos"].notna().groupby(F["race_id"]).transform("any")
    if args.date:
        future &= F["date"] == pd.Timestamp(args.date)
    card = F[future]
    if card.empty:
        sys.exit("予測対象 (結果未確定) のレースがありません")
    if card["odds_win"].isna().all():
        sys.exit("対象レースにオッズがありません (odds_snapshot 取得後に実行してください)")
    cut = card["date"].min()
    past = F[(F["date"] < cut) & ~future]
    c0 = cut - pd.DateOffset(months=args.calib_months)
    model = TwoStageModel(cols).fit(past[past["date"] < c0], past[past["date"] >= c0])
    strat = Strategy(slippage=args.slippage)
    if args.optimize:
        strat, _ = optimize_strategy(model.predict(past[past["date"] >= c0]).dropna(subset=["p"]), strat)
    pred = model.predict(card)
    if "horse_name" in card:
        pred["horse"] = card["horse_name"]
    bets = select_bets(pred, strat)
    pd.set_option("display.width", 200)
    pd.set_option("display.unicode.east_asian_width", True)
    print("戦略:", json.dumps(strat.to_dict(), ensure_ascii=False))
    cols_out = ["race_id", "horse_no"] + (["horse"] if "horse" in pred else []) + \
        ["odds_win", "p_mkt", "p_fund", "p", "ev"]
    print(pred[cols_out].sort_values(["race_id", "p"], ascending=[True, False]).round(3).to_string(index=False))
    print("\n=== 買い目 (kelly = 資金に対する賭け比率) ===")
    print(bets[cols_out + ["kelly"]].round(4).to_string(index=False) if len(bets) else "該当なし (見送り)")


def main():
    for s in (sys.stdout, sys.stderr):      # Windows (cp932) でも落ちないように
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="keiba")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo"); d.add_argument("--years", type=int, default=6); d.add_argument("--seed", type=int, default=1)
    b = sub.add_parser("backtest")
    p = sub.add_parser("predict"); p.add_argument("--card"); p.add_argument("--date")
    p.add_argument("--optimize", action="store_true"); p.add_argument("--calib-months", type=int, default=12)
    c = sub.add_parser("dbcheck"); c.add_argument("--db", default="keiba.db")
    e = sub.add_parser("explore"); e.add_argument("--db", default="keiba.db")
    e.add_argument("--since", default="2015-01-01"); e.add_argument("--no-gbm", action="store_true")
    e.add_argument("--out", default="explore_report.txt")
    for x in (b, p):
        x.add_argument("--data"); x.add_argument("--db")
        x.add_argument("--since", default="2015-01-01", help="DB から読む開始日 (過去成績の集計用)")
        x.add_argument("--odds-since", default=None, help="この日以降のオッズだけ使う (既定: --since と同じ)")
    for x in (d, b):
        x.add_argument("--no-ablation", action="store_true"); x.add_argument("--out")
    for x in (d, b, p):
        x.add_argument("--slippage", type=float, default=0.0)
    a = ap.parse_args()
    if a.cmd == "demo":
        cmd_demo(a)
    elif a.cmd == "backtest":
        cmd_backtest(_load(a), a)
    elif a.cmd == "explore":
        from .explore import run
        from .sqlite_loader import load_db, load_payouts
        print("読み込み中...")
        raw = load_db(a.db, since=a.since)
        try:
            pay = load_payouts(a.db)
        except Exception as ex:            # 払戻テーブルが無くても単勝の検証は続ける
            print("払戻テーブルを読めませんでした:", ex)
            pay = None
        text = run(raw, pay, gbm=not a.no_gbm)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\nレポート -> {a.out}")
    elif a.cmd == "dbcheck":
        from .sqlite_loader import db_check
        print(db_check(a.db))
    else:
        cmd_predict(a)


if __name__ == "__main__":
    main()
