"""时间序列小工具。

序列统一用 ``list[tuple[date, float]]`` 表示，按日期升序、日期不重复。
只用 Python 标准库。
"""

from __future__ import annotations

import bisect
import math
from datetime import date, timedelta
from typing import Callable, Iterable

Series = list[tuple[date, float]]


def clean(points: Iterable[tuple[date, float]]) -> Series:
    """排序、去重（同一天保留最后一个）、去掉 NaN。"""
    out: dict[date, float] = {}
    for d, v in points:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        out[d] = float(v)
    return sorted(out.items())


def since(s: Series, start: date) -> Series:
    return [(d, v) for d, v in s if d >= start]


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def quarter_start(d: date) -> date:
    return date(d.year, (d.month - 1) // 3 * 3 + 1, 1)


def _group(s: Series, key: Callable[[date], date], how: str) -> Series:
    buckets: dict[date, list[float]] = {}
    for d, v in s:
        buckets.setdefault(key(d), []).append(v)
    out = []
    for k in sorted(buckets):
        vals = buckets[k]
        if how == "last":
            out.append((k, vals[-1]))
        elif how == "mean":
            out.append((k, sum(vals) / len(vals)))
        elif how == "sum":
            out.append((k, sum(vals)))
        else:
            raise ValueError(how)
    return out


def to_monthly(s: Series, how: str = "last") -> Series:
    """按自然月聚合，日期记为当月 1 日。"""
    return _group(s, month_start, how)


def to_quarterly(s: Series, how: str = "mean") -> Series:
    return _group(s, quarter_start, how)


def to_annual(s: Series, how: str = "mean") -> Series:
    return _group(s, lambda d: date(d.year, 1, 1), how)


def to_weekly(s: Series) -> Series:
    """日频压成周频：每个 ISO 周取最后一个观测，日期保留该观测的真实日期。"""
    out: dict[tuple[int, int], tuple[date, float]] = {}
    for d, v in s:
        out[d.isocalendar()[:2]] = (d, v)
    return sorted(out.values())


def _shift_month(d: date, n: int) -> date:
    m = d.year * 12 + d.month - 1 + n
    return date(m // 12, m % 12 + 1, 1)


def _lag_key(d: date, periods: int, freq: str) -> date:
    if freq == "M":
        return _shift_month(d, -periods)
    if freq == "Q":
        return _shift_month(d, -3 * periods)
    if freq == "A":
        return date(d.year - periods, 1, 1)
    raise ValueError(freq)


def pct_change(s: Series, periods: int, freq: str, annualize: int | None = None) -> Series:
    """按日历找 periods 期之前的值算变化率（%）。

    freq 为 M / Q / A；期间有缺口时，按日历找不到基期的点直接跳过，
    不会把相邻两个点误当成「上一期」。annualize 给出每年期数时，按复利年化。
    """
    lookup = dict(s)
    out = []
    for d, v in s:
        base = lookup.get(_lag_key(d, periods, freq))
        if base is None or base == 0:
            continue
        ratio = v / base
        if annualize:
            if ratio <= 0:
                continue
            out.append((d, (ratio ** (annualize / periods) - 1) * 100))
        else:
            out.append((d, (ratio - 1) * 100))
    return out


def diff(s: Series, periods: int, freq: str) -> Series:
    lookup = dict(s)
    out = []
    for d, v in s:
        base = lookup.get(_lag_key(d, periods, freq))
        if base is not None:
            out.append((d, v - base))
    return out


def diff_obs(s: Series, periods: int = 1) -> Series:
    """按观测顺序相减，用于周频这类没有固定日历间隔的序列。"""
    return [(s[i][0], s[i][1] - s[i - periods][1]) for i in range(periods, len(s))]


def rolling(s: Series, n: int, how: str = "mean", freq: str = "M") -> Series:
    """月度滚动窗口。窗口里缺任何一个月就不出值。"""
    lookup = dict(s)
    out = []
    for d, _ in s:
        vals = []
        for k in range(n):
            v = lookup.get(_lag_key(d, k, freq))
            if v is None:
                break
            vals.append(v)
        if len(vals) == n:
            out.append((d, sum(vals) if how == "sum" else sum(vals) / n))
    return out


def rolling_obs(s: Series, n: int) -> Series:
    """按观测个数滚动平均（周频用）。"""
    return [(s[i][0], sum(v for _, v in s[i - n + 1 : i + 1]) / n) for i in range(n - 1, len(s))]


def combine(fn: Callable[..., float | None], *series: Series) -> Series:
    """按相同日期把几条序列逐点组合，任一缺值则跳过。"""
    if not series:
        return []
    maps = [dict(s) for s in series]
    common = set(maps[0])
    for m in maps[1:]:
        common &= set(m)
    out = []
    for d in sorted(common):
        v = fn(*(m[d] for m in maps))
        if v is not None:
            out.append((d, v))
    return out


def asof(s: Series, d: date, max_gap_days: int | None = None) -> float | None:
    """取 d 当天或之前最近的一个值。"""
    if not s:
        return None
    dates = [x[0] for x in s]
    i = bisect.bisect_right(dates, d) - 1
    if i < 0:
        return None
    if max_gap_days is not None and (d - s[i][0]).days > max_gap_days:
        return None
    return s[i][1]


def asof_align(base: Series, other: Series, max_gap_days: int = 7) -> Series:
    """把 other 按 base 的日期取「当日或之前」的值。"""
    dates = [x[0] for x in other]
    out = []
    for d, _ in base:
        i = bisect.bisect_right(dates, d) - 1
        if i >= 0 and (d - other[i][0]).days <= max_gap_days:
            out.append((d, other[i][1]))
    return out


def splice(old: Series, new: Series) -> Series:
    """new 开始之前用 old，之后用 new（例如 IOER 接 IORB）。"""
    if not new:
        return old
    first = new[0][0]
    return [p for p in old if p[0] < first] + new


def scale(s: Series, k: float) -> Series:
    return [(d, v * k) for d, v in s]


def mean_std(values: list[float]) -> tuple[float, float]:
    n = len(values)
    if n < 2:
        return (values[0] if values else 0.0, 0.0)
    m = sum(values) / n
    var = sum((x - m) ** 2 for x in values) / (n - 1)
    return m, math.sqrt(var)


def percentile_rank(values: list[float], x: float) -> float:
    """x 在 values 中的百分位（0–100），相等值算一半。"""
    if not values:
        return float("nan")
    below = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return (below + 0.5 * equal) / len(values) * 100


def shift_days(d: date, days: int) -> date:
    return d + timedelta(days=days)
