"""入力データ仕様と読み込み.

1行 = 1出走 (race_id x horse)。必須列:
    race_id, date, course, surface(turf/dirt), distance, going(0良 1稍 2重 3不),
    race_class(0新馬未勝利..4OP以上), horse_id, horse_no, age, sex(M/F/G),
    weight_carried, body_weight_diff, jockey_id, trainer_id, sire_id,
    odds_win(最終単勝オッズ), finish_pos, time_sec, last3f_sec, corner_pos(最終コーナー通過順)
未来の出馬表 (予測対象) は finish_pos/time_sec/last3f_sec/corner_pos を空欄にする。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED = [
    "race_id", "date", "course", "surface", "distance", "going", "race_class",
    "horse_id", "horse_no", "age", "sex", "weight_carried", "body_weight_diff",
    "jockey_id", "trainer_id", "sire_id", "odds_win",
    "finish_pos", "time_sec", "last3f_sec", "corner_pos",
]

RESULT_COLUMNS = ["finish_pos", "time_sec", "last3f_sec", "corner_pos"]

# よくある日本語ヘッダからのマッピング
JP_COLUMNS = {
    "レースID": "race_id", "日付": "date", "場所": "course", "芝ダ": "surface",
    "距離": "distance", "馬場": "going", "クラス": "race_class", "馬ID": "horse_id",
    "馬番": "horse_no", "年齢": "age", "性別": "sex", "斤量": "weight_carried",
    "増減": "body_weight_diff", "騎手": "jockey_id", "調教師": "trainer_id",
    "父": "sire_id", "単勝": "odds_win", "着順": "finish_pos", "タイム": "time_sec",
    "上がり": "last3f_sec", "通過": "corner_pos",
}
_GOING = {"良": 0, "稍": 1, "稍重": 1, "重": 2, "不": 3, "不良": 3}
_SURF = {"芝": "turf", "ダ": "dirt", "ダート": "dirt"}
_SEX = {"牡": "M", "牝": "F", "セ": "G", "せん": "G"}
_CLASS = {"新馬": 0, "未勝利": 0, "1勝": 1, "500万": 1, "2勝": 2, "1000万": 2,
          "3勝": 3, "1600万": 3, "OP": 4, "オープン": 4, "L": 4, "G3": 4, "G2": 4, "G1": 4}


def _parse_time(v):
    """'1:34.5' -> 94.5"""
    if pd.isna(v):
        return np.nan
    s = str(v)
    if ":" in s:
        m, sec = s.split(":")
        return int(m) * 60 + float(sec)
    try:
        return float(s)
    except ValueError:
        return np.nan


def _parse_corner(v):
    """'3-3-2' -> 2 (最終コーナー)"""
    if pd.isna(v):
        return np.nan
    s = str(v).split("-")[-1]
    try:
        return float(s)
    except ValueError:
        return np.nan


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=JP_COLUMNS).copy()
    for c in RESULT_COLUMNS:          # 出馬表 (未来レース) では結果列は無くてよい
        if c not in df.columns:
            df[c] = np.nan
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"必須列が不足しています: {missing}")
    df["date"] = pd.to_datetime(df["date"])
    df["surface"] = df["surface"].replace(_SURF)
    df["going"] = pd.to_numeric(df["going"].replace(_GOING), errors="coerce").fillna(0.0)
    df["sex"] = df["sex"].replace(_SEX)
    if df["race_class"].dtype == object:
        df["race_class"] = df["race_class"].replace(_CLASS)
    df["race_class"] = pd.to_numeric(df["race_class"], errors="coerce").fillna(0)
    df["time_sec"] = df["time_sec"].map(_parse_time)
    df["corner_pos"] = df["corner_pos"].map(_parse_corner)
    for c in ["distance", "horse_no", "age", "weight_carried", "body_weight_diff",
              "odds_win", "finish_pos", "last3f_sec"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # 結果のあるレースで着順が無い馬:
    #   オッズ無し -> 取消・除外 (馬券は返還) なので行ごと除く
    #   オッズ有り -> 競走中止・失格。馬券は売られて負けているので「最下位の出走馬」として残す。
    #                 (除くと残りの馬の市場確率が水増しされ、しかも「その馬が負ける」という
    #                  未来情報を使うことになり、回収率が実際より高く出る)
    race_has_result = df.groupby("race_id")["finish_pos"].transform(lambda s: s.notna().any())
    no_fin = race_has_result & df["finish_pos"].isna()
    df = df[~(no_fin & df["odds_win"].isna())].copy()
    no_fin = no_fin.reindex(df.index)
    worst = df.groupby("race_id")["finish_pos"].transform("max")
    df.loc[no_fin, "finish_pos"] = worst[no_fin] + 1
    return df.reset_index(drop=True)


def load_csv(path: str) -> pd.DataFrame:
    return normalize(pd.read_csv(path))
