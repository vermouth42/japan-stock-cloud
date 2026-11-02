"""テクニカル指標（TradingView の ta.* と同じ定義）。

すべて「古い順」のリストを受け取り、同じ長さのリストを返す。
計算できない位置は None（Pine の na に相当）。
"""

from __future__ import annotations

from typing import List, Optional, Sequence

Num = Optional[float]
Series = List[Num]


def _check_length(length: int) -> None:
    if length <= 0:
        raise ValueError("length must be positive")


def sma(values: Sequence[Num], length: int) -> Series:
    """単純移動平均（ta.sma）。ウィンドウ内に None があれば None。"""
    _check_length(length)
    out: Series = [None] * len(values)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(window) / length  # type: ignore[arg-type]
    return out


def rma(values: Sequence[Num], length: int) -> Series:
    """Wilder の移動平均（ta.rma）。

    最初の length 本の SMA で初期化し、以後 alpha = 1 / length で平滑化する。
    """
    _check_length(length)
    out: Series = [None] * len(values)
    alpha = 1.0 / length
    prev: Num = None
    seed: List[float] = []
    for i, v in enumerate(values):
        if v is None:
            if prev is None:
                seed = []
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == length:
                prev = sum(seed) / length
                out[i] = prev
            continue
        prev = alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> Series:
    """トゥルーレンジ（ta.tr(true)）。先頭バーは 高値−安値。"""
    out: Series = []
    for i in range(len(close)):
        hl = high[i] - low[i]
        if i == 0:
            out.append(hl)
            continue
        prev_close = close[i - 1]
        out.append(max(hl, abs(high[i] - prev_close), abs(low[i] - prev_close)))
    return out


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], length: int = 14) -> Series:
    """ATR（ta.atr）= TR の Wilder 平滑。"""
    return rma(true_range(high, low, close), length)


def rsi(close: Sequence[float], length: int = 14) -> Series:
    """RSI（ta.rsi）。Wilder 平滑。下落幅の平均が 0 のときは 100。"""
    ups: Series = [None]
    downs: Series = [None]
    for i in range(1, len(close)):
        diff = close[i] - close[i - 1]
        ups.append(max(diff, 0.0))
        downs.append(max(-diff, 0.0))
    avg_up = rma(ups, length)
    avg_down = rma(downs, length)
    out: Series = []
    for up, down in zip(avg_up, avg_down):
        if up is None or down is None:
            out.append(None)
        elif down == 0:
            out.append(100.0)
        elif up == 0:
            out.append(0.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + up / down))
    return out


def highest(values: Sequence[Num], length: int) -> Series:
    """直近 length 本（当日を含む）の最大値（ta.highest）。"""
    _check_length(length)
    out: Series = [None] * len(values)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        if any(v is None for v in window):
            continue
        out[i] = max(window)  # type: ignore[type-var]
    return out


def lowest(values: Sequence[Num], length: int) -> Series:
    """直近 length 本（当日を含む）の最小値（ta.lowest）。"""
    _check_length(length)
    out: Series = [None] * len(values)
    for i in range(length - 1, len(values)):
        window = values[i - length + 1 : i + 1]
        if any(v is None for v in window):
            continue
        out[i] = min(window)  # type: ignore[type-var]
    return out


def shift(values: Sequence[Num], periods: int) -> Series:
    """periods 本前の値（Pine の x[periods]）。"""
    if periods < 0:
        raise ValueError("periods must be >= 0")
    if periods == 0:
        return list(values)
    return [None] * min(periods, len(values)) + list(values[: max(len(values) - periods, 0)])
