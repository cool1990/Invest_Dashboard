"""Coin Metrics 社区接口：价格、市值、MVRV、流通量、每日发行量。

不需要 key。实现市值不在免费目录里，实现价格在 build 里用价格 / MVRV。
发行量用 IssTotNtv（新产出的币，含手续费），IssContNtv 不在免费目录里。
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from ..csvio import write_csv
from ..series import Series, clean
from .http import get_json
from .indicators import CM_METRICS

ROOT = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
FIELDS = ["date", *CM_METRICS]


def _day(stamp: str) -> date:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()


def parse_rows(payload: dict, metrics: tuple[str, ...] = CM_METRICS) -> list[dict]:
    out = []
    for row in payload.get("data") or []:
        item = {"date": _day(row["time"]).isoformat()}
        for m in metrics:
            raw = row.get(m)
            item[m] = "" if raw in (None, "") else raw
        if any(item[m] for m in metrics):
            out.append(item)
    return out


def fetch_asset(asset: str, metrics: tuple[str, ...], start: str = "2011-01-01") -> list[dict]:
    """按页把日频拉完。社区接口大约每 6 秒 10 次，这里一次尽量要满。"""
    url = f"{ROOT}?assets={asset}&metrics={','.join(metrics)}&frequency=1d&page_size=10000&start_time={start}"
    rows: list[dict] = []
    seen: set[str] = set()
    pages = 0
    while url and url not in seen and pages < 20:
        seen.add(url)
        pages += 1
        payload = get_json(url)
        rows.extend(parse_rows(payload, metrics))
        url = payload.get("next_page_url") or ""
        if not payload.get("data"):
            break
    return rows


def update_btc(path: Path) -> tuple[list[dict], str | None]:
    try:
        rows = fetch_asset("btc", CM_METRICS)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    if not rows:
        return [], "没有数据"
    write_csv(path, rows, FIELDS)
    return rows, None


def update_eth(path: Path) -> tuple[list[dict], str | None]:
    """以太坊价格，只在币安日线失败时用来算 ETH/BTC。"""
    try:
        rows = fetch_asset("eth", ("PriceUSD",), start="2015-08-01")
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    if not rows:
        return [], "没有数据"
    write_csv(path, [{"date": r["date"], "PriceUSD": r["PriceUSD"]} for r in rows], ["date", "PriceUSD"])
    return rows, None


def load_btc(path: Path) -> dict[str, Series]:
    out: dict[str, list] = {m: [] for m in CM_METRICS}
    if not path.exists():
        return {m: [] for m in CM_METRICS}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                d = date.fromisoformat(row["date"][:10])
            except ValueError:
                continue
            for m in CM_METRICS:
                raw = (row.get(m) or "").strip()
                if not raw:
                    continue
                try:
                    out[m].append((d, float(raw)))
                except ValueError:
                    continue
    return {m: clean(s) for m, s in out.items()}


def load_price(path: Path, col: str = "PriceUSD") -> Series:
    if not path.exists():
        return []
    pts = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = (row.get(col) or "").strip()
            if not raw:
                continue
            try:
                pts.append((date.fromisoformat(row["date"][:10]), float(raw)))
            except ValueError:
                continue
    return clean(pts)
