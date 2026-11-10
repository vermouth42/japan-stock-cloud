"""検証用の合成レースデータ生成器.

実データ無しでもパイプライン全体 (特徴量→モデル→買い目→検証) が正しく動くことを
確認するためのもの。市場 (オッズ) は以下の「現実の馬券市場で知られている歪み」を持つ:
  * 控除率 20% (JRA 単勝)
  * 本命-大穴バイアス (人気薄が過剰に買われる)
  * 人気騎手・前走着順の過大評価、血統適性・枠順・展開の過小評価
※ 合成データで回収率100%を超えても、実データで超える保証にはならない。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TURF_D = [1200, 1400, 1600, 1800, 2000, 2200, 2400, 2500, 3000]
DIRT_D = [1000, 1200, 1400, 1600, 1700, 1800, 1900, 2100]
AGE_CURVE = {2: -0.4, 3: 0.0, 4: 0.15, 5: 0.1, 6: -0.05}


def generate(n_years: int = 5, races_per_week: int = 30, seed: int = 0,
             start: str = "2019-01-05") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n_c, n_s, n_j, n_t = 10, 60, 120, 200
    courses = [f"C{i:02d}" for i in range(n_c)]
    course_off = rng.normal(0, 0.5, n_c)
    draw_bias = {(c, s): rng.normal(-0.35 if s == "turf" else 0.0, 0.2)
                 for c in range(n_c) for s in ("turf", "dirt")}
    sire_opt = rng.uniform(1200, 2800, n_s)
    sire_turf = rng.normal(0, 0.45, n_s)
    sire_heavy = rng.normal(0, 0.3, n_s)
    sire_style = rng.uniform(0.2, 0.8, n_s)
    j_skill = rng.normal(0, 0.25, n_j)
    j_pop = j_skill + rng.normal(0, 0.25, n_j)
    t_skill = rng.normal(0, 0.2, n_t)

    cap = 60_000
    H = {k: np.zeros(cap) for k in ["base", "turf", "opt", "heavy", "style", "form",
                                    "bw", "last_w", "rest", "wins", "runs", "birth",
                                    "last_fin"]}
    Hi = {k: np.zeros(cap, int) for k in ["sire", "trainer", "jockey", "female", "active"]}
    n_h = 0

    def add_horses(k, year):
        nonlocal n_h
        idx = np.arange(n_h, n_h + k)
        s = rng.integers(0, n_s, k)
        Hi["sire"][idx] = s
        H["base"][idx] = rng.normal(0, 1, k)
        H["turf"][idx] = sire_turf[s] + rng.normal(0, 0.3, k)
        H["opt"][idx] = sire_opt[s] + rng.normal(0, 200, k)
        H["heavy"][idx] = sire_heavy[s] + rng.normal(0, 0.2, k)
        H["style"][idx] = np.clip(sire_style[s] + rng.normal(0, 0.2, k), 0, 1)
        H["bw"][idx] = rng.normal(470, 25, k)
        H["last_w"][idx] = -999
        H["birth"][idx] = year - 2
        H["last_fin"][idx] = 0.5
        Hi["trainer"][idx] = rng.integers(0, n_t, k)
        Hi["jockey"][idx] = rng.integers(0, n_j, k)
        Hi["female"][idx] = rng.random(k) < 0.45
        Hi["active"][idx] = 1
        n_h += k

    start = pd.Timestamp(start)
    add_horses(2500, start.year)
    rows = []
    rid = 0
    weeks = n_years * 52
    for w in range(weeks):
        date0 = start + pd.Timedelta(weeks=w)
        if w > 0:
            add_horses(int(races_per_week * 1.1), date0.year)
        act = np.flatnonzero(Hi["active"][:n_h] == 1)
        elig = act[(w - H["last_w"][act]) >= H["rest"][act]]
        used = np.zeros(n_h, bool)
        week_courses = rng.choice(n_c, 3, replace=False)
        going_day = {(c, s): rng.choice(4, p=[0.7, 0.15, 0.1, 0.05])
                     for c in week_courses for s in ("turf", "dirt")}
        cls_all = np.minimum(H["wins"][elig], 4).astype(int)
        for r in range(races_per_week):
            date = date0 + pd.Timedelta(days=int(r % 2))
            c = week_courses[r % 3]
            surf = "turf" if rng.random() < 0.55 else "dirt"
            dist = int(rng.choice(TURF_D if surf == "turf" else DIRT_D))
            going = going_day[(c, surf)]
            free = elig[~used[elig]]
            if len(free) < 8:
                break
            fc = cls_all[~used[elig]]
            cls = int(rng.choice(fc))
            pool = free[fc == cls]
            if len(pool) < 6:
                continue
            # 陣営は適性のある条件を選びがち
            suit = np.exp(-((dist - H["opt"][pool]) / 700) ** 2
                          + 0.5 * H["turf"][pool] * (1 if surf == "turf" else -1))
            n = int(min(rng.integers(8, 19), len(pool)))
            pick = rng.choice(pool, n, replace=False, p=suit / suit.sum())
            used[pick] = True
            jockey = np.where(rng.random(n) < 0.7, Hi["jockey"][pick], rng.integers(0, n_j, n))
            age = date.year - H["birth"][pick]
            off = w - H["last_w"][pick]
            first = H["runs"][pick] == 0
            bw_diff = np.where(first, 0, np.round(rng.normal(0, 5, n)))
            H["bw"][pick] += bw_diff
            wc = rng.choice([54, 55, 56, 57, 58], n) - 2 * Hi["female"][pick]
            style = H["style"][pick]
            pp = np.mean(style < 0.35)

            ability = H["base"][pick] + np.vectorize(lambda a: AGE_CURVE.get(int(a), -0.3))(age) \
                + H["form"][pick]
            sign = 1 if surf == "turf" else -1
            t_surf = 0.5 * H["turf"][pick] * sign
            t_dist = -0.6 * ((dist - H["opt"][pick]) / 500) ** 2
            gate = np.arange(n) / max(n - 1, 1)
            t_draw = draw_bias[(c, surf)] * gate
            t_heavy = H["heavy"][pick] * (going >= 2)
            t_pace = 1.5 * (style - 0.5) * (pp - 0.3)
            t_rest = np.where(~first & (off > 26), -0.4, 0.0)
            t_bw = -0.03 * np.maximum(np.abs(bw_diff) - 8, 0)
            t_wc = -0.05 * (wc - 55)
            s = (ability + j_skill[jockey] + t_skill[Hi["trainer"][pick]] + t_surf + t_dist
                 + t_draw + t_heavy + t_pace + t_rest + t_bw + t_wc)
            s = 1.3 * s
            perf = s + rng.gumbel(0, 1, n)
            order = np.argsort(-perf)
            fin = np.empty(n, int); fin[order] = np.arange(1, n + 1)

            # --- 市場 (大衆の評価) ---
            noise_sd = np.where(first, 0.6, 0.3)
            public = (ability + rng.normal(0, 1, n) * noise_sd
                      + 1.2 * j_pop[jockey] + 0.8 * t_skill[Hi["trainer"][pick]]
                      + 0.3 * t_surf + 0.3 * t_dist + 0.2 * t_draw + 0.3 * t_heavy
                      + t_rest + 0.5 * t_bw + t_wc
                      - 0.5 * np.where(first, 0, H["last_fin"][pick] - 0.5))
            public = 1.3 * public
            q = np.exp(public - public.max()); q /= q.sum()
            q = q ** 0.72; q /= q.sum()  # 本命-大穴バイアス
            odds = np.maximum(np.floor(0.80 / q * 10) / 10, 1.0)

            dk = dist / 1000
            time = (dk * (59 if surf == "turf" else 62) + course_off[c] + going * 0.35 * dk
                    - 0.35 * perf * dist / 1600 + rng.normal(0, 0.1, n))
            l3 = (34.5 + (0 if surf == "turf" else 2.0) + 0.4 * going - 0.3 * perf
                  + 0.8 * (0.5 - style) + rng.normal(0, 0.3, n))
            corner_raw = style + rng.normal(0, 0.15, n)
            corner = np.empty(n, int); corner[np.argsort(corner_raw)] = np.arange(1, n + 1)

            for i in range(n):
                h = pick[i]
                rows.append((f"R{rid:06d}", date, courses[c], surf, dist, going, cls,
                             f"H{h:06d}", i + 1, int(age[i]), "F" if Hi["female"][h] else "M",
                             float(wc[i]), float(bw_diff[i]), f"J{jockey[i]:03d}",
                             f"T{Hi['trainer'][h]:03d}", f"S{Hi['sire'][h]:02d}",
                             float(odds[i]), int(fin[i]), round(float(time[i]), 1),
                             round(float(l3[i]), 1), int(corner[i])))
            # 状態更新
            H["last_w"][pick] = w
            H["rest"][pick] = rng.integers(3, 10, n) + (rng.random(n) < 0.05) * 30
            H["runs"][pick] += 1
            H["wins"][pick] += fin == 1
            H["last_fin"][pick] = (fin - 1) / (n - 1)
            H["form"][pick] = 0.7 * H["form"][pick] + rng.normal(0, 0.15, n)
            retire = (H["runs"][pick] >= rng.integers(15, 35, n)) | (age >= 8) | (rng.random(n) < 0.02)
            Hi["active"][pick[retire]] = 0
            rid += 1
    cols = ["race_id", "date", "course", "surface", "distance", "going", "race_class",
            "horse_id", "horse_no", "age", "sex", "weight_carried", "body_weight_diff",
            "jockey_id", "trainer_id", "sire_id", "odds_win", "finish_pos", "time_sec",
            "last3f_sec", "corner_pos"]
    return pd.DataFrame(rows, columns=cols)
