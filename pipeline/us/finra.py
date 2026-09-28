"""FINRA 客户融资余额：保证金账户借方余额，月频，单位百万美元。

页面上的 Excel 从 1997 年 1 月起。链接写在页面里，变了就从页面上重新找。
下载或解析失败时保留本地已有的 data/raw/us/margin.csv。
"""

from __future__ import annotations

import re
import urllib.request
from datetime import date
from pathlib import Path

from ..csvio import read_csv, write_csv
from .xlsxio import read_sheet

PAGE = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
FALLBACK = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
UA = {"User-Agent": "Mozilla/5.0 (compatible; InvestDashboard/1.0)"}
FIELDS = ["date", "debit", "credit_cash", "credit_margin"]
_MONTH = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def xlsx_url(html: str) -> str:
    m = re.search(r'href="([^"]*margin-statistics\.xlsx)"', html)
    if not m:
        return FALLBACK
    href = m.group(1).replace("&amp;", "&")
    if href.startswith("http"):
        return href
    return "https://www.finra.org" + href


def parse_month(text: str) -> date | None:
    s = (text or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    m = re.fullmatch(r"([A-Za-z]{3})-(\d{2})", s)
    if not m or m.group(1).title() not in _MONTH:
        return None
    yy = int(m.group(2))
    year = 2000 + yy if yy < 80 else 1900 + yy
    return date(year, _MONTH[m.group(1).title()], 1)


def _num(text: str) -> str:
    s = (text or "").replace(",", "").strip()
    if not s:
        return ""
    try:
        return str(float(s))
    except ValueError:
        return ""


def parse_table(rows: list[list[str]]) -> list[dict]:
    """表头里要有 Debit Balances。日期记为当月 1 日，金额是百万美元。"""
    header = None
    for i, row in enumerate(rows):
        if any("Debit Balances" in c for c in row):
            header = i
            break
    if header is None:
        return []
    head = rows[header]
    debit_i = next(i for i, c in enumerate(head) if "Debit Balances" in c)
    cash_i = next((i for i, c in enumerate(head) if "Cash Accounts" in c), None)
    margin_i = next((i for i, c in enumerate(head) if "Securities Margin Accounts" in c and i != debit_i), None)
    out = []
    for row in rows[header + 1:]:
        month = parse_month(row[0]) if row else None
        if month is None:
            continue
        debit = _num(row[debit_i] if debit_i < len(row) else "")
        if not debit:
            continue
        out.append({
            "date": month.isoformat(),
            "debit": debit,
            "credit_cash": _num(row[cash_i]) if cash_i is not None and cash_i < len(row) else "",
            "credit_margin": _num(row[margin_i]) if margin_i is not None and margin_i < len(row) else "",
        })
    out.sort(key=lambda r: r["date"])
    return out


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def update(path: Path) -> str | None:
    """下载并覆盖写成 CSV。失败时不动已有文件，返回错误信息。"""
    try:
        html = _get(PAGE).decode("utf-8", errors="replace")
        data = _get(xlsx_url(html))
        rows = parse_table(read_sheet(data))
        if len(rows) < 12:
            return f"Excel 里只有 {len(rows)} 行，不像全历史，沿用上次的文件"
    except Exception as exc:  # noqa: BLE001
        return str(exc)[:200]
    write_csv(path, rows, FIELDS)
    return None


def load(path: Path) -> list[dict]:
    return read_csv(path)
