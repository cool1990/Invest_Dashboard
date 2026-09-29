"""读旧站 cool1990/invest-dashboard_v1 的公开文件：存储价格、GPU 租金、OpenRouter、SiliconData、
韩国芯片出口、盈利笔记里的 EPS 修正、日历。

这些数都来自每天早晨的笔记，旧站已整理成 CSV。这里按主键合并进 data/raw/semis/oldsite/，
所以就算旧站以后只保留最近几天，本仓库的历史也不会丢。读取失败时保留本地已有记录。
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from pathlib import Path

from ..csvio import merge, read_csv, write_csv
from .indicators import EPS_KEEP

BASE = "https://raw.githubusercontent.com/cool1990/invest-dashboard_v1/main/"

# 名称 → (旧站路径, 主键列, 保留的列)
TABLES = {
    "memory": ("data/semis/memory.csv", ("date", "product"),
               ["date", "product", "value", "unit", "chg_1d_pct", "chg_7d_pct", "chg_30d_pct"]),
    "gpu": ("data/semis/gpu.csv", ("date", "gpu"),
            ["date", "gpu", "price", "unit", "chg_1d_pct", "chg_7d_pct", "chg_30d_pct", "chg_90d_pct"]),
    "openrouter": ("data/semis/openrouter.csv", ("date", "window"),
                   ["date", "window", "tokens", "unit", "change_pct", "note"]),
    "silicon": ("data/semis/silicon.csv", ("date",),
                ["date", "value", "unit", "chg_7d_pct", "chg_30d_pct", "chg_90d_pct"]),
    "korea": ("data/semis/korea.csv", ("period",),
              ["period", "d10_usd_mn", "d10_yoy", "d10_share", "d20_usd_mn", "d20_yoy", "d20_share",
               "month_usd_mn", "month_yoy", "month_share", "asof"]),
    "eps": ("data/earnings/daily.csv", ("date", "ticker"),
            ["date", "ticker", "close", "next_fy_eps", "revision_30d", "up30", "down30", "revision_signal"]),
}
CALENDAR = "data/calendar/events.json"


def _num_ok(v: str) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def parse_table(name: str, text: str) -> list[dict]:
    _, key, fields = TABLES[name]
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        row = {k: (r.get(k) or "").strip() for k in fields}
        if any(not row[k] for k in key):
            continue
        if name == "eps" and row["ticker"] not in EPS_KEEP:
            continue
        rows.append(row)
    return rows


def parse_calendar(text: str) -> dict:
    """只留半导体相关的条目：分类为半导体、带半导体标记，或观察名单上的云厂商财报。"""
    d = json.loads(text)
    keep = []
    for e in d.get("events", []):
        tags = e.get("tags") or []
        if e.get("category") == "半导体" or "半导体" in tags or any(f"（{t}）" in e.get("title", "") for t in EPS_KEEP):
            keep.append({k: e.get(k, "") for k in ("date", "time_bj", "category", "title", "url", "note",
                                                    "previous", "consensus", "priority")})
    return {"generated_at": d.get("generated_at", ""), "events": keep}


def _get(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return resp.read().decode("utf-8-sig", errors="replace")


def update(out: Path, offline: bool = False) -> dict[str, str | None]:
    """逐个文件下载并合并。返回 {名称: 错误信息或 None}。"""
    errors: dict[str, str | None] = {}
    for name, (path, key, fields) in TABLES.items():
        if offline:
            continue
        f = out / f"{name}.csv"
        try:
            new = parse_table(name, _get(path))
        except Exception as exc:  # noqa: BLE001
            errors[name] = str(exc)[:200]
            continue
        rows = merge(read_csv(f), new, key=lambda r, key=key: tuple(r[k] for k in key))
        write_csv(f, rows, fields)
        errors[name] = None
    if not offline:
        try:
            cal = parse_calendar(_get(CALENDAR))
            out.mkdir(parents=True, exist_ok=True)
            (out / "calendar.json").write_text(json.dumps(cal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            errors["calendar"] = None
        except Exception as exc:  # noqa: BLE001
            errors["calendar"] = str(exc)[:200]
    return errors


def load(out: Path) -> dict:
    data = {name: read_csv(out / f"{name}.csv") for name in TABLES}
    cal = out / "calendar.json"
    data["calendar"] = json.loads(cal.read_text(encoding="utf-8")) if cal.exists() else {"events": []}
    return data
