"""标普 500 前十大权重。

先试 iShares IVV 的持仓 CSV。这个地址目前经常返回产品页而不是 CSV，
这时改用同样跟踪标普 500 的 SPDR SPY 日持仓（xlsx）。
每种来源一天一行，按日期合并，所以历史从第一次成功下载开始累积。
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
from datetime import date, datetime

from ..csvio import merge, read_csv, write_csv
from .xlsxio import read_sheet

IVV_URL = ("https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/"
           "1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund")
SPY_URL = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
UA = {"User-Agent": "Mozilla/5.0 (compatible; InvestDashboard/1.0)"}
FIELDS = ["date", "top10", "names", "source"]
_SKIP_TICKER = {"", "-", "—", "N/A", "NA"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _asof(text: str) -> date | None:
    m = re.search(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%d-%b-%Y").date()
        except ValueError:
            pass
    m = re.search(r"([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{4})", text)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
        except ValueError:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _top10(pairs: list[tuple[float, str]]) -> tuple[float, str] | None:
    """pairs: (权重 %, 代码)，取得分最高的 10 只。"""
    rows = [(w, t) for w, t in pairs if t not in _SKIP_TICKER and w > 0]
    rows.sort(key=lambda x: -x[0])
    # 有的文件把 8.2% 写成 0.082
    if rows and rows[0][0] < 1:
        rows = [(w * 100, t) for w, t in rows]
    top = rows[:10]
    if len(top) < 10:
        return None
    return round(sum(w for w, _ in top), 4), ",".join(t for _, t in top)


def parse_ivv(text: str) -> dict | None:
    """IVV 持仓 CSV。开头几行是基金说明，表头一行以 Ticker 开头。返回不了就 None。"""
    if not text or text.lstrip().startswith("<"):
        return None
    lines = text.splitlines()
    asof = _asof("\n".join(lines[:30]))
    header = next((i for i, line in enumerate(lines) if line.split(",")[0].strip().strip('"') == "Ticker"), None)
    if asof is None or header is None:
        return None
    reader = csv.DictReader(io.StringIO("\n".join(lines[header:])))
    weight_key = next((k for k in (reader.fieldnames or []) if k and "weight" in k.lower()), None)
    if not weight_key:
        return None
    pairs = []
    for r in reader:
        ticker = (r.get("Ticker") or "").strip()
        asset = (r.get("Asset Class") or "Equity").strip()
        if asset and asset != "Equity":
            continue
        try:
            w = float((r.get(weight_key) or "").replace(",", ""))
        except ValueError:
            continue
        pairs.append((w, ticker))
    got = _top10(pairs)
    if not got:
        return None
    top, names = got
    return {"date": asof.isoformat(), "top10": str(top), "names": names, "source": "IVV"}


def parse_spy(data: bytes) -> dict | None:
    """SPY 日持仓。权重列已经是百分数。"""
    rows = read_sheet(data)
    asof = None
    header = None
    for i, row in enumerate(rows):
        if asof is None:
            asof = _asof(" ".join(row[:4]))
        if row and row[0].strip() == "Name" and "Ticker" in row and "Weight" in row:
            header = i
            break
    if asof is None or header is None:
        return None
    head = rows[header]
    ti, wi = head.index("Ticker"), head.index("Weight")
    pairs = []
    for row in rows[header + 1:]:
        if wi >= len(row):
            break
        ticker = row[ti].strip() if ti < len(row) else ""
        try:
            w = float(row[wi].replace(",", ""))
        except ValueError:
            if not ticker:
                break
            continue
        pairs.append((w, ticker))
    got = _top10(pairs)
    if not got:
        return None
    top, names = got
    return {"date": asof.isoformat(), "top10": str(top), "names": names, "source": "SPY"}


def update(path: Path) -> str | None:
    """成功返回 None。IVV 不行但 SPY 行，也不算失败。两个都不行才返回错误，并保留旧文件。"""
    ivv_err = ""
    row = None
    try:
        text = _get(IVV_URL).decode("utf-8-sig", errors="replace")
        row = parse_ivv(text)
        if row is None:
            ivv_err = "返回的不是持仓 CSV"
    except Exception as exc:  # noqa: BLE001
        ivv_err = str(exc)[:120]
    if row is None:
        try:
            row = parse_spy(_get(SPY_URL))
        except Exception as exc:  # noqa: BLE001
            return f"IVV：{ivv_err}；SPY：{str(exc)[:120]}"
        if row is None:
            return f"IVV：{ivv_err}；SPY 持仓解析失败"
    rows = merge(read_csv(path), [row], key=lambda r: (r["date"],))
    write_csv(path, rows, FIELDS)
    return None


def load(path: Path) -> list[dict]:
    return read_csv(path)
