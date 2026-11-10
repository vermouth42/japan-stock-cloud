"""prices.csv（GitHub Actions が毎日更新する日足データ）の読み込み。"""

from __future__ import annotations

import csv
from collections import defaultdict
from typing import Dict, List

from .rules import Bar

# prices.csv のヘッダー名
COL_DATE = "日付"
COL_CODE = "コード"
COL_OPEN = "始値"
COL_HIGH = "高値"
COL_LOW = "安値"
COL_CLOSE = "終値"
COL_VOLUME = "出来高"


def load_prices(path: str) -> Dict[str, List[Bar]]:
    """銘柄コード → 日付昇順の Bar リスト。"""
    by_code: Dict[str, List[Bar]] = defaultdict(list)
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = {COL_DATE, COL_CODE, COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE, COL_VOLUME} - set(
            reader.fieldnames or []
        )
        if missing:
            raise ValueError(f"{path}: 列が見つかりません: {sorted(missing)}")
        for row in reader:
            try:
                bar = Bar(
                    date=row[COL_DATE],
                    open=float(row[COL_OPEN]),
                    high=float(row[COL_HIGH]),
                    low=float(row[COL_LOW]),
                    close=float(row[COL_CLOSE]),
                    volume=float(row[COL_VOLUME] or 0),
                )
            except (TypeError, ValueError):
                continue
            by_code[row[COL_CODE]].append(bar)
    for bars in by_code.values():
        bars.sort(key=lambda b: b.date)
    return dict(by_code)
