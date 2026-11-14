"""まとめて検証 (探索用レポート).

1回の実行で次を比べる。全てウォークフォワードのアウトオブサンプル予測で評価する。
  A 線形・基本ファクター              (これまでのモデル)
  B 線形 + 追加ファクター
  C 勾配ブースティング + 追加ファクター  (非線形・条件との組み合わせ)
  D C に市場確率も入力                 (オッズへの補正を直接学習)
各モデルについて
  * 市場に対する予測精度の上乗せ (対数尤度/レース)
  * 単勝: モデルの期待値帯ごとの実際の回収率 (帯が上がるほど回収率が上がれば本物の信号)
  * 指数 (モデル勝率) の順位にもとづく 8 通りの買い方 (単勝・複勝・馬連流し・3連複流し・
    馬連BOX・ワイドBOX・3連複BOX・3連単流し) を払戻テーブルで精算した回収率と収支
注意: ここは探索なので多数の帯を同時に見る。1つの帯だけ 100% を超えても偶然の可能性が高い。
     「帯が上がるほど回収率が上がる」「95%CI の下限が 1 を超える」の両方を満たすものだけを候補にする。
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .backtest import walk_forward
from .betting import Strategy
from .features import build_features, ALL_FEATURES
from .model import TwoStageModel, trainable

EV_BINS = [0, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, np.inf]
ODDS_BINS = [1, 2, 3, 5, 10, 20, 50, 1e6]


def roi_table(d: pd.DataFrame, by: str, bins) -> pd.DataFrame:
    """帯ごとの 点数・的中率・回収率・95%CI (同一レース内の相関を考慮したクラスタ標準誤差)。"""
    d = d.assign(_b=pd.cut(d[by], bins, right=False))
    rows = []
    for b, g in d.groupby("_b", observed=True):
        n = len(g)
        x = g["ret"].to_numpy() - 1.0
        mean = x.mean()
        per = pd.Series(x - mean).groupby(g["race_id"].to_numpy()).sum()
        se = np.sqrt((per ** 2).sum()) / n
        rows.append({by: str(b), "n": n, "hit": (g["ret"] > 0).mean(), "roi": 1 + mean,
                     "lo95": 1 + mean - 1.96 * se, "hi95": 1 + mean + 1.96 * se})
    return pd.DataFrame(rows)


def win_rows(preds: pd.DataFrame) -> pd.DataFrame:
    p = preds[preds["mkt_ok"].astype(bool) & preds["finish_pos"].notna()].copy()
    p["ret"] = (p["finish_pos"] == 1) * p["odds_win"]
    return p


# 指数 (= モデルの勝率 p) の順位にもとづく 8 通りの買い方: (名前, 券種, 1点あたり金額)
PATTERNS = [
    ("1 単勝 指数1位", "単勝", 5000),
    ("2 複勝 指数1位", "複勝", 5000),
    ("3 馬連流し 1位→2,3,4位", "馬連", 2500),
    ("4 3連複流し 1位→2,3,4位", "3連複", 2500),
    ("5 馬連BOX 1〜3位", "馬連", 2500),
    ("6 ワイドBOX 1〜3位", "ワイド", 2500),
    ("7 3連複BOX 1〜4位", "3連複", 2000),
    ("8 3連単流し 1位→2,3,4位", "3連単", 1500),
]


def _tickets(a, b, c, d):
    """指数 1〜4 位の馬番 a,b,c,d から各パターンの買い目 (払戻テーブルと同じ並び) を作る。"""
    s = lambda *x: tuple(sorted(x))
    return [
        [(a,)],
        [(a,)],
        [s(a, b), s(a, c), s(a, d)],
        [s(a, b, c), s(a, b, d), s(a, c, d)],
        [s(a, b), s(a, c), s(b, c)],
        [s(a, b), s(a, c), s(b, c)],
        [s(a, b, c), s(a, b, d), s(a, c, d), s(b, c, d)],
        [(a, x, y) for x in (b, c, d) for y in (b, c, d) if x != y],
    ]


def pattern_rows(preds: pd.DataFrame, payouts: pd.DataFrame, rank_by: str = "p") -> pd.DataFrame:
    """各レースで 8 パターンを買った場合の 投資額・払戻額 (円)。rank_by="p_mkt" なら人気順で買う。

    その券種の払戻が DB に無いレースは対象外 (的中しても外れ扱いになるのを防ぐ)。
    """
    pay = {bt: {(r, c): v for r, c, v in zip(g["race_id"], g["combo"], g["ret"])}
           for bt, g in payouts.groupby("bet_type")}
    have = {bt: set(g["race_id"]) for bt, g in payouts.groupby("bet_type")}
    ok = preds[preds["mkt_ok"].astype(bool) & preds["finish_pos"].notna() & preds[rank_by].notna()]
    rows = []
    for rid, g in ok.groupby("race_id", sort=False):
        if len(g) < 5:
            continue
        g = g.sort_values(rank_by, ascending=False, kind="mergesort")
        no = g["horse_no"].to_numpy().astype(int)
        top = g.iloc[0]
        ev1 = top["p"] * top["odds_win"]
        is_fav = top["odds_win"] == g["odds_win"].min()
        for (name, bt, stake), tk in zip(PATTERNS, _tickets(*no[:4])):
            if rid not in have.get(bt, ()):
                continue
            table = pay[bt]
            ret = sum(table.get((rid, c), 0.0) for c in tk) * stake
            rows.append((rid, name, len(tk) * stake, ret, ev1, top["p"], is_fav, len(g)))
    return pd.DataFrame(rows, columns=["race_id", "pattern", "stake", "ret", "ev1", "p1", "is_fav", "field"])


def pattern_table(d: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, g in d.groupby("pattern"):
        n, stake = len(g), g["stake"].iloc[0]
        r = g["ret"].to_numpy() / stake
        se = r.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
        rows.append({"買い方": name, "レース数": n, "投資/R": int(stake), "的中率": (r > 0).mean(),
                     "回収率": r.mean(), "lo95": r.mean() - 1.96 * se, "hi95": r.mean() + 1.96 * se,
                     "収支/R(円)": round((g["ret"] - g["stake"]).mean()),
                     "合計収支(円)": round((g["ret"] - g["stake"]).sum())})
    return pd.DataFrame(rows)


def pattern_pivot(d: pd.DataFrame, key: pd.Series) -> pd.DataFrame:
    """条件 (key) 別 × 買い方別 の回収率。列見出しはパターン番号。"""
    d = d.assign(_k=key.to_numpy(), _n=d["pattern"].str[0])
    g = d.groupby(["_k", "_n"], observed=True)
    roi = (g["ret"].sum() / g["stake"].sum()).unstack("_n")
    roi.insert(0, "レース数", d[d["_n"] == "1"].groupby("_k", observed=True).size())
    return roi.reset_index().rename(columns={"_k": "条件"})


def _fmt(df: pd.DataFrame) -> str:
    return df.round(3).to_string(index=False) if len(df) else "(該当データなし)"


def run(raw: pd.DataFrame, payouts: pd.DataFrame | None = None, test_months: int = 12,
        gbm: bool = True, log=print) -> str:
    L: list[str] = []

    def out(s=""):
        L.append(s)
        log(s)

    t0 = time.time()
    F, cols = build_features(raw, extra=True)
    extras = [c for c in cols if c not in ALL_FEATURES]
    out(f"出走 {len(F)} 行 / {F['race_id'].nunique()} レース, 追加ファクター {len(extras)} 個: {extras}")

    # --- 追加ファクター単体の有意性 (線形モデルの z 値) ---
    last = F["date"].max()
    c0 = last - pd.DateOffset(months=12)
    m = TwoStageModel(cols).fit(F[F["date"] < c0], F[(F["date"] >= c0)])
    z = m.z_values()
    out("\n[1] 追加ファクターの z 値 (オッズを使わない線形モデル内。|z|>2 で統計的に意味あり)")
    out(z[extras].round(2).sort_values(key=np.abs, ascending=False).to_string())

    variants = {"A 線形・基本": lambda: TwoStageModel(list(ALL_FEATURES)),
                "B 線形+追加": lambda: TwoStageModel(cols)}
    if gbm:
        try:
            import sklearn  # noqa: F401
            variants["C GBM+追加"] = lambda: TwoStageModel(cols, kind="gbm")
            variants["D GBM+追加+市場"] = lambda: TwoStageModel(cols, kind="gbm", use_market=True)
        except ImportError:
            out("\n(scikit-learn が無いので GBM は省略)")

    summary, all_preds = [], {}
    for name, mk in variants.items():
        t1 = time.time()
        per, _, preds, _ = walk_forward(F, cols, test_months=test_months, make_model=mk,
                                        base_strategy=Strategy(ev_min=9e9), verbose=False)
        all_preds[name] = preds
        gain = per["ll_combined"] - per["ll_market"]
        w = per["races"]
        summary.append({"model": name, "LL上乗せ/レース": (gain * w).sum() / w.sum(),
                        "上回った期間": f"{(gain > 0).sum()}/{len(per)}",
                        "Stage1単独LL-市場": ((per["ll_fund"] - per["ll_market"]) * w).sum() / w.sum(),
                        "alpha(自前)": per["alpha_fund"].mean(), "gamma(市場)": per["gamma_market"].mean(),
                        "秒": round(time.time() - t1)})
        log(f"  {name} 完了 ({round(time.time() - t1)} 秒)")
    out("\n[2] モデル比較 (LL上乗せ > 0 かつ多くの期間で上回るほど、オッズに無い情報を持っている)")
    out(pd.DataFrame(summary).round(4).to_string(index=False))

    out("\n[3] 単勝: モデル期待値 (p×オッズ) の帯ごとの実際の回収率")
    for name, preds in all_preds.items():
        out(f"--- {name}")
        out(_fmt(roi_table(win_rows(preds), "ev", EV_BINS)))

    base_preds = all_preds["A 線形・基本"]
    base = win_rows(base_preds)
    out("\n[4] 参考: 市場だけで見た単勝回収率 (オッズ帯別)")
    out(_fmt(roi_table(base, "odds_win", ODDS_BINS)))

    if payouts is not None and len(payouts):
        out("\n[5] 指数順位にもとづく 8 通りの買い方 (払戻テーブルで精算。指数 = 各モデルの勝率)")
        out("    比較用の「市場(人気順)」は、指数の代わりに単勝人気順で同じ買い方をした場合")
        sets = {"市場(人気順)": pattern_rows(base_preds, payouts, rank_by="p_mkt")}
        sets.update({name: pattern_rows(pr, payouts) for name, pr in all_preds.items()})
        for name, d in sets.items():
            out(f"--- {name}")
            if d.empty:
                out("(該当データなし)")
                continue
            out(_fmt(pattern_table(d)))
            tot = d.groupby("race_id")[["stake", "ret"]].sum()
            out(f"    8通り全部: 投資 {int(tot['stake'].sum()):,} 円 / 払戻 {int(tot['ret'].sum()):,} 円 / "
                f"回収率 {tot['ret'].sum() / tot['stake'].sum():.3f}")
        out("\n[6] 条件別の回収率 (列 1〜8 は上の買い方の番号)")
        for name, d in sets.items():
            if d.empty or name.startswith("市場"):
                continue
            out(f"--- {name} / 指数1位の単勝期待値 (p×オッズ) 別")
            out(_fmt(pattern_pivot(d, pd.cut(d["ev1"], [0, 0.7, 0.8, 0.9, 1.0, 1.1, np.inf], right=False).astype(str))))
            out(f"--- {name} / 指数1位が1番人気かどうか")
            out(_fmt(pattern_pivot(d, d["is_fav"].map({True: "1番人気と同じ", False: "1番人気と違う"}))))
    out(f"\n合計 {round(time.time() - t0)} 秒")
    return "\n".join(L)
