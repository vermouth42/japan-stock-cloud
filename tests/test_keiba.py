import numpy as np
import pandas as pd
import pytest

from keiba import build_features, ConditionalLogit, Strategy, select_bets, walk_forward
from keiba.betting import harville_exacta, quinella_probs, place_probs
from keiba.features import ALL_FEATURES
from keiba.model import softmax_by_group
from keiba.synthetic import generate


@pytest.fixture(scope="module")
def raw():
    return generate(n_years=3, races_per_week=20, seed=3)


@pytest.fixture(scope="module")
def feats(raw):
    return build_features(raw)


def test_no_future_leakage(raw, feats):
    """未来のレースを削っても、過去レースの特徴量は1ビットも変わらない。"""
    F, cols = feats
    cut = raw["date"].quantile(0.6)
    F_cut, _ = build_features(raw[raw["date"] <= cut])
    a = F[F["date"] <= cut].set_index(["race_id", "horse_id"])[cols].sort_index()
    b = F_cut.set_index(["race_id", "horse_id"])[cols].sort_index()
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9, atol=1e-9)


def test_results_of_race_do_not_affect_its_features(raw, feats):
    """当該レースの着順・タイムを消しても特徴量は同じ (=結果を見ていない)。"""
    F, cols = feats
    last_day = raw["date"].max()
    r2 = raw.copy()
    m = r2["date"] == last_day
    r2.loc[m, ["finish_pos", "time_sec", "last3f_sec", "corner_pos"]] = np.nan
    F2, _ = build_features(r2)
    a = F[F["date"] == last_day].set_index(["race_id", "horse_id"])[cols].sort_index()
    b = F2[F2["date"] == last_day].set_index(["race_id", "horse_id"])[cols].sort_index()
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9, atol=1e-9)


def test_features_finite(feats):
    F, cols = feats
    assert cols == ALL_FEATURES
    assert np.isfinite(F[cols].to_numpy()).all()


def test_conditional_logit_recovers_coefficients():
    rng = np.random.default_rng(0)
    n_r, n = 4000, 10
    X = rng.normal(size=(n_r * n, 3))
    rid = np.repeat(np.arange(n_r), n)
    beta = np.array([1.0, -0.5, 0.0])
    gid = rid
    p = softmax_by_group(X @ beta, gid)
    y = np.zeros(len(p))
    for r in range(n_r):
        sl = slice(r * n, (r + 1) * n)
        y[r * n + rng.choice(n, p=p[sl])] = 1
    m = ConditionalLogit(l2=1e-6).fit(X, y, rid)
    est = m.coef_ / m.scale_
    assert np.allclose(est, beta, atol=0.1)


def test_harville():
    p = np.array([0.5, 0.3, 0.2])
    E = harville_exacta(p, lam=1.0)
    assert np.isclose(E.sum(), 1.0)
    assert np.isclose(E[0, 1], 0.5 * 0.3 / 0.5)
    assert np.isclose(np.triu(quinella_probs(p)).sum(), 1.0)
    pl = place_probs(np.array([0.3, 0.25, 0.2, 0.15, 0.1]))
    assert np.isclose(pl.sum(), 3.0)


def test_select_bets_ev_filter():
    pred = pd.DataFrame({"race_id": ["a"] * 3, "p": [0.5, 0.3, 0.2],
                         "odds_win": [1.8, 4.0, 4.0], "finish_pos": [2, 1, 3]})
    b = select_bets(pred, Strategy(ev_min=1.1, min_prob=0.0, max_odds=100))
    assert list(b["p"]) == [0.3]          # EV 1.2 のみ (0.9, 0.8 は見送り)
    assert b["ret"].iloc[0] == 4.0
    assert 0 < b["kelly"].iloc[0] <= 0.10


def test_walk_forward_runs(feats):
    F, cols = feats
    per, bets, preds, model = walk_forward(F, cols, test_months=6, calib_months=6,
                                           min_train_months=12, verbose=False)
    assert len(per) >= 2
    # 融合モデルの尤度は市場単独とほぼ同等以上 (市場を事前分布に使うため大きく劣化しない)
    assert (per["ll_combined"] >= per["ll_market"] - 0.02).all()


def test_incomplete_market_is_not_bet(raw):
    """出走馬の行が欠けたレース (Σ1/オッズ が異常) は、期待値が水増しされるので買わない。"""
    rid = raw["race_id"].iloc[-1]
    fav = raw[raw["race_id"] == rid]["odds_win"].idxmin()
    F, _ = build_features(raw.drop(index=fav))
    assert not F.loc[F["race_id"] == rid, "mkt_ok"].any()
    assert F["mkt_ok"].mean() > 0.97
    pred = F[F["race_id"] == rid].assign(p=1.0)
    assert len(select_bets(pred, Strategy(ev_min=0, min_prob=0, max_odds=1e9))) == 0


def test_dnf_kept_as_loser():
    from keiba.schema import normalize
    raw = generate(n_years=1, races_per_week=5, seed=1)
    rid = raw["race_id"].iloc[0]
    i, j = raw.index[raw["race_id"] == rid][:2]
    raw.loc[i, "finish_pos"] = np.nan                      # 中止: オッズあり
    raw.loc[j, ["finish_pos", "odds_win"]] = np.nan        # 取消: オッズなし
    n0 = (raw["race_id"] == rid).sum()
    out = normalize(raw)
    r = out[out["race_id"] == rid]
    assert len(r) == n0 - 1 and r["finish_pos"].notna().all()
    assert r["finish_pos"].max() == r["finish_pos"].nlargest(2).iloc[1] + 1
