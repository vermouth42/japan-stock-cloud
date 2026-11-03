"""スイング戦略のバックテスト（TradingView の JP_SwingDay_Strategy と同じ約定モデル）。

約定モデル
- シグナルは終値で判定し、翌営業日の始値で成行エントリー。
- 損切り・トレーリング・第1利確の水準は「前日の終値時点」で更新され、当日の値動きで約定する。
- 同じ日に損切りと利確の両方に届いた場合は、保守的に損切りが先とみなす。
- 窓を空けて損切りラインを超えた場合は始値で約定する。
- 25日線割れ・期間満了は終値で判定し、翌日始値で決済する。
- 手数料 0（SBI・楽天など国内株の売買手数料無料プランを想定）。スリッページは価格（円）で指定。
- 地合いフィルター（任意）: 指数の前日終値が移動平均より上なら買いのみ、下なら売りのみ。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date as _date
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

from . import indicators as ta
from .rules import LONG_SETUPS, SHORT_SETUPS, Bar, SwingParams, analyze


@dataclass
class Trade:
    code: str
    side: str  # "long" / "short"
    setup: str
    signal_date: str
    entry_date: str
    entry_price: float
    init_stop: float
    risk: float  # 1株あたりのリスク（= stop_atr × ATR）
    exit_date: str = ""
    exit_price: float = 0.0  # 平均決済価格
    exit_reason: str = ""
    bars_held: int = 0
    r_multiple: float = 0.0
    return_pct: float = 0.0
    open_at_end: bool = False
    score: int = 0  # シグナル時点のテクニカルスコア（同じ日に複数シグナルがあるときの優先順位）


@dataclass
class MarketFilter:
    """指数の「前日終値 > 移動平均」で地合いを判定する（TradingView 版の地合いフィルターと同じ）。

    シグナル日より前の最後の指数の足で判定する。指数のデータが無い・移動平均が計算できない日は通す。
    """

    def __init__(self, index_bars: Sequence[Bar], ma_len: int = 75) -> None:
        closes = [b.close for b in index_bars]
        ma = ta.sma(closes, ma_len)
        self._dates = [b.date for b in index_bars]
        self._state: List[Optional[Tuple[bool, bool]]] = [
            None if m is None else (c > m, c < m) for c, m in zip(closes, ma)
        ]

    def allows(self, date: str, side: int) -> bool:
        k = bisect_left(self._dates, date) - 1
        if k < 0 or self._state[k] is None:
            return True
        up, down = self._state[k]  # type: ignore[misc]
        return up if side > 0 else down


@dataclass
class _Position:
    trade: Trade
    side: int  # +1 long / -1 short
    stop: float
    tp: float
    entry_index: int
    remaining: float = 1.0
    realized: float = 0.0  # 1株あたり損益 × 決済割合の累計
    tp_done: bool = False
    extreme: float = 0.0  # long: 最高値 / short: 最安値
    exit_next_open: str = ""
    fills: List[str] = field(default_factory=list)


def _close_part(pos: _Position, price: float, fraction: float) -> None:
    pos.realized += pos.side * (price - pos.trade.entry_price) * fraction
    pos.remaining -= fraction


def _finish(pos: _Position, date: str, index: int, reason: str) -> Trade:
    t = pos.trade
    pnl = pos.realized
    t.exit_date = date
    t.exit_reason = reason
    t.bars_held = index - pos.entry_index + 1
    t.exit_price = t.entry_price + pnl * pos.side
    t.r_multiple = pnl / t.risk if t.risk > 0 else 0.0
    t.return_pct = pnl / t.entry_price * 100.0 if t.entry_price else 0.0
    return t


def backtest_swing(
    code: str,
    bars: Sequence[Bar],
    p: SwingParams = SwingParams(),
    *,
    allow_long: bool = True,
    allow_short: bool = False,
    slippage: float = 0.0,
    market: Optional[MarketFilter] = None,
    analysis: Optional[Dict[str, List]] = None,
) -> List[Trade]:
    """1銘柄分のトレード一覧を返す。slippage は価格単位（例: 1円）。

    market を渡すと地合いフィルターをかける。analysis を渡すと analyze() の代わりにそれを使う（テスト用）。
    """
    a = analysis if analysis is not None else analyze(bars, p)
    atr = a["atr"]
    ma_m = a["ma_m"]
    trades: List[Trade] = []
    pos: Optional[_Position] = None
    pending: Optional[Dict] = None

    for i, b in enumerate(bars):
        # 1) 前日終値で決まった成行決済（25日線割れ・期間満了）
        if pos is not None and pos.exit_next_open:
            _close_part(pos, b.open - pos.side * slippage, pos.remaining)
            trades.append(_finish(pos, b.date, i - 1, pos.exit_next_open))
            pos = None

        # 2) 前日シグナルの成行エントリー
        if pos is None and pending is not None:
            side = pending["side"]
            entry = b.open + side * slippage
            trade = Trade(
                code=code,
                side="long" if side > 0 else "short",
                setup=pending["setup"],
                signal_date=pending["date"],
                entry_date=b.date,
                entry_price=entry,
                init_stop=pending["stop"],
                risk=pending["risk"],
                score=pending["score"],
            )
            pos = _Position(
                trade=trade, side=side, stop=pending["stop"], tp=pending["tp"],
                entry_index=i, extreme=b.high if side > 0 else b.low,
            )
            pending = None

        # 3) ザラ場中の損切り・利確
        if pos is not None:
            s = pos.side
            stop_hit_at_open = (b.open <= pos.stop) if s > 0 else (b.open >= pos.stop)
            stop_hit = (b.low <= pos.stop) if s > 0 else (b.high >= pos.stop)
            tp_hit = (not pos.tp_done) and ((b.high >= pos.tp) if s > 0 else (b.low <= pos.tp))
            if stop_hit_at_open or stop_hit:
                price = b.open if stop_hit_at_open else pos.stop
                _close_part(pos, price - s * slippage, pos.remaining)
                reason = "建値ストップ" if pos.tp_done and pos.stop == pos.trade.entry_price else (
                    "損切り" if pos.stop == pos.trade.init_stop else "トレーリング"
                )
                trades.append(_finish(pos, b.date, i, reason))
                pos = None
            elif tp_hit:
                fill = max(b.open, pos.tp) if s > 0 else min(b.open, pos.tp)
                _close_part(pos, fill, p.tp_pct / 100.0)
                pos.tp_done = True
                if pos.remaining <= 1e-9:
                    trades.append(_finish(pos, b.date, i, "利確"))
                    pos = None

        # 4) 終値での更新（トレーリング・建値移動・終値ベースの手仕舞い）
        if pos is not None:
            s = pos.side
            pos.extreme = max(pos.extreme, b.high) if s > 0 else min(pos.extreme, b.low)
            if atr[i] is not None:
                trail = pos.extreme - s * p.trail_atr * atr[i]
                pos.stop = max(pos.trade.init_stop, trail) if s > 0 else min(pos.trade.init_stop, trail)
            if pos.tp_done:
                be = pos.trade.entry_price
                pos.stop = max(pos.stop, be) if s > 0 else min(pos.stop, be)
            held = i - pos.entry_index + 1
            buffer = p.ma_exit_atr * atr[i] if atr[i] is not None else 0.0
            if p.ma_exit and ma_m[i] is not None and (
                (s > 0 and b.close < ma_m[i] - buffer) or (s < 0 and b.close > ma_m[i] + buffer)
            ):
                pos.exit_next_open = "25日線割れ" if s > 0 else "25日線超え"
            elif p.max_hold > 0 and held >= p.max_hold:
                pos.exit_next_open = "期間満了"

        # 5) 新規シグナル（ノーポジションのときだけ）
        setup = a["setup"][i]
        if pos is None and pending is None and setup is not None and atr[i] is not None and i + 1 < len(bars):
            side = 0
            if allow_long and setup in LONG_SETUPS:
                side = 1
            elif allow_short and setup in SHORT_SETUPS:
                side = -1
            if side != 0 and market is not None and not market.allows(b.date, side):
                side = 0
            if side != 0:
                risk = p.stop_atr * atr[i]
                pending = {
                    "side": side,
                    "setup": setup,
                    "date": b.date,
                    "stop": b.close - side * risk,
                    "tp": b.close + side * p.tp_r * risk,
                    "risk": risk,
                    "score": (a.get("score") or [None] * len(bars))[i] or 0,
                }

    # 期末に残ったポジションは最終終値で評価
    if pos is not None:
        last = bars[-1]
        _close_part(pos, last.close, pos.remaining)
        t = _finish(pos, last.date, len(bars) - 1, "未決済(評価)")
        t.open_at_end = True
        trades.append(t)
    return trades


def summarize(trades: Sequence[Trade]) -> Dict[str, float]:
    """勝率・プロフィットファクター・期待値（R）などを計算する。"""
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    rs = [t.r_multiple for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    # 連敗の最大
    streak = worst = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    # R 累積の最大ドローダウン（トレード順）
    equity = peak = max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": n,
        "win_rate": len(wins) / n * 100.0,
        "avg_r": sum(rs) / n,
        "median_r": median(rs),
        "avg_win_r": gross_win / len(wins) if wins else 0.0,
        "avg_loss_r": -gross_loss / len(losses) if losses else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "total_r": sum(rs),
        "max_dd_r": max_dd,
        "max_losing_streak": worst,
        "avg_bars": sum(t.bars_held for t in trades) / n,
        "avg_return_pct": sum(t.return_pct for t in trades) / n,
    }


def yearly(trades: Sequence[Trade]) -> Dict[str, Dict[str, float]]:
    """エントリー年ごとの集計。"""
    by_year: Dict[str, List[Trade]] = {}
    for t in trades:
        by_year.setdefault(t.entry_date[:4], []).append(t)
    return {y: summarize(ts) for y, ts in sorted(by_year.items())}


@dataclass
class PortfolioResult:
    initial: float
    final: float
    cagr_pct: float
    max_dd_pct: float
    taken: int
    skipped: int
    years: float
    yearly_return_pct: Dict[str, float]
    max_concurrent: int


def _years_between(start: str, end: str) -> float:
    d0 = _date.fromisoformat(start)
    d1 = _date.fromisoformat(end)
    return max((d1 - d0).days / 365.25, 1e-9)


def simulate_portfolio(
    trades: Sequence[Trade],
    *,
    start: str,
    end: str,
    initial: float = 3_000_000.0,
    risk_pct: float = 1.0,
    max_positions: int = 5,
) -> PortfolioResult:
    """資金を共有したときの成績。

    - 同時に持てるのは max_positions 銘柄まで。枠が空いていないシグナルは見送る。
    - 同じ日に複数のシグナルがあれば、テクニカルスコアの高い順に入る。
    - 1トレードの損失は「エントリー時点の資金 × risk_pct%」（= 1R）。損益は R × その金額。
    - 決済した日に資金に反映する（含み損益は資産曲線に入れないので、最大DDは実際より小さめに出る）。
    - 単元株の端数や、1銘柄の投資額の上限は考えない（資金に対して株価が高い銘柄も買える前提）。
    """
    candidates = sorted(trades, key=lambda t: (t.entry_date, -t.score, t.code))
    equity = initial
    open_: List[Tuple[str, Trade, float]] = []  # (決済日, トレード, 1R の金額)
    curve: List[Tuple[str, float]] = [(start, initial)]
    taken = skipped = max_concurrent = 0

    def realize_until(day: Optional[str]) -> None:
        nonlocal equity
        open_.sort(key=lambda x: x[0])
        while open_ and (day is None or open_[0][0] < day):
            exit_date, t, risk_amount = open_.pop(0)
            equity += risk_amount * t.r_multiple
            curve.append((exit_date, equity))

    for t in candidates:
        realize_until(t.entry_date)
        if len(open_) >= max_positions:
            skipped += 1
            continue
        open_.append((t.exit_date, t, equity * risk_pct / 100.0))
        taken += 1
        max_concurrent = max(max_concurrent, len(open_))
    realize_until(None)

    peak = initial
    max_dd = 0.0
    for _, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)

    year_end: Dict[str, float] = {}
    for day, eq in curve:
        year_end[day[:4]] = eq
    yearly_ret: Dict[str, float] = {}
    prev = initial
    for y in sorted(year_end):
        yearly_ret[y] = (year_end[y] / prev - 1.0) * 100.0 if prev > 0 else 0.0
        prev = year_end[y]

    years = _years_between(start, end)
    cagr = ((equity / initial) ** (1.0 / years) - 1.0) * 100.0 if equity > 0 else -100.0
    return PortfolioResult(
        initial=initial, final=equity, cagr_pct=cagr, max_dd_pct=max_dd,
        taken=taken, skipped=skipped, years=years, yearly_return_pct=yearly_ret,
        max_concurrent=max_concurrent,
    )
