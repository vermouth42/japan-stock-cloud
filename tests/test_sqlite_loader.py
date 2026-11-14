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
    conn = sqlite3.connect(path)
    for name, t in [("races", races), ("entries", e), ("horses", horses), ("odds_snapshot", snap)]:
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
