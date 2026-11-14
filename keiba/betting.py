"""買い目決定.

控除率 (単勝20%) を超えるには「当たる馬」ではなく「オッズに対して勝率が過小評価された馬」
だけを買う必要がある。

  期待値  EV = p × odds            (p: 融合モデルの勝率)
  条件    EV ≥ ev_min, p ≥ min_prob, odds ≤ max_odds
  資金配分 分数ケリー f = kelly_frac × (p·o − 1)/(o − 1), 1レース上限 max_race_frac

ev_min 等の閾値は「過去のアウトオブサンプル予測」上で、ROI の下側信頼限界
(平均 − z·標準誤差) を最大化するように選ぶ。単純な ROI 最大化は少数の大穴的中に
引きずられて過学習するため。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class Strategy:
    ev_min: float = 1.15
    min_prob: float = 0.03
    max_odds: float = 30.0
    kelly_frac: float = 0.25
    max_race_frac: float = 0.10
    slippage: float = 0.0      # 締切直前のオッズ変動・自分の投票による低下 (例 0.05 = 5%悪化)

    def to_dict(self):
        return asdict(self)


def select_bets(pred: pd.DataFrame, s: Strategy) -> pd.DataFrame:
    d = pred.copy()
    d["odds_eff"] = d["odds_win"] * (1 - s.slippage)
    d["ev_eff"] = d["p"] * d["odds_eff"]
    m = (d["ev_eff"] >= s.ev_min) & (d["p"] >= s.min_prob) & (d["odds_win"] <= s.max_odds)
    if "mkt_ok" in d:                      # オッズが全頭そろっていないレースは買わない
        m &= d["mkt_ok"].astype(bool)
    b = d[m].copy()
    o = b["odds_eff"]
    b["kelly"] = (s.kelly_frac * (b["p"] * o - 1) / (o - 1)).clip(lower=0)
    tot = b.groupby("race_id")["kelly"].transform("sum")
    b["kelly"] *= np.minimum(1.0, s.max_race_frac / tot.replace(0, np.nan)).fillna(0)
    if "finish_pos" in b:
        b["hit"] = (b["finish_pos"] == 1).astype(float)
        b["ret"] = b["hit"] * b["odds_eff"]          # 100円あたり払戻/100
    return b


def _score(bets: pd.DataFrame, min_bets: int, z: float) -> float:
    if len(bets) < min_bets:
        return -np.inf
    r = bets["ret"].to_numpy() - 1.0
    return r.mean() - z * r.std(ddof=1) / np.sqrt(len(r))


def optimize_strategy(pred: pd.DataFrame, base: Strategy | None = None,
                      min_bets: int = 200, z: float = 1.0) -> tuple[Strategy, pd.DataFrame]:
    base = base or Strategy()
    grid = {
        "ev_min": [1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.5],
        "min_prob": [0.0, 0.02, 0.05, 0.1],
        "max_odds": [10.0, 20.0, 30.0, 50.0, 100.0],
    }
    rows = []
    for ev, mp, mo in itertools.product(*grid.values()):
        s = Strategy(**{**base.to_dict(), "ev_min": ev, "min_prob": mp, "max_odds": mo})
        b = select_bets(pred, s)
        roi = b["ret"].mean() if len(b) else np.nan
        rows.append({"ev_min": ev, "min_prob": mp, "max_odds": mo, "n": len(b),
                     "roi": roi, "score": _score(b, min_bets, z)})
    table = pd.DataFrame(rows).sort_values("score", ascending=False)
    best = table.iloc[0]
    if not np.isfinite(best["score"]):
        return base, table
    return Strategy(**{**base.to_dict(), "ev_min": best["ev_min"],
                       "min_prob": best["min_prob"], "max_odds": best["max_odds"]}), table


# ------------------------------------------------- 連系馬券用 (Harville + 補正)
def harville_exacta(p: np.ndarray, lam: float = 0.81) -> np.ndarray:
    """P(i 1着, j 2着)。2着以降は勝率を lam 乗で平滑化 (Benter/Lo の補正, 人気薄の過大評価を抑える)."""
    p = np.asarray(p, float)
    q = p ** lam
    q = q / q.sum()
    n = len(p)
    M = np.zeros((n, n))
    for i in range(n):
        rest = 1.0 - q[i]
        M[i] = p[i] * q / rest
        M[i, i] = 0.0
    return M


def quinella_probs(p: np.ndarray, lam: float = 0.81) -> np.ndarray:
    """馬連: P({i,j} が1-2着)."""
    E = harville_exacta(p, lam)
    return E + E.T


def place_probs(p: np.ndarray, lam2: float = 0.81, lam3: float = 0.65) -> np.ndarray:
    """複勝 (3着以内) 確率。"""
    p = np.asarray(p, float)
    n = len(p)
    q2 = p ** lam2; q2 /= q2.sum()
    q3 = p ** lam3; q3 /= q3.sum()
    top3 = p.copy()
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            pij = p[i] * q2[j] / (1 - q2[i])
            top3[j] += pij
            for k in range(n):
                if k in (i, j):
                    continue
                top3[k] += pij * q3[k] / (1 - q3[i] - q3[j])
    return np.clip(top3, 0, 1)
