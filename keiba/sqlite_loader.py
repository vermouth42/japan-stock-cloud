"""keiba_app の SQLite (keiba.db) -> 本パッケージのスキーマへの変換.

引き継ぎ資料の構造に対応:
  * entries.horse_id / jockey_id / trainer_id は「名前」。同名馬の混同を避けるため
    馬キーは「馬名_生年 (開催年 - 年齢)」にする。
  * races.class は表記ゆれあり ('1勝クラス' / '500万下' ...) -> 0..4 に正規化。障害は除外。
  * entries.odds は確定オッズで、信頼できるのは netkeiba 期間 (既定 2021-08-01 以降)。
    それ以前のオッズは使わず (NaN)、過去成績の集計とファンダメンタル学習にだけ使う。
  * 取消・除外・中止 (finish_position NULL/0) は結果のあるレースから除く。
  * 結果がまだ無いレース (出馬表) は include_future=True のときだけ残し、
    単勝オッズが無ければ odds_snapshot の値で補う。
DB には SELECT しか発行しない。
"""
from __future__ import annotations

import re
import sqlite3

import numpy as np
import pandas as pd

from .schema import normalize

_E_COLS = ["race_id", "horse_id", "jockey_id", "trainer_id", "waku", "umaban", "kinryo",
           "finish_position", "time", "agari_3f", "passing_order", "odds", "age",
           "weight_diff"]
_R_COLS = ["race_id", "date", "racecourse", "distance", "track_type", "track_condition", "class"]
_H_COLS = ["horse_id", "sex", "sire", "broodmare_sire"]


def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _sel(alias: str, wanted: list[str], have: set[str]) -> str:
    """実物に無い列は NULL で埋める (資料と実DBの差を吸収)。"""
    return ", ".join(f"{alias}.{c} AS {alias}_{c}" if c in have else f"NULL AS {alias}_{c}"
                     for c in wanted)


def classify_class(label) -> float:
    """クラス表記 -> 0:新馬/未勝利 1:1勝 2:2勝 3:3勝 4:OP・重賞"""
    s = str(label)
    if "新馬" in s or "未勝利" in s or "未出走" in s:
        return 0
    if "1勝" in s or "１勝" in s or "500万" in s or "400万" in s:
        return 1
    if "2勝" in s or "２勝" in s or "1000万" in s or "900万" in s:
        return 2
    if "3勝" in s or "３勝" in s or "1600万" in s or "1500万" in s:
        return 3
    return 4


def _last_corner(v) -> float:
    """'3-3-2-1' -> 1。括弧や * が混じっても数字だけ拾う。"""
    if v is None:
        return np.nan
    nums = re.findall(r"\d+", str(v))
    return float(nums[-1]) if nums else np.nan


def load_db(path: str = "keiba.db", since: str = "2015-01-01",
            odds_since: str = "2021-08-01", include_future: bool = False) -> pd.DataFrame:
    conn = sqlite3.connect(path, timeout=15)
    try:
        ec, rc, hc = _cols(conn, "entries"), _cols(conn, "races"), _cols(conn, "horses")
        q = f"""
            SELECT {_sel('e', _E_COLS, ec)}, {_sel('r', _R_COLS, rc)}, {_sel('h', _H_COLS, hc)}
            FROM entries e
            JOIN races r ON e.race_id = r.race_id
            LEFT JOIN horses h ON e.horse_id = h.horse_id
            WHERE r.date >= ? AND r.class NOT LIKE '%障害%'
        """
        d = pd.read_sql_query(q, conn, params=[since])
        snap = None
        if include_future and "odds_snapshot" in {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}:
            snap = pd.read_sql_query("SELECT race_id, umaban, odds FROM odds_snapshot", conn)
    finally:
        conn.close()

    num = lambda s: pd.to_numeric(s, errors="coerce")
    date = pd.to_datetime(d["r_date"], errors="coerce")
    age = num(d["e_age"])
    fin = num(d["e_finish_position"])
    fin = fin.where(fin > 0)
    time = num(d["e_time"]); time = time.where(time > 0)
    l3 = num(d["e_agari_3f"]); l3 = l3.where(l3 > 0)
    odds = num(d["e_odds"]); odds = odds.where(odds >= 1.0)
    name = d["e_horse_id"].astype(str)
    birth = (date.dt.year - age).round()
    horse_key = np.where(birth.notna(), name + "_" + birth.fillna(0).astype(int).astype(str), name)

    out = pd.DataFrame({
        "race_id": d["e_race_id"].astype(str), "date": date,
        "course": d["r_racecourse"], "surface": d["r_track_type"],
        "distance": num(d["r_distance"]), "going": d["r_track_condition"].fillna("良"),
        "race_class": d["r_class"].map(classify_class),
        "horse_id": horse_key, "horse_name": name, "horse_no": num(d["e_umaban"]),
        "age": age, "sex": d["h_sex"].fillna("牡"),
        "weight_carried": num(d["e_kinryo"]), "body_weight_diff": num(d["e_weight_diff"]),
        "jockey_id": d["e_jockey_id"].fillna("不明"), "trainer_id": d["e_trainer_id"].fillna("不明"),
        "sire_id": d["h_sire"].fillna("不明"),
        "odds_win": odds, "finish_pos": fin, "time_sec": time, "last3f_sec": l3,
        "corner_pos": d["e_passing_order"].map(_last_corner),
    })
    out = out[out["date"].notna() & out["horse_no"].notna() & out["distance"].notna()]

    has_result = out.groupby("race_id")["finish_pos"].transform(lambda s: s.notna().any())
    if include_future:
        if snap is not None and len(snap):
            snap["race_id"] = snap["race_id"].astype(str)
            snap = snap.drop_duplicates(["race_id", "umaban"], keep="last")
            m = out.merge(snap, left_on=["race_id", "horse_no"], right_on=["race_id", "umaban"],
                          how="left")["odds"].to_numpy()
            out["odds_win"] = out["odds_win"].where(has_result | out["odds_win"].notna(),
                                                    pd.to_numeric(pd.Series(m, index=out.index), errors="coerce"))
    else:
        out = out[has_result]
        has_result = has_result[has_result]
    # 信頼できない期間の確定オッズは使わない
    old = has_result.reindex(out.index).fillna(False) & (out["date"] < pd.Timestamp(odds_since))
    out.loc[old, "odds_win"] = np.nan
    return normalize(out)


def db_check(path: str = "keiba.db") -> str:
    """年別の件数と各列の充足率 (このパッケージで使う列のみ)。"""
    df = load_db(path, since="1900-01-01", odds_since="1900-01-01")
    df["year"] = df["date"].dt.year
    g = df.groupby("year")
    t = pd.DataFrame({
        "races": g["race_id"].nunique(), "rows": g.size(),
        "odds": g["odds_win"].apply(lambda s: s.notna().mean()),
        "time": g["time_sec"].apply(lambda s: s.notna().mean()),
        "agari": g["last3f_sec"].apply(lambda s: s.notna().mean()),
        "corner": g["corner_pos"].apply(lambda s: s.notna().mean()),
        "bw_diff": g["body_weight_diff"].apply(lambda s: s.notna().mean()),
        "trainer": g["trainer_id"].apply(lambda s: (s != "不明").mean()),
        "sire": g["sire_id"].apply(lambda s: (s != "不明").mean()),
    })
    w = df[df["finish_pos"] == 1]
    t["win_roi_all"] = (w.groupby("year")["odds_win"].sum() / g["odds_win"].count()).round(3)
    return t.round(3).to_string()
