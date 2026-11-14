"""引き継ぎ資料どおりの構造のモック keiba.db を作り、読み込み〜検証〜予測まで通す。"""
import sqlite3
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from keiba import build_features, walk_forward
from keiba.sqlite_loader import load_db, classify_class, _last_corner, db_check
from keiba.synthetic import generate

CLASS_LABELS = {0: ["未勝利", "3歳未勝利", "新馬"], 1: ["1勝クラス", "500万下"],
                2: ["2勝クラス", "1000万下"], 3: ["3勝クラス", "1600万下"], 4: ["オープン", "G1", "G3"]}
ODDS_SINCE = "2021-01-01"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    raw = generate(n_years=3, races_per_week=20, seed=5, start="2019-01-05")
    rng = np.random.default_rng(0)
    path = str(tmp_path_factory.mktemp("db") / "keiba.db")
    last = raw["date"].max()
    r = raw.drop_duplicates("race_id")
    races = pd.DataFrame({
        "race_id": r["race_id"], "organization": "JRA", "date": r["date"].dt.strftime("%Y-%m-%d"),
        "racecourse": r["course"], "race_number": 1, "distance": r["distance"],
        "track_type": r["surface"].map({"turf": "芝", "dirt": "ダート"}),
        "track_condition": r["going"].map({0: "良", 1: "稍重", 2: "重", 3: "不良"}),
        "class": [rng.choice(CLASS_LABELS[c]) for c in r["race_class"]],
    })
    races.loc[races.index[5], "class"] = "障害未勝利"
    fut = raw["date"] == last                       # 最終日は「出馬表」扱い
    old = raw["date"] < ODDS_SINCE
    e = pd.DataFrame({
        "race_id": raw["race_id"], "horse_id": "馬" + raw["horse_id"],
        "jockey_id": raw["jockey_id"], "trainer_id": raw["trainer_id"],
        "waku": 1, "umaban": raw["horse_no"], "kinryo": raw["weight_carried"],
        "finish_position": raw["finish_pos"].where(~fut).astype("object"),
        "time": raw["time_sec"].where(~fut), "agari_3f": raw["last3f_sec"].where(~fut),
        "passing_order": ("5-4-(" + raw["corner_pos"].astype(str) + ")").where(~fut),
        "ninki": 1, "odds": raw["odds_win"].where(~fut), "age": raw["age"],
        "weight_diff": raw["body_weight_diff"].where(raw["date"] >= "2021-06-01"),
    })
    e.loc[e.index[::97], "finish_position"] = 0     # 競走中止 (オッズあり・着順なし)
    e.loc[e.index[1::97], ["finish_position", "odds"]] = None   # 取消・除外 (オッズなし)
    e.loc[fut, "finish_position"] = None
    h = raw.drop_duplicates("horse_id")
    horses = pd.DataFrame({"horse_id": "馬" + h["horse_id"], "name": "馬" + h["horse_id"],
                           "sex": h["sex"].map({"M": "牡", "F": "牝"}), "sire": h["sire_id"],
                           "broodmare_sire": None})
    horses.loc[horses.index[::3], "sire"] = None        # 血統未取得の馬
    e.loc[raw["date"] >= "2021-03-01", "trainer_id"] = None   # 調教師が未補完の期間
    snap = raw.loc[fut, ["race_id", "odds_win"]].rename(columns={"odds_win": "odds"})
    snap["umaban"] = raw.loc[fut, "horse_no"]
    snap["fetched_at"] = "x"
    e["body_weight"] = np.where(raw["date"] >= "2021-06-01", 470 + raw["body_weight_diff"], np.nan)
    e["train_grade"] = np.where(raw["date"] >= "2021-06-01", rng.choice(["A", "B", "C"], len(raw)), None)
    e["prize"] = np.where(raw["finish_pos"] <= 5, 600 - 100 * raw["finish_pos"], 0.0)
    horses["broodmare_sire"] = ("BMS" + h["sire_id"]).to_numpy()
    recent = raw["date"] >= "2021-06-01"
    gear = pd.DataFrame({"race_id": raw.loc[recent, "race_id"], "horse_id": 1,
                         "blinker": rng.integers(0, 2, recent.sum()), "sex": "牡",
                         "horse_name": "馬" + raw.loc[recent, "horse_id"]})
    # 払戻: 単勝・複勝・馬連・ワイド
    res = raw[~fut].sort_values(["race_id", "finish_pos"])
    pay = []
    for rid, g in res.groupby("race_id"):
        no, od = g["horse_no"].to_numpy(), g["odds_win"].to_numpy()
        pay.append((rid, "単勝", str(no[0]), int(od[0] * 100)))
        for k in range(3):
            pay.append((rid, "複勝", str(no[k]), int(max(110, od[k] * 30))))
        pay.append((rid, "馬連", f"{no[0]}-{no[1]}", int(od[0] * od[1] * 60)))
        for a_, b_ in ((0, 1), (0, 2), (1, 2)):
            pay.append((rid, "ワイド", f"{no[a_]}-{no[b_]}", int(od[a_] * od[b_] * 20)))
        pay.append((rid, "3連複", f"{no[0]}-{no[1]}-{no[2]}", int(od[0] * od[1] * od[2] * 15)))
        pay.append((rid, "三連単", f"{no[0]}→{no[1]}→{no[2]}", int(od[0] * od[1] * od[2] * 90)))
    pay = pd.DataFrame(pay, columns=["race_id", "bet_type", "combo", "payout"])
    conn = sqlite3.connect(path)
    for name, t in [("races", races), ("entries", e), ("horses", horses), ("odds_snapshot", snap),
                    ("race_gear", gear), ("payouts", pay)]:
        t.to_sql(name, conn, index=False)
    conn.close()
    return path, raw


def test_helpers():
    assert [classify_class(x) for x in ["3歳未勝利", "500万下", "2勝クラス", "1600万下", "G1", "オープン"]] \
        == [0, 1, 2, 3, 4, 4]
    assert _last_corner("3-3-(2)-*1") == 1
    assert np.isnan(_last_corner(None))


def test_load_db(db):
    path, raw = db
    df = load_db(path, since="2000-01-01", odds_since=ODDS_SINCE)
    assert df["finish_pos"].notna().all() and (df["finish_pos"] > 0).all()   # 取消除外・出馬表は除外
    n_dnf = sqlite3.connect(path).execute(
        "SELECT COUNT(*) FROM entries WHERE CAST(finish_position AS INTEGER) = 0 "
        "AND finish_position IS NOT NULL").fetchone()[0]
    assert n_dnf > 0 and len(df) > 0                                          # 中止馬は残る
    conn = sqlite3.connect(path)
    jump = raw["race_id"].unique()[5]
    last = raw["date"].max().strftime("%Y-%m-%d")
    expected = conn.execute(
        "SELECT COUNT(*) FROM entries e JOIN races r USING(race_id) WHERE r.date < ? AND e.race_id != ? "
        "AND NOT (e.finish_position IS NULL AND e.odds IS NULL)", (last, jump)).fetchone()[0]
    assert len(df) == expected                      # 取消だけが消え、中止馬は最下位扱いで残る
    assert not df["race_id"].isin([raw["race_id"].unique()[5]]).any()          # 障害は除外
    assert df.loc[df["date"] < ODDS_SINCE, "odds_win"].isna().all()           # 古いオッズは使わない
    assert df.loc[df["date"] >= ODDS_SINCE, "odds_win"].notna().all()
    assert set(df["surface"]) == {"turf", "dirt"} and set(df["sex"]) <= {"M", "F", "G"}
    assert df["sire_id"].isna().any() and df["trainer_id"].isna().any()       # 不明は NaN のまま
    assert df["horse_id"].str.contains("_").all()                              # 馬名_生年
    fut = load_db(path, since="2000-01-01", odds_since=ODDS_SINCE, include_future=True)
    card = fut[fut["date"] == raw["date"].max()]
    assert len(card) and card["finish_pos"].isna().all() and card["odds_win"].notna().all()


def test_backtest_and_predict_on_db(db):
    path, _ = db
    df = load_db(path, since="2000-01-01", odds_since=ODDS_SINCE)
    F, cols = build_features(df)
    assert np.isfinite(F[cols].to_numpy()).all()
    per, bets, preds, _ = walk_forward(F, cols, verbose=False)
    assert len(per) >= 1 and preds["date"].min() >= pd.Timestamp(ODDS_SINCE) + pd.DateOffset(months=6)
    assert "races" in db_check(path)
    out = subprocess.run([sys.executable, "-m", "keiba", "predict", "--db", path, "--since", "2000-01-01",
                          "--odds-since", ODDS_SINCE, "--calib-months", "6"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "買い目" in out.stdout


def test_explore_on_db(db):
    from keiba.explore import run
    from keiba.features import EXTRA_FEATURES
    from keiba.sqlite_loader import load_payouts
    path, raw = db
    df = load_db(path, since="2000-01-01")
    pay = load_payouts(path)
    assert set(pay["bet_type"]) == {"単勝", "複勝", "馬連", "ワイド", "3連複", "3連単"}
    assert all(isinstance(c, tuple) for c in pay["combo"])
    F, cols = build_features(df, extra=True)
    assert set(EXTRA_FEATURES) <= set(cols) and np.isfinite(F[cols].to_numpy()).all()
    # 追加ファクターも未来リークしない
    cut = df["date"].quantile(0.93)
    F2, cols2 = build_features(df[df["date"] <= cut], extra=True)
    assert cols2 == cols
    a = F[F["date"] <= cut].set_index(["race_id", "horse_id"])[cols].sort_index()
    b = F2.set_index(["race_id", "horse_id"])[cols].sort_index()
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9, atol=1e-9)
    text = run(df, pay, log=lambda s: None)
    for key in ("[1]", "[2]", "[3]", "[5]", "[6]", "D GBM", "ワイドBOX", "3連単流し", "市場(人気順)"):
        assert key in text


def test_pattern_settlement():
    """8 通りの買い方の精算を手計算と突き合わせる。"""
    from keiba.explore import pattern_rows
    # 指数順 = 馬番 1,2,3,4,5,6。着順は 1着=馬番2, 2着=馬番1, 3着=馬番4
    preds = pd.DataFrame({"race_id": "r", "horse_no": [1, 2, 3, 4, 5, 6],
                          "p": [0.4, 0.25, 0.15, 0.1, 0.06, 0.04], "p_mkt": [0.3, 0.3, 0.1, 0.1, 0.1, 0.1],
                          "odds_win": [2.0, 3.0, 8.0, 9.0, 10.0, 11.0],
                          "finish_pos": [2, 1, 4, 3, 5, 6], "mkt_ok": True})
    pay = pd.DataFrame([("r", "単勝", (2,), 3.0), ("r", "複勝", (2,), 1.3), ("r", "複勝", (1,), 1.2),
                        ("r", "複勝", (4,), 2.0), ("r", "馬連", (1, 2), 5.0), ("r", "ワイド", (1, 2), 2.0),
                        ("r", "ワイド", (2, 4), 6.0), ("r", "ワイド", (1, 4), 4.0),
                        ("r", "3連複", (1, 2, 4), 20.0), ("r", "3連単", (2, 1, 4), 90.0)],
                       columns=["race_id", "bet_type", "combo", "ret"])
    d = pattern_rows(preds, pay).set_index("pattern")
    got = {k[0]: (v["stake"], v["ret"]) for k, v in d.iterrows()}
    assert got["1"] == (5000, 0)                 # 指数1位(馬番1)は2着 -> 単勝外れ
    assert got["2"] == (5000, 6000)              # 複勝 1.2倍
    assert got["3"] == (7500, 12500)             # 馬連 1-2 的中 2500×5
    assert got["4"] == (7500, 50000)             # 3連複 1-2-4 的中 2500×20
    assert got["5"] == (7500, 12500)             # 馬連BOX 1-2
    assert got["6"] == (7500, 5000)              # ワイドBOX(1,2,3): 1-2 のみ 2500×2
    assert got["7"] == (8000, 40000)             # 3連複BOX(1-4): 1-2-4 2000×20
    assert got["8"] == (9000, 0)                 # 3連単流しは指数1位が1着でないので外れ
