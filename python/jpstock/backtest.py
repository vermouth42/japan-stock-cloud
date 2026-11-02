"""スイング戦略のバックテスト（TradingView の JP_SwingDay_Strategy と同じ約定モデル）。

約定モデル
- シグナルは終値で判定し、翌営業日の始値で成行エントリー。
- 損切り・トレーリング・第1利確の水準は「前日の終値時点」で更新され、当日の値動きで約定する。
- 同じ日に損切りと利確の両方に届いた場合は、保守的に損切りが先とみなす。
- 窓を空けて損切りラインを超えた場合は始値で約定する。
- 25日線割れ・期間満了は終値で判定し、翌日始値で決済する。
- 手数料 0（SBI・楽天など国内株の売買手数料無料プランを想定）。スリッページは ticks で指定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Dict, List, Optional, Sequence

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
    analysis: Optional[Dict[str, List]] = None,
) -> List[Trade]:
    """1銘柄分のトレード一覧を返す。slippage は価格単位（例: 1円）。

    analysis を渡すと analyze() の代わりにそれを使う（テスト用）。
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
            if side != 0:
                risk = p.stop_atr * atr[i]
                pending = {
                    "side": side,
                    "setup": setup,
                    "date": b.date,
                    "stop": b.close - side * risk,
                    "tp": b.close + side * p.tp_r * risk,
                    "risk": risk,
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
