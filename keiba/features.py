"""多ファクター特徴量生成.

原則:
  * 全ての特徴量は「そのレースの日付より前」の情報のみで作る (未来リーク禁止)。
  * 条件付きロジットはレース内の相対比較しか効かないため、馬レベルの値は
    レース内 z スコア化し、レース共通の条件 (ペース・馬場) は馬レベル値との交互作用で入れる。

ファクター群 (FEATURE_GROUPS):
  ability     能力: スピード指数 (基準タイム+馬場差補正), 上がり, 着順率
  aptitude    適性: 馬自身の芝ダ/距離/コース/道悪実績, 父系の芝ダ・距離 A/E
  connections 人: 騎手・調教師・コンビの A/E (実績/期待), 市場比 A/E, 乗り替わり
  condition   状態: 間隔, 休み明け, 馬体重増減, キャリア, 年齢, 性, 斤量
  context     条件替わり: 昇降級, 距離変更
  pace_draw   展開と枠: 脚質, 脚質xペース圧力, コース別枠順バイアス
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIST_BINS = [0, 1400, 1800, 2200, 10_000]

FEATURE_GROUPS: dict[str, list[str]] = {
    "ability": ["fig_last", "fig_m5", "fig_best5", "fig_ewm", "l3f_m5",
                "fin_pct_m5", "fin_pct_last", "top3_rate"],
    "aptitude": ["apt_surface", "apt_dist", "apt_course", "heavy_inter",
                 "sire_surf_ae", "sire_dist_ae", "dist_fit", "sire_dist_fit"],
    "connections": ["jockey_ae", "jockey_mkt_ae", "trainer_ae", "trainer_mkt_ae",
                    "jt_ae", "jockey_upgrade"],
    "condition": ["log_days", "layoff", "bw_diff", "abs_bw_diff", "n_runs_log",
                  "age", "female", "weight_carried", "no_fig"],
    "context": ["class_change", "dist_change_abs"],
    "pace_draw": ["style", "pace_inter", "draw_ae"],
}
ALL_FEATURES = [f for g in FEATURE_GROUPS.values() for f in g]
# z スコア化しない列 (フラグや既に交互作用済みのもの)
_RAW = {"no_fig", "female", "layoff", "pace_inter", "heavy_inter"}


# ---------------------------------------------------------------- helpers
def _prior_mean(df: pd.DataFrame, keys: list[str], col: str) -> pd.Series:
    """df は keys 内で日付順に並んでいる前提。自分より前の行の col 平均。"""
    v = df[col]
    val = v.fillna(0.0)
    has = v.notna().astype(float)
    g_val = val.groupby([df[k] for k in keys])
    g_has = has.groupby([df[k] for k in keys])
    s = g_val.cumsum() - val
    n = g_has.cumsum() - has
    return (s / n.replace(0, np.nan))


def _prior_ae(df: pd.DataFrame, keys: list[str], actual: str, expected: str,
              k: float = 5.0) -> pd.Series:
    """keys 単位の (実績勝利数 + k) / (期待勝利数 + k) を前日までの累計で計算し log を返す。

    期待値に 1/頭数 を使えば「平均的な馬に比べてどれだけ勝つか」、
    市場確率を使えば「市場の評価以上に勝つか (=妙味)」を測れる。
    """
    tmp = df[keys + ["date"]].copy()
    valid = df[actual].notna() & df[expected].notna()   # オッズ無し期間は市場比 A/E に数えない
    tmp["_a"] = df[actual].where(valid, 0.0)
    tmp["_e"] = df[expected].where(valid, 0.0)
    g = tmp.groupby(keys + ["date"], sort=False)[["_a", "_e"]].sum().reset_index()
    g = g.sort_values("date", kind="mergesort")
    cum = g.groupby(keys, sort=False)[["_a", "_e"]].cumsum() - g[["_a", "_e"]]
    g["_ae"] = np.log((cum["_a"] + k) / (cum["_e"] + k))
    out = tmp[keys + ["date"]].merge(g[keys + ["date", "_ae"]], on=keys + ["date"], how="left")
    return pd.Series(out["_ae"].to_numpy(), index=df.index)


def _race_z(df: pd.DataFrame, col: str) -> pd.Series:
    g = df.groupby("race_id")[col]
    mu = g.transform("mean")
    sd = g.transform("std")
    z = (df[col] - mu) / sd.replace(0, np.nan)
    return z.fillna(0.0).clip(-4, 4)


# ---------------------------------------------------------------- steps
def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "race_id", "horse_no"], kind="mergesort").reset_index(drop=True)
    df["field_size"] = df.groupby("race_id")["horse_id"].transform("size")
    fs1 = (df["field_size"] - 1).clip(lower=1)
    df["won"] = (df["finish_pos"] == 1).astype(float).where(df["finish_pos"].notna())
    df["top3"] = (df["finish_pos"] <= 3).astype(float).where(df["finish_pos"].notna())
    df["fin_pct"] = (df["finish_pos"] - 1) / fs1
    df["corner_pct"] = (df["corner_pos"] - 1) / fs1
    df["exp_win"] = 1.0 / df["field_size"]
    inv = 1.0 / df["odds_win"]
    df["p_mkt"] = inv / inv.groupby(df["race_id"]).transform("sum")
    df["dist_band"] = pd.cut(df["distance"], DIST_BINS, labels=False)
    df["heavy_now"] = (df["going"] >= 2).astype(float)
    df["gate_pct"] = (df["horse_no"] - 1) / fs1
    df["gate_zone"] = np.minimum((df["gate_pct"] * 3).astype(int), 2)
    return df


def _speed_figures(df: pd.DataFrame) -> pd.DataFrame:
    """スピード指数: コース・距離別基準タイム(過去分のみ)からの偏差を、当日の馬場差で補正。"""
    race = df.groupby("race_id", sort=False).agg(
        date=("date", "first"), course=("course", "first"), surface=("surface", "first"),
        distance=("distance", "first"), race_class=("race_class", "first"),
        med=("time_sec", "median"), l3=("last3f_sec", "median")).reset_index()
    race = race.sort_values("date", kind="mergesort").reset_index(drop=True)

    def stats(keys):
        mean = _prior_mean(race, keys, "med")
        race["_sq"] = race["med"] ** 2
        msq = _prior_mean(race, keys, "_sq")
        n = race["med"].notna().astype(float).groupby([race[k] for k in keys]).cumsum() \
            - race["med"].notna().astype(float)
        sd = np.sqrt((msq - mean ** 2).clip(lower=1e-4))
        ok = n >= 5
        return mean.where(ok), sd.where(ok)

    m1, s1 = stats(["course", "surface", "distance"])
    m2, s2 = stats(["surface", "distance"])
    race["std_mean"] = m1.fillna(m2)
    race["std_sd"] = s1.fillna(s2)
    race["z"] = (race["med"] - race["std_mean"]) / race["std_sd"]
    # クラス差を除いた上で当日・同場・同コース種別の平均ズレ = 馬場差
    race["class_z"] = _prior_mean(race, ["race_class"], "z").fillna(0.0)
    race["_resid"] = race["z"] - race["class_z"]
    race["variant"] = race.groupby(["date", "course", "surface"])["_resid"].transform("mean")

    d = df.merge(race[["race_id", "std_mean", "std_sd", "variant", "l3"]], on="race_id", how="left")
    d.index = df.index
    df["fig"] = -((d["time_sec"] - d["std_mean"]) / d["std_sd"] - d["variant"].fillna(0)) * 10
    df["l3f_rel"] = d["l3"] - df["last3f_sec"]  # 上がりがレース中央値より速いほど +
    return df


def _horse_history(df: pd.DataFrame) -> pd.DataFrame:
    h = df.sort_values(["horse_id", "date"], kind="mergesort")
    gh = h.groupby("horse_id", sort=False)
    hid = h["horse_id"]

    def roll(col, w, fn):
        s = gh[col].shift(1)
        r = s.groupby(hid).rolling(w, min_periods=1)
        return getattr(r, fn)().reset_index(level=0, drop=True)

    out = pd.DataFrame(index=h.index)
    out["fig_last"] = gh["fig"].shift(1)
    out["fig_m5"] = roll("fig", 5, "mean")
    out["fig_best5"] = roll("fig", 5, "max")
    out["fig_ewm"] = gh["fig"].transform(lambda x: x.shift(1).ewm(halflife=2, ignore_na=True).mean())
    out["l3f_m5"] = roll("l3f_rel", 5, "mean")
    out["fin_pct_m5"] = -roll("fin_pct", 5, "mean")    # 符号反転: 大きいほど良い
    out["fin_pct_last"] = -gh["fin_pct"].shift(1)
    out["style"] = roll("corner_pct", 5, "mean")         # 0=逃げ 1=追込

    n_prev = gh.cumcount()
    cum_top3 = gh["top3"].transform(lambda x: x.fillna(0).cumsum()) - h["top3"].fillna(0)
    out["top3_rate"] = (cum_top3 + 0.6) / (n_prev + 3)
    out["n_runs_log"] = np.log1p(n_prev)

    days = (h["date"] - gh["date"].shift(1)).dt.days
    out["log_days"] = np.log1p(days.fillna(0))
    out["layoff"] = (days > 180).astype(float)
    out["class_change"] = (h["race_class"] - gh["race_class"].shift(1)).fillna(0)
    out["dist_change_abs"] = (h["distance"] - gh["distance"].shift(1)).abs().fillna(0)
    prev_j = gh["jockey_id"].shift(1)
    out["_jchange"] = (prev_j.notna() & (prev_j != h["jockey_id"])).astype(float)

    # 条件別の自己実績: 同条件での平均指数 - 全体平均指数
    base = _prior_mean(h, ["horse_id"], "fig")
    out["apt_surface"] = (_prior_mean(h, ["horse_id", "surface"], "fig") - base).fillna(0)
    out["apt_dist"] = (_prior_mean(h, ["horse_id", "dist_band"], "fig") - base).fillna(0)
    out["apt_course"] = (_prior_mean(h, ["horse_id", "course"], "fig") - base).fillna(0)
    # 距離適性: 好走(上位1/3)した距離の平均と今回距離の差 (二次的な効き方を想定し二乗)
    h_good = h.assign(_gd=h["distance"].where(h["fin_pct"] <= 0.33))
    best_d = _prior_mean(h_good, ["horse_id"], "_gd")
    out["dist_fit"] = -((h["distance"] - best_d) / 400) ** 2
    out["_apt_heavy"] = (_prior_mean(h, ["horse_id", "heavy_now"], "fig") - base).fillna(0)
    return df.join(out)


def _connections(df: pd.DataFrame) -> pd.DataFrame:
    df["jockey_ae"] = _prior_ae(df, ["jockey_id"], "won", "exp_win", k=10)
    df["jockey_mkt_ae"] = _prior_ae(df, ["jockey_id"], "won", "p_mkt", k=20)
    df["trainer_ae"] = _prior_ae(df, ["trainer_id"], "won", "exp_win", k=10)
    df["trainer_mkt_ae"] = _prior_ae(df, ["trainer_id"], "won", "p_mkt", k=20)
    df["jt_ae"] = _prior_ae(df, ["jockey_id", "trainer_id"], "won", "exp_win", k=5)
    df["sire_surf_ae"] = _prior_ae(df, ["sire_id", "surface"], "won", "exp_win", k=10)
    df["sire_dist_ae"] = _prior_ae(df, ["sire_id", "dist_band"], "won", "exp_win", k=10)
    df["draw_ae"] = _prior_ae(df, ["course", "surface", "gate_zone"], "won", "exp_win", k=20)
    # 父の距離適性: 産駒が好走した距離の平均 (日付順の累積)
    d = df.sort_values("date", kind="mergesort")
    d = d.assign(_gd=d["distance"].where(d["fin_pct"] <= 0.33))
    tmp = d.groupby(["sire_id", "date"], sort=False)["_gd"].agg(["sum", "count"]).reset_index()
    cum = tmp.groupby("sire_id")[["sum", "count"]].cumsum() - tmp[["sum", "count"]]
    tmp["_sd"] = (cum["sum"] + 1800 * 20) / (cum["count"] + 20)
    sd = df[["sire_id", "date"]].merge(tmp[["sire_id", "date", "_sd"]], how="left")["_sd"].to_numpy()
    df["sire_dist_fit"] = -((df["distance"] - sd) / 400) ** 2
    # 乗り替わり: 今回騎手の A/E - 前走時の騎手 A/E
    h = df.sort_values(["horse_id", "date"], kind="mergesort")
    prev = h.groupby("horse_id")["jockey_ae"].shift(1)
    df["jockey_upgrade"] = ((h["jockey_ae"] - prev) * h["_jchange"]).reindex(df.index).fillna(0)
    return df


def build_features(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """raw (schema 準拠) -> (特徴量付き DataFrame, 特徴量列名)"""
    df = _prepare(raw)
    df = _speed_figures(df)
    df = _horse_history(df)
    df = _connections(df)

    df["no_fig"] = df["fig_m5"].isna().astype(float)
    df["female"] = (df["sex"] == "F").astype(float)
    df["bw_diff"] = df["body_weight_diff"].fillna(0)
    df["abs_bw_diff"] = df["bw_diff"].abs()

    for c in ALL_FEATURES:
        if c in _RAW or c in ("pace_inter",):
            continue
        df[c] = _race_z(df, c)

    # 展開: 先行馬の多さ (ペース圧力) x 脚質。差し・追込は先行争いが激しいほど有利
    front = (df["style"] < -0.6).astype(float)  # z 化後の style で先行勢を判定
    pp = front.groupby(df["race_id"]).transform("mean")
    df["pace_pressure"] = pp
    df["pace_inter"] = df["style"] * (pp - 0.25)
    # 道悪: 今日が道悪のときだけ道悪適性が効く
    df["heavy_inter"] = _race_z(df, "_apt_heavy") * df["heavy_now"]
    df[ALL_FEATURES] = df[ALL_FEATURES].fillna(0.0)
    return df, list(ALL_FEATURES)
