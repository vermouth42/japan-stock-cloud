"""勝率モデル.

Stage 1 (ファンダメンタル): 条件付きロジット (= McFadden / Plackett-Luce の1着部分)
    P(i 勝ち | レース r) = exp(x_i·β) / Σ_{j∈r} exp(x_j·β)
    オッズを一切使わずに馬の実力・適性・状態を評価する。

Stage 2 (市場融合, Benter 1994):
    P(i) ∝ exp(α·u_i + γ·log q_i)     u_i: Stage1 の効用, q_i: 単勝オッズから求めた市場確率
    市場は大量の情報を織り込んでいるので、それを「事前分布」とし、
    ファンダメンタルモデルが市場と異なる部分だけを上乗せする。これが回収率100%超えの核。
    Stage2 は Stage1 の学習に使っていない期間で推定する (過学習防止)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _group_index(race_ids: np.ndarray) -> np.ndarray:
    _, gid = np.unique(race_ids, return_inverse=True)
    return gid


def softmax_by_group(u: np.ndarray, gid: np.ndarray) -> np.ndarray:
    n_g = gid.max() + 1
    mx = np.full(n_g, -np.inf)
    np.maximum.at(mx, gid, u)
    e = np.exp(u - mx[gid])
    s = np.bincount(gid, weights=e, minlength=n_g)
    return e / s[gid]


class ConditionalLogit:
    def __init__(self, l2: float = 1.0, max_iter: int = 50, tol: float = 1e-8):
        self.l2, self.max_iter, self.tol = l2, max_iter, tol
        self.coef_: np.ndarray | None = None
        self.mean_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    def _std(self, X):
        return (X - self.mean_) / self.scale_

    def fit(self, X: np.ndarray, y: np.ndarray, race_ids: np.ndarray) -> "ConditionalLogit":
        X = np.asarray(X, float)
        self.mean_ = X.mean(0)
        self.scale_ = X.std(0)
        self.scale_[self.scale_ == 0] = 1.0
        Z = self._std(X)
        gid = _group_index(race_ids)
        n_g, k = gid.max() + 1, Z.shape[1]
        b = np.zeros(k)
        prev = -np.inf
        for _ in range(self.max_iter):
            p = softmax_by_group(Z @ b, gid)
            ll = np.sum(y * np.log(np.clip(p, 1e-300, None))) - 0.5 * self.l2 * b @ b
            grad = Z.T @ (y - p) - self.l2 * b
            # Hessian: Σ_r [Z_r' diag(p) Z_r - (Z_r'p)(p'Z_r)]
            Zp = Z * p[:, None]
            M = np.zeros((n_g, k))
            np.add.at(M, gid, Zp)
            H = Z.T @ Zp - M.T @ M + self.l2 * np.eye(k)
            step = np.linalg.solve(H, grad)
            # 簡易ラインサーチ
            t = 1.0
            while t > 1e-4:
                nb = b + t * step
                pn = softmax_by_group(Z @ nb, gid)
                lln = np.sum(y * np.log(np.clip(pn, 1e-300, None))) - 0.5 * self.l2 * nb @ nb
                if lln >= ll - 1e-12:
                    break
                t *= 0.5
            b = nb
            if abs(lln - prev) < self.tol * max(1.0, abs(lln)):
                break
            prev = lln
        self.coef_ = b
        self.loglik_ = lln
        return self

    def utility(self, X: np.ndarray) -> np.ndarray:
        return self._std(np.asarray(X, float)) @ self.coef_

    def predict_proba(self, X, race_ids) -> np.ndarray:
        return softmax_by_group(self.utility(X), _group_index(race_ids))


def winner_target(df: pd.DataFrame) -> np.ndarray:
    """1着=1 (同着は按分)."""
    w = (df["finish_pos"] == 1).astype(float)
    n = w.groupby(df["race_id"]).transform("sum")
    return (w / n.replace(0, np.nan)).fillna(0).to_numpy()


def trainable(df: pd.DataFrame) -> pd.Series:
    has_winner = (df["finish_pos"] == 1).groupby(df["race_id"]).transform("any")
    return has_winner & (df["field_size"] >= 5) & df["odds_win"].notna()


def log_loss_winner(p: np.ndarray, y: np.ndarray) -> float:
    """1レースあたりの平均対数尤度 (勝ち馬の log 確率)."""
    n_races = y.sum()
    return float(np.sum(y * np.log(np.clip(p, 1e-12, None))) / n_races)


class TwoStageModel:
    """Stage1 (ファンダメンタル) + Stage2 (市場融合)."""

    def __init__(self, features: list[str], l2: float = 5.0):
        self.features = features
        self.stage1 = ConditionalLogit(l2=l2)
        self.stage2 = ConditionalLogit(l2=1e-3)

    def fit(self, train: pd.DataFrame, calib: pd.DataFrame) -> "TwoStageModel":
        tr = train[trainable(train)]
        self.stage1.fit(tr[self.features].to_numpy(), winner_target(tr), tr["race_id"].to_numpy())
        ca = calib[trainable(calib)]
        Z2 = self._stage2_X(ca)
        self.stage2.fit(Z2, winner_target(ca), ca["race_id"].to_numpy())
        return self

    def _stage2_X(self, df):
        u = self.stage1.utility(df[self.features].to_numpy())
        return np.column_stack([u, np.log(df["p_mkt"].clip(1e-6).to_numpy())])

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        rid = df["race_id"].to_numpy()
        out = df[["race_id", "date", "horse_id", "horse_no", "odds_win", "p_mkt"]].copy()
        if "finish_pos" in df:
            out["finish_pos"] = df["finish_pos"]
        out["p_fund"] = self.stage1.predict_proba(df[self.features].to_numpy(), rid)
        out["p"] = self.stage2.predict_proba(self._stage2_X(df), rid)
        out["ev"] = out["p"] * out["odds_win"]
        return out

    def coefficients(self) -> pd.Series:
        return pd.Series(self.stage1.coef_, index=self.features).sort_values(key=np.abs, ascending=False)

    def market_weights(self) -> dict:
        a, g = self.stage2.coef_ / self.stage2.scale_
        return {"alpha_fund": float(a), "gamma_market": float(g)}
