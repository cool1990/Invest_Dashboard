"""市场隐含 EFFR（下月 / 年底 / 明年底）。

这三项来自每天早晨的笔记，已经由 cool1990/invest-dashboard_v1 整理进
data/sentiment/series.csv。这里读那份公开文件，只挑出三行 EFFR，
按 (date, series_id) 合并进本仓库的 data/macro/effr_expectations.csv。
读取失败时保留本地已有记录。
"""

from __future__ import annotations

import csv
import io
import urllib.request
from pathlib import Path

SOURCE_URL = "https://raw.githubusercontent.com/cool1990/invest-dashboard_v1/main/data/sentiment/series.csv"
KEYS = ("effr_next", "effr_year", "effr_ny")
FIELDS = ["date", "series_id", "value", "obs_date", "remark"]


def parse_source(text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        if r.get("series_id") not in KEYS:
            continue
        try:
            float(r.get("value", ""))
        except ValueError:
            continue
        rows.append({k: (r.get(k) or "").strip() for k in FIELDS})
    return rows


def read_local(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    by_key = {(r["date"], r["series_id"]): r for r in old}
    for r in new:
        by_key[(r["date"], r["series_id"])] = r
    return [by_key[k] for k in sorted(by_key)]


def update(path: Path) -> tuple[list[dict], str | None]:
    """返回合并后的全部记录，以及错误信息（成功时为 None）。"""
    old = read_local(path)
    try:
        with urllib.request.urlopen(SOURCE_URL, timeout=30) as resp:
            new = parse_source(resp.read().decode("utf-8-sig", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return old, str(exc)[:200]
    rows = merge(old, new)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows, None
