"""美股公司季度财务数：SEC XBRL companyfacts（免费，要在 User-Agent 里写明是谁）。

取资本开支、营收、销货成本、存货。10-Q 里的现金流量表是年初至今的累计数，
这里把累计数差分成单季：同一个起点、终点相差约一个季度的两条，相减就是后一季；
Q4 = 全年 − 前三季累计。利润表本身有单季数，直接用。
季度按财报期末所在的日历季度归档（甲骨文 5 月、英伟达 1 月结账，按期末月份算）。
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import date
from pathlib import Path

from ..csvio import merge, read_csv, write_csv
from .indicators import SEC

URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
# SEC 要求 User-Agent 写成「名称 邮箱」，格式不对会返回 403；另外每秒不超过 10 次请求
UA = {"User-Agent": "ray raycao2023@gmail.com", "Accept": "application/json"}
PAUSE_SEC = 0.3
FIELDS = ["ticker", "item", "end", "value", "concept"]

# 项目 → 按顺序尝试的 us-gaap 科目；取最近期末最新的那个
CONCEPTS = {
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "cogs": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
    "inventory": ("InventoryNet",),
}
ITEMS = {"cloud": ("capex",), "ai": ("revenue", "cogs", "inventory"), "memory": ("revenue", "cogs", "inventory"),
         "analog": ("revenue", "cogs", "inventory"), "equip": ("revenue",)}
INSTANT = {"inventory"}
START_YEAR = 2015


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _facts(units: dict) -> list[dict]:
    """同一 (start, end) 保留最后提交的一条。"""
    best: dict[tuple, dict] = {}
    for f in units.get("USD", []):
        if f.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
            continue
        k = (f.get("start"), f["end"])
        if k not in best or f.get("filed", "") >= best[k].get("filed", ""):
            best[k] = f
    return list(best.values())


def quarterly(units: dict) -> list[tuple[date, float]]:
    """期间型科目 → 单季序列 [(期末, 值)]。"""
    facts = [f for f in _facts(units) if f.get("start")]
    q: dict[date, float] = {}
    by_start: dict[str, list[dict]] = {}
    for f in facts:
        days = (_d(f["end"]) - _d(f["start"])).days
        if 80 <= days <= 100:
            q[_d(f["end"])] = float(f["val"])
        by_start.setdefault(f["start"], []).append(f)
    # 累计数差分：同一起点，终点相差 80–100 天
    for fs in by_start.values():
        fs.sort(key=lambda f: f["end"])
        for a, b in zip(fs, fs[1:]):
            gap = (_d(b["end"]) - _d(a["end"])).days
            end = _d(b["end"])
            if 80 <= gap <= 100 and end not in q:
                q[end] = float(b["val"]) - float(a["val"])
    return sorted((d, v) for d, v in q.items() if d.year >= START_YEAR)


def instant(units: dict) -> list[tuple[date, float]]:
    out = {_d(f["end"]): float(f["val"]) for f in sorted(_facts(units), key=lambda f: f.get("filed", ""))
           if not f.get("start")}
    return sorted((d, v) for d, v in out.items() if d.year >= START_YEAR)


def extract(ticker: str, facts: dict) -> list[dict]:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    group = SEC[ticker][2]
    rows = []
    for item in ITEMS[group]:
        best: tuple[str, list] | None = None
        for c in CONCEPTS[item]:
            if c not in gaap:
                continue
            s = (instant if item in INSTANT else quarterly)(gaap[c].get("units", {}))
            if s and (best is None or s[-1][0] > best[1][-1][0]):
                best = (c, s)
        if best:
            rows += [{"ticker": ticker, "item": item, "end": d.isoformat(), "value": v, "concept": best[0]}
                     for d, v in best[1]]
    return rows


def update(path: Path) -> tuple[list[dict], dict[str, str]]:
    """逐家下载。某家失败时保留它的旧记录。返回 (全部记录, {代号: 错误})。"""
    old = read_csv(path)
    errors: dict[str, str] = {}
    new: list[dict] = []
    done: set[str] = set()
    for ticker, (cik, _, _) in SEC.items():
        try:
            req = urllib.request.Request(URL.format(cik=cik), headers=UA)
            with urllib.request.urlopen(req, timeout=60) as resp:
                rows = extract(ticker, json.loads(resp.read()))
            if rows:
                new += rows
                done.add(ticker)
            else:
                errors[ticker] = "没有找到对应科目"
        except Exception as exc:  # noqa: BLE001
            errors[ticker] = str(exc)[:160]
        time.sleep(PAUSE_SEC)
    kept = [r for r in old if r["ticker"] not in done]  # 下载成功的公司整体替换（修订会覆盖）
    rows = merge(kept, new, key=lambda r: (r["ticker"], r["item"], r["end"]))
    if new:
        write_csv(path, rows, FIELDS)
    return rows, errors
