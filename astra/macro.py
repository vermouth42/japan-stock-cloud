"""Point-in-time macro joins. No provider credentials, downloads, or trade signals.

Keep original publication vintages. A revised value becomes usable only at its
own available_at timestamp. Do not set available_at from the observation date.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite


@dataclass(frozen=True)
class Observation:
    series: str
    observation_time: datetime
    available_at: datetime
    value: float
    source: str
    vintage: str

    def validate(self):
        if not self.series or not self.source or not self.vintage:
            raise ValueError("series/source/vintage evidence required")
        for t in (self.observation_time, self.available_at):
            if not isinstance(t, datetime) or t.tzinfo is None:
                raise ValueError("timezone-aware timestamps required")
        if self.available_at < self.observation_time:
            raise ValueError("value available before observation")
        if type(self.value) not in (int, float) or not isfinite(self.value):
            raise ValueError("finite numeric value required")


def asof(observations: list[Observation], series: str, at: datetime,
         max_age: timedelta) -> Observation | None:
    """Latest known observation/vintage, or None when absent/stale.

    max_age is measured from the observation, so a revision cannot make an old
    market quote appear fresh. Each series needs an explicitly chosen TTL.
    Conflicting records at the same observation/publication time fail closed.
    """
    if at.tzinfo is None or max_age <= timedelta(0):
        raise ValueError("aware decision timestamp and positive max_age required")
    candidates = {}
    for item in observations:
        if item.series != series:
            continue
        item.validate()
        if item.available_at > at or item.observation_time > at:
            continue
        key = (item.observation_time, item.available_at)
        if key in candidates and candidates[key] != item:
            raise ValueError("conflicting macro vintages")
        candidates[key] = item
    if not candidates:
        return None
    latest = candidates[max(candidates)]
    return latest if at - latest.observation_time <= max_age else None


def context(observations: list[Observation], at: datetime,
            max_age_by_series: dict[str, timedelta]) -> dict:
    """Descriptive context only; missing data never becomes a bullish signal."""
    selected = {s: asof(observations, s, at, age)
                for s, age in max_age_by_series.items()}
    return {"observations": selected,
            "missing": tuple(s for s, v in selected.items() if v is None),
            "go_allowed": False}
