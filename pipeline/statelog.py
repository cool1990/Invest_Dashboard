"""判断变化日志：状态标签和上一次记录不同时追加一行。

只比较标签，不比较一句话：一句话里有具体数字，几乎每天都会变。宏观、半导体两页共用。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from .csvio import read_csv, write_csv

FIELDS = ["date", "dim", "name", "from", "to", "head", "trigger"]
DAYS = 30


def update(path: Path, today: str, current: list[tuple[str, str, str, str]],
           trigger_for: Callable[[str], str], write: bool, days: int = DAYS) -> list[dict]:
    """current：[(key, 名称, 标签, 理由)]。trigger_for(key) 返回「因为哪条数据」的一句话。

    返回最近 days 天的记录（新的在前），给页面显示。
    """
    rows = read_csv(path)
    last = {}
    for r in rows:
        last[r["dim"]] = r
    for key, name, label, head in current:
        prev = last.get(key)
        if prev and prev["to"] == label:
            continue
        rows.append({"date": today, "dim": key, "name": name, "from": prev["to"] if prev else "",
                     "to": label, "head": head, "trigger": trigger_for(key) if prev else "开始记录"})
    if write:
        write_csv(path, rows, FIELDS)
    cutoff = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    return sorted((r for r in rows if r["date"] >= cutoff), key=lambda r: r["date"], reverse=True)
