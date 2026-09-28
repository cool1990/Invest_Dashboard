"""比特币主导率。

CoinGecko 的 /global 只有当前值，历史总市值要 key。
历史用 BGeometrics（在 bgeometrics.py 里下载）。这里只在它缺「今天」时补当前点。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..csvio import merge, read_csv, write_csv
from .http import get_json

URL = "https://api.coingecko.com/api/v3/global"


def fetch_today() -> tuple[str, float] | None:
    payload = get_json(URL)
    pct = (payload.get("data") or {}).get("market_cap_percentage") or {}
    if "btc" not in pct:
        return None
    updated = (payload.get("data") or {}).get("updated_at")
    if updated:
        day = datetime.fromtimestamp(int(updated), tz=timezone.utc).date().isoformat()
    else:
        day = datetime.now(timezone.utc).date().isoformat()
    return day, float(pct["btc"])


def merge_today(path, rows: list[dict]) -> tuple[list[dict], str | None]:
    """rows 是 BGeometrics 已经写好的 date,value。能拿到今天的 CoinGecko 点就补上。"""
    try:
        point = fetch_today()
    except Exception as exc:  # noqa: BLE001
        return rows or read_csv(path), str(exc)
    if point is None:
        return rows or read_csv(path), "没有主导率"
    day, value = point
    merged = merge(rows or read_csv(path), [{"date": day, "value": value}], lambda r: (r["date"],))
    write_csv(path, merged, ["date", "value"])
    return merged, None
