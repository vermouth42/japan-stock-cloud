"""ウォークフォワード検証.

各テスト期間 T について:
    [開始, T−calib)        Stage1 学習
    [T−calib, T)           Stage2 (市場融合) 学習
    T                      予測 (完全アウトオブサンプル)
買い目閾値は「T より前のアウトオブサンプル予測」だけで最適化してから T に適用する。
これにより検証結果に未来情報が一切混入しない。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .betting import Strategy, select_bets, optimize_strategy
from .model import TwoStageModel, trainable, winner_target, log_loss_winner


def walk_forward(df: pd.DataFrame, features: list[str], test_months: int = 6,
                 calib_months: int = 6, min_train_months: int = 18,
                 base_strategy: Strategy | None = None, l2: float = 5.0,
                 verbose: bool = True, make_model=None):
    base_strategy = base_strategy or Strategy()
    start = df["date"].min().normalize()
    end = df["date"].max()
    t = start + pd.DateOffset(months=min_train_months + calib_months)
    # オッズのある期間が calib_months 分たまってから賭け始める (それ以前は成績集計と Stage1 学習のみ)
    odds_start = df.loc[df["odds_win"].notna() & df["finish_pos"].notna(), "date"].min().normalize()
    t = max(t, odds_start + pd.DateOffset(months=calib_months))
    preds, periods, bets_all = [], [], []
    model = None
    while t <= end:
        t_end = t + pd.DateOffset(months=test_months)
        c0 = t - pd.DateOffset(months=calib_months)
        train = df[df["date"] < c0]
        calib = df[(df["date"] >= c0) & (df["date"] < t)]
        test = df[(df["date"] >= t) & (df["date"] < t_end)]
        test = test[trainable(test)]
        if len(test) == 0:
            break
        model = (make_model() if make_model else TwoStageModel(features, l2=l2)).fit(train, calib)
        pt = model.predict(test)

        if preds:
            strat, _ = optimize_strategy(pd.concat(preds), base_strategy)
        else:
            strat = base_strategy
        b = select_bets(pt, strat)
        bets_all.append(b)
        preds.append(pt)

        ok = trainable(test).to_numpy()
        y = winner_target(test[ok])
        info = {
            "period": f"{t.date()}~{(t_end - pd.Timedelta(days=1)).date()}",
            "races": int(test.loc[ok, "race_id"].nunique()),
            "ll_market": log_loss_winner(pt.loc[ok, "p_mkt"].to_numpy(), y),
            "ll_fund": log_loss_winner(pt.loc[ok, "p_fund"].to_numpy(), y),
            "ll_combined": log_loss_winner(pt.loc[ok, "p"].to_numpy(), y),
            "ev_min": strat.ev_min, "min_prob": strat.min_prob, "max_odds": strat.max_odds,
            "bets": len(b), "hit_rate": b["hit"].mean() if len(b) else np.nan,
            "roi": b["ret"].mean() if len(b) else np.nan,
            **model.market_weights(),
        }
        periods.append(info)
        if verbose:
            print(f"  {info['period']}  races={info['races']:5d} bets={info['bets']:5d} "
                  f"ROI={info['roi']:.3f}  LL mkt={info['ll_market']:.4f} comb={info['ll_combined']:.4f}")
        t = t_end
    return pd.DataFrame(periods), pd.concat(bets_all) if bets_all else pd.DataFrame(), \
        pd.concat(preds) if preds else pd.DataFrame(), model


def _max_drawdown(curve: np.ndarray) -> float:
    peak = np.maximum.accumulate(curve)
    return float(((peak - curve) / peak).max()) if len(curve) else 0.0


def simulate_bankroll(bets: pd.DataFrame, bankroll: float = 1_000_000, unit: int = 100,
                      max_bet: float = 50_000):
    """分数ケリーで資金推移を計算 (100円単位)。

    パリミュチュエル方式では自分の投票でオッズが下がるため、1点あたり max_bet で上限を設ける。
    """
    bets = bets.sort_values(["date", "race_id"])
    bank = bankroll
    curve = [bank]
    for _, g in bets.groupby(["date", "race_id"], sort=False):
        stakes = np.minimum(np.floor(g["kelly"].to_numpy() * bank / unit) * unit, max_bet)
        bank += float(np.sum(stakes * g["ret"].to_numpy()) - stakes.sum())
        curve.append(bank)
    return np.array(curve)


def bootstrap_roi(bets: pd.DataFrame, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """レース単位リサンプリングで均等買い ROI の95%信頼区間."""
    g = bets.groupby("race_id").agg(stake=("ret", "size"), ret=("ret", "sum"))
    s, r = g["stake"].to_numpy(), g["ret"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(n, len(g)))
    rois = r[idx].sum(1) / s[idx].sum(1)
    return float(np.percentile(rois, 2.5)), float(np.percentile(rois, 97.5))


def summarize(periods: pd.DataFrame, bets: pd.DataFrame, preds: pd.DataFrame) -> dict:
    out = {"bets": len(bets), "races_bet": bets["race_id"].nunique() if len(bets) else 0}
    if len(bets) == 0:
        return out
    out["hit_rate"] = float(bets["hit"].mean())
    out["roi_flat"] = float(bets["ret"].mean())
    out["roi_ci95"] = bootstrap_roi(bets)
    curve = simulate_bankroll(bets)
    out["kelly_final_multiple"] = float(curve[-1] / curve[0])
    out["kelly_max_drawdown"] = _max_drawdown(curve)
    bands = pd.cut(bets["odds_win"], [1, 3, 6, 10, 20, 50, 1000])
    out["by_odds"] = bets.groupby(bands, observed=True).agg(
        n=("ret", "size"), hit=("hit", "mean"), roi=("ret", "mean"))
    # 参考: 全馬均等買い / 1番人気買い の ROI (市場の控除率の確認)
    p = preds.dropna(subset=["finish_pos"])
    out["roi_all_horses"] = float(((p["finish_pos"] == 1) * p["odds_win"]).mean())
    fav = p.loc[p.groupby("race_id")["odds_win"].idxmin()]
    out["roi_favorite"] = float(((fav["finish_pos"] == 1) * fav["odds_win"]).mean())
    return out


def factor_ablation(df: pd.DataFrame, groups: dict[str, list[str]], train_months: int = 24,
                    calib_months: int = 6, test_months: int = 12, l2: float = 5.0) -> pd.DataFrame:
    """ファクター群を1つずつ外したときの、テスト期間の対数尤度 (市場比) の低下量.

    値が大きいほど「市場に織り込まれていない情報」をそのファクターが持っている。
    """
    start = df["date"].min().normalize()
    odds_start = df.loc[df["odds_win"].notna() & df["finish_pos"].notna(), "date"].min().normalize()
    c0 = max(start + pd.DateOffset(months=train_months), odds_start)
    t0 = c0 + pd.DateOffset(months=calib_months)
    t1 = t0 + pd.DateOffset(months=test_months)
    train, calib = df[df["date"] < c0], df[(df["date"] >= c0) & (df["date"] < t0)]
    test = df[(df["date"] >= t0) & (df["date"] < t1)]
    test = test[trainable(test)]
    y = winner_target(test)
    ll_mkt = log_loss_winner(test["p_mkt"].to_numpy(), y)

    def run(feats):
        m = TwoStageModel(feats, l2=l2).fit(train, calib)
        return log_loss_winner(m.predict(test)["p"].to_numpy(), y)

    all_f = [f for g in groups.values() for f in g]
    full = run(all_f)
    rows = [{"group": "(全ファクター)", "ll_gain_vs_market": full - ll_mkt, "drop_cost": 0.0}]
    for g, fs in groups.items():
        ll = run([f for f in all_f if f not in fs])
        rows.append({"group": g, "ll_gain_vs_market": ll - ll_mkt, "drop_cost": full - ll})
    return pd.DataFrame(rows)
