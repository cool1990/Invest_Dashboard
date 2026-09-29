"""读旧站公开文件里美股板块要用的部分。

- data/earnings/daily.csv：盈利跟踪笔记，收盘价、远期市盈率、30 日 EPS 修正。只留美股。
- data/sentiment/series.csv：VIX、CNN 恐贪、AAII、标普参与度等。只留 indicators.SENTIMENT 里的序列。
- data/calendar/events.json：日历全文，生成页面时再挑观察名单上的财报。

按主键合并进 data/raw/us/，旧站以后只留最近几天也不会丢掉这里的历史。
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from pathlib import Path

from ..csvio import merge, read_csv, write_csv
from .indicators import SENTIMENT

BASE = "https://raw.githubusercontent.com/cool1990/invest-dashboard_v1/main/"
EARNINGS_URL = "data/earnings/daily.csv"
SENTIMENT_URL = "data/sentiment/series.csv"
CALENDAR_URL = "data/calendar/events.json"

EARN_FIELDS = ["date", "ticker", "company", "market", "close", "rsi", "forward_pe", "target_pe",
               "triggered", "next_fy_eps", "revision_30d", "up30", "down30", "revision_signal",
               "status", "flags", "is_trading_day"]
SENT_FIELDS = ["date", "series_id", "value", "obs_date"]


def _get(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return resp.read().decode("utf-8-sig", errors="replace")


def is_us(row: dict) -> bool:
    market = (row.get("market") or "").strip().upper()
    ticker = (row.get("ticker") or "").strip()
    if not ticker or market == "HK" or ticker.endswith(".HK"):
        return False
    return market in {"", "US"}


def parse_earnings(text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        row = {k: (r.get(k) or "").strip() for k in EARN_FIELDS}
        if not row["date"] or not is_us(row):
            continue
        rows.append(row)
    return rows


def parse_sentiment(text: str) -> list[dict]:
    keep = set(SENTIMENT)
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        if (r.get("series_id") or "").strip() not in keep:
            continue
        row = {k: (r.get(k) or "").strip() for k in SENT_FIELDS}
        if not row["date"] or not row["series_id"]:
            continue
        try:
            float(row["value"])
        except ValueError:
            continue
        rows.append(row)
    return rows


def parse_calendar(text: str) -> dict:
    d = json.loads(text)
    events = []
    for e in d.get("events", []):
        events.append({k: e.get(k, "") for k in ("date", "time_bj", "category", "title", "url", "note",
                                                  "previous", "consensus", "priority")})
    return {"generated_at": d.get("generated_at", ""), "events": events}


def update(out: Path) -> dict[str, str | None]:
    """逐个下载。返回 {名称: 错误或 None}。失败的那个不改本地文件。"""
    out.mkdir(parents=True, exist_ok=True)
    errors: dict[str, str | None] = {}
    jobs = {
        "earnings": (EARNINGS_URL, "earnings.csv", EARN_FIELDS, ("date", "ticker"), parse_earnings),
        "sentiment": (SENTIMENT_URL, "sentiment.csv", SENT_FIELDS, ("date", "series_id"), parse_sentiment),
    }
    for name, (url, fname, fields, key, parse) in jobs.items():
        f = out / fname
        try:
            new = parse(_get(url))
            if not new:
                raise ValueError("没有解析出记录")
        except Exception as exc:  # noqa: BLE001
            errors[name] = str(exc)[:200]
            continue
        rows = merge(read_csv(f), new, key=lambda r, key=key: tuple(r[k] for k in key))
        write_csv(f, rows, fields)
        errors[name] = None
    try:
        cal = parse_calendar(_get(CALENDAR_URL))
        (out / "calendar.json").write_text(json.dumps(cal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        errors["calendar"] = None
    except Exception as exc:  # noqa: BLE001
        errors["calendar"] = str(exc)[:200]
    return errors


def load(out: Path) -> dict:
    cal = out / "calendar.json"
    return {
        "earnings": read_csv(out / "earnings.csv"),
        "sentiment": read_csv(out / "sentiment.csv"),
        "calendar": json.loads(cal.read_text(encoding="utf-8")) if cal.exists() else {"events": []},
    }
