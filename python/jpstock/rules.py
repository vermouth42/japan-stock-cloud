"""スイング（日足）のシグナル判定ルール。

TradingView の JP_SwingDay_Indicator / JP_SwingDay_Strategy、MT5 の JP_SwingDay と同じ定義。
仕様は docs/strategy_spec.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from . import indicators as ta

SETUP_BREAKOUT = "BO"
SETUP_PULLBACK = "PB"
SETUP_BREAKDOWN = "BD"
SETUP_RALLY = "RS"

SETUP_LABELS = {
    SETUP_BREAKOUT: "ブレイクアウト買い",
    SETUP_PULLBACK: "押し目買い",
    SETUP_BREAKDOWN: "ブレイクダウン売り",
    SETUP_RALLY: "戻り売り",
}

LONG_SETUPS = (SETUP_BREAKOUT, SETUP_PULLBACK)
SHORT_SETUPS = (SETUP_BREAKDOWN, SETUP_RALLY)


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class SwingParams:
    # 移動平均（日本株で定番の 5・25・75 日）
    ma_short: int = 5
    ma_mid: int = 25
    ma_long: int = 75
    slope_len: int = 5  # 25日線の傾き判定（n 本前と比較）

    atr_len: int = 14
    rsi_len: int = 14
    vol_len: int = 25  # 出来高倍率の分母（25日平均出来高）

    # ブレイクアウト
    breakout_len: int = 20
    vol_ratio_min: float = 1.5
    close_pos_min: float = 0.6  # 当日レンジの上側 40% で引けている（上ヒゲが短い）
    rsi_bo_max: float = 80.0
    dev_max: float = 15.0  # 25日線乖離率の上限（%）。これ以上は追いかけない

    # 押し目
    pullback_lookback: int = 3
    pullback_atr: float = 0.5  # 25日線 + 0.5ATR 以内まで押した
    rsi_pb_min: float = 40.0
    rsi_pb_max: float = 65.0

    # 戻り売り（押し目の対称）
    rsi_rs_min: float = 35.0
    rsi_rs_max: float = 60.0
    rsi_bd_min: float = 20.0

    # 決済
    stop_atr: float = 2.0  # 初期損切り = シグナル終値 − 2ATR
    trail_atr: float = 3.0  # トレーリング = エントリー後の最高値 − 3ATR
    tp_r: float = 2.0  # 第1利確 = 2R
    tp_pct: float = 50.0  # 第1利確で決済する割合（%）
    max_hold: int = 30  # 最大保有日数（0 で無効）
    ma_exit: bool = True  # 終値で25日線を割ったら手仕舞い
    ma_exit_atr: float = 1.0  # 25日線割れの判定に持たせる余裕（ATR 倍）。押し目買い直後のダマシ対策

    # 長期トレンドフィルター（0 で無効）。買いは終値 > n日線、売りは終値 < n日線の銘柄だけ。
    # n日分のデータがそろうまではシグナルを出さない。10年の検証で1トレードの質が改善したので既定は200日線。
    trend_ma_len: int = 200


def _all(*values: object) -> bool:
    return all(v is not None for v in values)


def analyze(bars: Sequence[Bar], p: SwingParams = SwingParams()) -> Dict[str, List]:
    """指標・トレンド・シグナルを全バー分計算する。"""
    o = [b.open for b in bars]
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    c = [b.close for b in bars]
    v = [float(b.volume) for b in bars]
    n = len(bars)

    ma_s = ta.sma(c, p.ma_short)
    ma_m = ta.sma(c, p.ma_mid)
    ma_l = ta.sma(c, p.ma_long)
    ma_t = ta.sma(c, p.trend_ma_len) if p.trend_ma_len > 0 else [None] * n
    ma_m_prev = ta.shift(ma_m, p.slope_len)
    atr = ta.atr(h, lo, c, p.atr_len)
    rsi = ta.rsi(c, p.rsi_len)
    vol_ma = ta.sma(v, p.vol_len)
    hh_prev = ta.shift(ta.highest(h, p.breakout_len), 1)
    ll_prev = ta.shift(ta.lowest(lo, p.breakout_len), 1)
    hh_now = ta.highest(h, p.breakout_len)
    low_pb = ta.lowest(lo, p.pullback_lookback)
    high_pb = ta.highest(h, p.pullback_lookback)

    out: Dict[str, List] = {
        key: [None] * n
        for key in (
            "vol_ratio", "close_pos", "dev", "up_trend", "down_trend",
            "perfect_up", "perfect_down", "setup", "score",
        )
    }
    out.update(
        ma_s=ma_s, ma_m=ma_m, ma_l=ma_l, atr=atr, rsi=rsi,
        hh_prev=hh_prev, ll_prev=ll_prev, ma_t=ma_t,
    )

    for i in range(n):
        rng = h[i] - lo[i]
        close_pos = (c[i] - lo[i]) / rng if rng > 0 else 0.5
        vol_ratio = v[i] / vol_ma[i] if vol_ma[i] else None
        dev = (c[i] - ma_m[i]) / ma_m[i] * 100.0 if ma_m[i] else None
        out["close_pos"][i] = close_pos
        out["vol_ratio"][i] = vol_ratio
        out["dev"][i] = dev

        if not _all(ma_s[i], ma_m[i], ma_l[i], ma_m_prev[i], atr[i], rsi[i]):
            continue

        up = c[i] > ma_l[i] and ma_m[i] > ma_l[i] and ma_m[i] > ma_m_prev[i]
        down = c[i] < ma_l[i] and ma_m[i] < ma_l[i] and ma_m[i] < ma_m_prev[i]
        out["up_trend"][i] = up
        out["down_trend"][i] = down
        out["perfect_up"][i] = ma_s[i] > ma_m[i] > ma_l[i]
        out["perfect_down"][i] = ma_s[i] < ma_m[i] < ma_l[i]

        setup: Optional[str] = None
        if i > 0 and _all(hh_prev[i], vol_ratio, dev) and (
            c[i] > hh_prev[i]
            and vol_ratio >= p.vol_ratio_min
            and close_pos >= p.close_pos_min
            and c[i] > ma_m[i]
            and ma_m[i] >= ma_m_prev[i]
            and rsi[i] <= p.rsi_bo_max
            and dev <= p.dev_max
        ):
            setup = SETUP_BREAKOUT
        elif i > 0 and _all(low_pb[i]) and (
            up
            and low_pb[i] <= ma_m[i] + p.pullback_atr * atr[i]
            and c[i] > ma_m[i]
            and c[i] > h[i - 1]
            and c[i] > o[i]
            and p.rsi_pb_min <= rsi[i] <= p.rsi_pb_max
        ):
            setup = SETUP_PULLBACK
        elif i > 0 and _all(ll_prev[i], vol_ratio, dev) and (
            c[i] < ll_prev[i]
            and vol_ratio >= p.vol_ratio_min
            and close_pos <= 1.0 - p.close_pos_min
            and c[i] < ma_m[i]
            and ma_m[i] <= ma_m_prev[i]
            and rsi[i] >= p.rsi_bd_min
            and dev >= -p.dev_max
        ):
            setup = SETUP_BREAKDOWN
        elif i > 0 and _all(high_pb[i]) and (
            down
            and high_pb[i] >= ma_m[i] - p.pullback_atr * atr[i]
            and c[i] < ma_m[i]
            and c[i] < lo[i - 1]
            and c[i] < o[i]
            and p.rsi_rs_min <= rsi[i] <= p.rsi_rs_max
        ):
            setup = SETUP_RALLY
        if setup is not None and p.trend_ma_len > 0:
            above = ma_t[i] is not None and c[i] > ma_t[i]
            below = ma_t[i] is not None and c[i] < ma_t[i]
            if (setup in LONG_SETUPS and not above) or (setup in SHORT_SETUPS and not below):
                setup = None
        out["setup"][i] = setup

        out["score"][i] = technical_score(
            close=c[i], ma_s=ma_s[i], ma_m=ma_m[i], ma_l=ma_l[i],
            ma_m_prev=ma_m_prev[i], rsi=rsi[i], vol_ratio=vol_ratio,
            dev=dev, hh=hh_now[i],
        )
    return out


def technical_score(
    *, close: float, ma_s: float, ma_m: float, ma_l: float, ma_m_prev: float,
    rsi: float, vol_ratio: Optional[float], dev: Optional[float], hh: Optional[float],
) -> int:
    """買い目線のテクニカルスコア（0〜100）。スクリーニングの並べ替え用。"""
    score = 0
    if ma_s > ma_m > ma_l:
        score += 20  # パーフェクトオーダー
    if close > ma_m:
        score += 15
    if ma_m > ma_m_prev:
        score += 10  # 25日線が上向き
    if close > ma_l:
        score += 10
    if vol_ratio is not None:
        if vol_ratio >= 2.0:
            score += 15
        elif vol_ratio >= 1.5:
            score += 10
        elif vol_ratio >= 1.0:
            score += 5
    if 50 <= rsi <= 70:
        score += 10
    elif 40 <= rsi < 50 or 70 < rsi <= 75:
        score += 5
    if hh is not None and close >= hh * 0.95:
        score += 10  # 20日高値圏
    if dev is not None and 0 <= dev <= 10:
        score += 10  # 25日線の上で、離れすぎていない
    # 過熱のペナルティ
    if dev is not None and dev > 20:
        score -= 10
    if rsi > 80:
        score -= 10
    return max(0, min(100, score))


def warnings_for(a: Dict[str, List], i: int) -> List[str]:
    """i 本目の注意事項（保有中の判断材料）。"""
    notes: List[str] = []
    rsi = a["rsi"][i]
    dev = a["dev"][i]
    vol_ratio = a["vol_ratio"][i]
    ma_m = a["ma_m"]
    if rsi is not None and rsi >= 75:
        notes.append("RSI過熱")
    if rsi is not None and rsi <= 25:
        notes.append("RSI売られすぎ")
    if dev is not None and dev >= 15:
        notes.append("25日線乖離+15%超")
    if dev is not None and dev <= -15:
        notes.append("25日線乖離-15%超")
    if vol_ratio is not None and vol_ratio >= 2.0:
        notes.append("出来高急増")
    return notes


def crossed_below_mid(closes: Sequence[float], ma_m: Sequence[Optional[float]], i: int) -> bool:
    """i 本目の終値で25日線を下抜けたか。"""
    if i < 1 or ma_m[i] is None or ma_m[i - 1] is None:
        return False
    return closes[i] < ma_m[i] and closes[i - 1] >= ma_m[i - 1]
