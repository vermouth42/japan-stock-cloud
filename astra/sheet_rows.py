"""Prepare the existing A:R / 45-session Sheets import. No remote writes."""
from collections import Counter
from datetime import date
from .market_data import validate_dataset


def prepare(rows: list[list], codes: list[str], cutoff: date) -> list[list]:
    validate_dataset(rows, codes, cutoff)
    counts = Counter()
    selected = []
    for r in sorted(rows, key=lambda r:(r[0],r[1]), reverse=True):
        if counts[r[1]] >= 45:
            continue
        counts[r[1]] += 1
        serial = (date.fromisoformat(r[0])-date(1899,12,30)).days
        # Blank B is part of the existing sheet layout; C holds the stock code.
        code = int(r[1]) if r[1].isdigit() else r[1]
        selected.append([serial, "", code] + [float(v) if v != "" else "" for v in r[2:]])
    if len(selected) != 4500 or set(counts.values()) != {45}:
        raise ValueError("Sheets import must contain 100 x 45 rows")
    return selected
