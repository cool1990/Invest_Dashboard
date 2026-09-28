"""另类恐贪指数。alternative.me 公开接口，limit=0 为全部历史。"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from ..csvio import write_csv
from ..series import Series, clean
from .http import get_json

URL = "https://api.alternative.me/fng/?limit=0&format=json"


def parse(payload: dict) -> Series:
    pts = []
    for row in (payload or {}).get("data") or []:
        try:
            day = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc).date()
            pts.append((day, float(row["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    return clean(pts)


def update(path: Path) -> tuple[Series, str | None]:
    try:
        pts = parse(get_json(URL))
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    if not pts:
        return [], "没有数据"
    write_csv(path, [{"date": d.isoformat(), "value": v} for d, v in pts], ["date", "value"])
    return pts, None


def load(path: Path) -> Series:
    if not path.exists():
        return []
    pts = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                pts.append((datetime.fromisoformat(row["date"]).date(), float(row["value"])))
            except (KeyError, ValueError):
                continue
    return clean(pts)
