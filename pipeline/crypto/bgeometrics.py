"""BGeometrics 免费接口（bitcoin-data.com）。

免费层大约每小时 8 次、每天 15 次，部分序列滞后约 7 天。
这里固定 5 个端点：短线成本、长线成本、长线 SOPR、1 年以上币龄、比特币主导率。
未平仓和 ETF 不走这里。资金费率的月包不含当月，缺的那一段才额外要一次 funding-rate。
"""

from __future__ import annotations

import csv
import time
from datetime import date, datetime
from pathlib import Path

from ..csvio import write_csv
from ..series import Series, clean
from .http import get_json
from .indicators import BG_ENDPOINTS, HODL_LTH

BASE = "https://bitcoin-data.com/v1/"
HODL_FIELDS = [
    "age_0d_1d", "age_1d_1w", "age_1w_1m", "age_1m_3m", "age_3m_6m", "age_6m_1y", *HODL_LTH,
]


def parse_day(raw: str) -> date:
    return datetime.fromisoformat(str(raw).strip()[:10]).date()


def parse_points(payload, value_key: str) -> Series:
    rows = payload if isinstance(payload, list) else [payload]
    pts = []
    for row in rows:
        if not isinstance(row, dict) or "d" not in row or value_key not in row:
            continue
        try:
            pts.append((parse_day(row["d"]), float(row[value_key])))
        except (TypeError, ValueError):
            continue
    return clean(pts)


def parse_hodl(payload) -> list[dict]:
    rows = payload if isinstance(payload, list) else [payload]
    out = []
    for row in rows:
        if not isinstance(row, dict) or "d" not in row:
            continue
        item = {"date": parse_day(row["d"]).isoformat()}
        kept = False
        for key in HODL_FIELDS:
            raw = row.get(key)
            if raw in (None, ""):
                item[key] = ""
                continue
            try:
                item[key] = f"{float(raw):.8f}"
                kept = True
            except (TypeError, ValueError):
                item[key] = ""
        if kept:
            out.append(item)
    return out


def lth_coins(row: dict) -> float | None:
    """1 年以上的币数。波段对不齐 155 天，所以用 1 年这条整档。"""
    total = 0.0
    found = False
    for key in HODL_LTH:
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            total += float(raw)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def hodl_share(row: dict) -> tuple[float, float] | None:
    """返回 (1 年以上币数, 占全部波段的百分比)。分母用这一行里有的全部波段。"""
    lth = lth_coins(row)
    if lth is None:
        return None
    rest = 0.0
    for key, raw in row.items():
        if not str(key).startswith("age_") or key in HODL_LTH or raw in (None, ""):
            continue
        try:
            rest += float(raw)
        except (TypeError, ValueError):
            continue
    den = lth + rest
    if den <= 0:
        return None
    return lth, lth / den * 100


def parse_funding(payload) -> list[dict]:
    """转成币安资金费率表的列：time、rate（小数，不是百分数）。"""
    rows = payload if isinstance(payload, list) else [payload]
    out = []
    for row in rows:
        if not isinstance(row, dict) or "d" not in row or "fundingRate" not in row:
            continue
        raw = str(row["d"]).strip()
        stamp = (raw[:10] + "T00:00:00Z") if len(raw) == 10 else raw[:19].replace(" ", "T") + "Z"
        try:
            rate = float(row["fundingRate"])
        except (TypeError, ValueError):
            continue
        out.append({"time": stamp, "rate": f"{rate:.8f}"})
    return out


def fetch_funding(start: str) -> list[dict]:
    """币安月包不含当月。这里补 start 之后的 8 小时费率，免费层大约滞后 7 天。"""
    return parse_funding(get_json(f"{BASE}funding-rate?startday={start}"))


def _fetch(endpoint: str):
    return get_json(BASE + endpoint)


def update(raw: Path) -> dict[str, str | None]:
    """拉 5 个端点。某一个失败不影响其他的，调用方沿用旧文件。"""
    errors: dict[str, str | None] = {}
    for key, (endpoint, value_key) in BG_ENDPOINTS.items():
        path = raw / f"bg_{key}.csv"
        try:
            payload = _fetch(endpoint)
            pts = parse_points(payload, value_key)
            if not pts:
                raise ValueError("没有数据")
            write_csv(path, [{"date": d.isoformat(), "value": v} for d, v in pts], ["date", "value"])
            errors[key] = None
        except Exception as exc:  # noqa: BLE001
            errors[key] = str(exc)[:200]
        time.sleep(0.4)
    path = raw / "bg_hodl.csv"
    try:
        rows = parse_hodl(_fetch("hodl-waves-supply"))
        if not rows:
            raise ValueError("没有数据")
        write_csv(path, rows, ["date", *HODL_FIELDS])
        errors["hodl"] = None
    except Exception as exc:  # noqa: BLE001
        errors["hodl"] = str(exc)[:200]
    return errors


def load_value(path: Path) -> Series:
    if not path.exists():
        return []
    pts = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                pts.append((date.fromisoformat(row["date"][:10]), float(row["value"])))
            except (KeyError, ValueError):
                continue
    return clean(pts)


def load_hodl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))
