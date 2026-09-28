"""台湾上市公司月营收。

当月：证交所 OpenAPI t187ap05_L（JSON，全部上市公司最近一个月，含去年同月营收）。
历史：公开资讯观测站旧版每月汇总页 t21sc03（HTML，Big5），只在本地历史不足时回补一次。
单位：千元新台币。月营收一般在次月 10 日前公布。
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import date
from pathlib import Path

from ..csvio import merge, read_csv, write_csv
from .indicators import TWSE

OPENAPI = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
HISTORY = "https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{roc}_{m}_0.html"
FIELDS = ["period", "code", "name", "revenue", "revenue_ly"]
BACKFILL_FROM = (2019, 1)
UA = {"User-Agent": "Mozilla/5.0 (compatible; Invest_Dashboard)"}


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def roc_period(s: str) -> str | None:
    """「11508」「115/08」→ 2026-08。"""
    m = re.fullmatch(r"(\d{2,3})/?(\d{1,2})", str(s).strip())
    if not m:
        return None
    return f"{int(m[1]) + 1911}-{int(m[2]):02d}"


def _pick(r: dict, *names: str):
    for n in names:
        if n in r and r[n] not in (None, ""):
            return r[n]
    return None


def parse_openapi(text: str, codes=TWSE) -> list[dict]:
    rows = []
    for r in json.loads(text):
        code = str(_pick(r, "公司代號", "Code") or "").strip()
        if code not in codes:
            continue
        period = roc_period(_pick(r, "資料年月", "DataYearMonth") or "")
        rev = _num(_pick(r, "營業收入-當月營收", "Revenue"))
        if not period or rev is None:
            continue
        ly = _num(_pick(r, "營業收入-去年當月營收", "RevenueLastYear") or "")
        rows.append({"period": period, "code": code, "name": codes[code][0], "revenue": rev,
                     "revenue_ly": "" if ly is None else ly})
    return rows


_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def parse_history(html: str, period: str, codes=TWSE) -> list[dict]:
    """每月汇总页：列依次是 代號、名稱、當月營收、上月營收、去年當月營收……"""
    rows = []
    for tr in _TR.findall(html):
        cells = [_TAG.sub("", c).replace("&nbsp;", "").strip() for c in _TD.findall(tr)]
        if len(cells) < 5 or cells[0] not in codes:
            continue
        rev, ly = _num(cells[2]), _num(cells[4])
        if rev is None:
            continue
        rows.append({"period": period, "code": cells[0], "name": codes[cells[0]][0], "revenue": rev,
                     "revenue_ly": "" if ly is None else ly})
    return rows


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read()


def _months(start: tuple[int, int], end: date):
    y, m = start
    while (y, m) < (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def update(path: Path, today: date) -> tuple[list[dict], str | None]:
    old = read_csv(path)
    key = lambda r: (r["period"], r["code"])  # noqa: E731
    err = None
    new: list[dict] = []
    try:
        new = parse_openapi(_get(OPENAPI).decode("utf-8-sig", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        err = f"OpenAPI：{str(exc)[:160]}"
    have = {r["period"] for r in old} | {r["period"] for r in new}
    if len(have) < 24:  # 历史不足两年才回补，回补过一次后不再请求
        missing = [(y, m) for y, m in _months(BACKFILL_FROM, today) if f"{y}-{m:02d}" not in have]
        fails = 0
        for y, m in missing:
            try:
                html = _get(HISTORY.format(roc=y - 1911, m=m)).decode("cp950", errors="replace")
                new += parse_history(html, f"{y}-{m:02d}")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                if fails >= 3:
                    err = (err + "；" if err else "") + f"历史回补：{str(exc)[:160]}"
                    break
            time.sleep(0.5)
    rows = merge(old, new, key)
    if new:
        write_csv(path, rows, FIELDS)
    return rows, err
