"""币安 BTCUSDT 永续的资金费率和未平仓，以及现货日线（算以太坊/比特币）。

fapi.binance.com 在部分网络（包括美国的 GitHub Actions）会回 451。
费率改读 data.binance.vision 的月度压缩包，未平仓改读每日 metrics 压缩包，
现货日线改读 data-api.binance.vision。都是币安同一本 BTCUSDT / ETHUSDT 的公开数据。
"""

from __future__ import annotations

import csv
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..csvio import merge, read_csv, write_csv
from ..series import Series, clean
from .http import FetchError, get_bytes, get_json

VISION = "https://data.binance.vision/data/futures/um"
SPOT = "https://data-api.binance.vision/api/v3/klines"
FAPI = "https://fapi.binance.com"
FUND_START = date(2019, 9, 1)
OI_BACKFILL_DAYS = 180
WORKERS = 6


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def parse_funding_csv(text: str) -> list[dict]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        raw_t, raw_r = row.get("calc_time"), row.get("last_funding_rate")
        if not raw_t or raw_r in (None, ""):
            continue
        try:
            stamp = _utc(int(float(raw_t))).strftime("%Y-%m-%dT%H:%M:%SZ")
            rate = float(raw_r)
        except ValueError:
            continue
        out.append({"time": stamp, "rate": f"{rate:.8f}"})
    return out


def parse_metrics_csv(text: str) -> dict | None:
    """每日 metrics 里取最后一行：当天最晚的未平仓和全账户多空比。"""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return None
    last = rows[-1]
    try:
        return {
            "date": last["create_time"][:10],
            "oi_btc": float(last["sum_open_interest"]),
            "oi_usd": float(last["sum_open_interest_value"]),
            "ls_ratio": float(last["count_long_short_ratio"]),
        }
    except (KeyError, ValueError):
        return None


def parse_klines(payload) -> Series:
    pts = []
    for row in payload or []:
        try:
            pts.append((_utc(int(row[0])).date(), float(row[4])))
        except (TypeError, ValueError, IndexError):
            continue
    return clean(pts)


def mean_recent(rows: list[dict], days: int = 7) -> float | None:
    """最近 days 天的 8 小时费率均值，返回百分数（0.01 表示 0.01%）。"""
    pts = []
    for row in rows:
        try:
            pts.append((datetime.fromisoformat(row["time"].replace("Z", "+00:00")), float(row["rate"])))
        except (KeyError, ValueError):
            continue
    if not pts:
        return None
    last = max(t for t, _ in pts)
    cutoff = last - timedelta(days=days)
    vals = [rate for t, rate in pts if cutoff < t <= last]
    if not vals:
        return None
    return sum(vals) / len(vals) * 100


def _zip_text(url: str) -> str | None:
    try:
        blob = get_bytes(url, retries=2, timeout=40)
    except FetchError:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = zf.namelist()[0]
            return zf.read(name).decode("utf-8")
    except (zipfile.BadZipFile, IndexError, UnicodeError):
        return None


def _months(start: date, end: date) -> list[date]:
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _funding_month_url(day: date) -> str:
    stamp = f"{day.year:04d}-{day.month:02d}"
    return f"{VISION}/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{stamp}.zip"


def _funding_day_url(day: date) -> str:
    stamp = day.isoformat()
    return f"{VISION}/daily/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{stamp}.zip"


def fetch_funding_vision(today: date, have: set[str] | None = None) -> list[dict]:
    """已结束的月份用月包；当月用日包。404 的月份跳过。本地已经有的旧月份不再重复下载。"""
    have = have or set()
    months = _months(FUND_START, today.replace(day=1) - timedelta(days=1))
    refresh_after = today.replace(day=1) - timedelta(days=40)
    needed = [m for m in months if m >= refresh_after or not any(t.startswith(f"{m.year:04d}-{m.month:02d}") for t in have)]
    rows: list[dict] = []
    if needed:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for text in pool.map(_zip_text, (_funding_month_url(d) for d in needed)):
                if text:
                    rows.extend(parse_funding_csv(text))
    daily = []
    d = today.replace(day=1)
    while d <= today:
        if not any(t.startswith(d.isoformat()) for t in have):
            daily.append(d)
        d += timedelta(days=1)
    if daily:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for text in pool.map(_zip_text, (_funding_day_url(d) for d in daily)):
                if text:
                    rows.extend(parse_funding_csv(text))
    return rows


def fetch_funding_fapi() -> list[dict]:
    """能访问 fapi 时按 8 小时往回翻。limit 1000，大约 333 天一页。"""
    rows: list[dict] = []
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest = int(datetime(2019, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
    while end > earliest:
        payload = get_json(f"{FAPI}/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000&endTime={end}")
        if not payload:
            break
        batch = []
        for row in payload:
            try:
                stamp = _utc(int(row["fundingTime"])).strftime("%Y-%m-%dT%H:%M:%SZ")
                batch.append({"time": stamp, "rate": f"{float(row['fundingRate']):.8f}"})
            except (KeyError, TypeError, ValueError):
                continue
        if not batch:
            break
        rows.extend(batch)
        first = min(int(row["fundingTime"]) for row in payload)
        if first >= end:
            break
        end = first - 1
        if len(payload) < 1000:
            break
    return rows


def update_funding(path: Path, today: date) -> tuple[list[dict], str | None]:
    note = None
    try:
        fresh = fetch_funding_fapi()
        if not fresh:
            raise FetchError("fapi 没有数据")
    except Exception as exc:  # noqa: BLE001
        note = f"fapi 不可用（{exc}），改用 data.binance.vision"
        have = {r["time"] for r in read_csv(path) if r.get("time")}
        fresh = fetch_funding_vision(today, have)
    merged = merge(read_csv(path), fresh, lambda r: (r["time"],))
    # 月包要等下月才出，当月改用 BGeometrics 转载的同一本费率（大约滞后 7 天）。
    latest = max((r["time"] for r in merged), default="")
    stale = not latest or latest[:10] < (today - timedelta(days=3)).isoformat()
    extra_err = None
    if stale:
        try:
            from .bgeometrics import fetch_funding
            start = latest[:10] if latest else "2024-01-01"
            extra = fetch_funding(start)
            merged = merge(merged, extra, lambda r: (r["time"],))
        except Exception as exc:  # noqa: BLE001
            extra_err = f"当月费率没补上：{exc}"
    if not merged:
        return [], extra_err or note or "没有资金费率"
    write_csv(path, merged, ["time", "rate"])
    return merged, extra_err


def _metrics_url(day: date) -> str:
    stamp = day.isoformat()
    return f"{VISION}/daily/metrics/BTCUSDT/BTCUSDT-metrics-{stamp}.zip"


def fetch_oi_days(days: list[date]) -> list[dict]:
    found = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for text in pool.map(_zip_text, (_metrics_url(d) for d in days)):
            if not text:
                continue
            row = parse_metrics_csv(text)
            if row:
                found.append({
                    "date": row["date"],
                    "oi_btc": f"{row['oi_btc']:.4f}",
                    "oi_usd": f"{row['oi_usd']:.4f}",
                    "ls_ratio": f"{row['ls_ratio']:.6f}",
                })
    return found


def update_oi(path: Path, today: date) -> tuple[list[dict], str | None]:
    old = read_csv(path)
    have = {r["date"] for r in old}
    start = today - timedelta(days=OI_BACKFILL_DAYS if len(old) < 30 else 14)
    days = []
    d = start
    while d <= today:
        if d.isoformat() not in have:
            days.append(d)
        d += timedelta(days=1)
    fresh = fetch_oi_days(days) if days else []
    merged = merge(old, fresh, lambda r: (r["date"],))
    if merged:
        write_csv(path, merged, ["date", "oi_btc", "oi_usd", "ls_ratio"])
    if not merged:
        return [], "没有未平仓"
    return merged, None if fresh or old else "没有未平仓"


def fetch_klines(symbol: str, start: date) -> Series:
    pts: Series = []
    cursor = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime.now(timezone.utc).timestamp() * 1000)
    while cursor < end:
        payload = get_json(f"{SPOT}?symbol={symbol}&interval=1d&limit=1000&startTime={cursor}")
        batch = parse_klines(payload)
        if not batch:
            break
        pts.extend(batch)
        last_ms = int(payload[-1][0])
        nxt = last_ms + 86_400_000
        if nxt <= cursor or len(payload) < 1000:
            break
        cursor = nxt
    return clean(pts)


def update_klines(path: Path, symbol: str, start: date) -> tuple[Series, str | None]:
    try:
        pts = fetch_klines(symbol, start)
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)
    if not pts:
        return [], "没有日线"
    write_csv(path, [{"date": d.isoformat(), "close": v} for d, v in pts], ["date", "close"])
    return pts, None


def load_funding(path: Path) -> list[dict]:
    return read_csv(path)


def load_oi(path: Path) -> tuple[Series, Series]:
    """未平仓名义美元、全账户多空比。"""
    oi, ls = [], []
    for row in read_csv(path):
        try:
            d = date.fromisoformat(row["date"][:10])
            oi.append((d, float(row["oi_usd"])))
            ls.append((d, float(row["ls_ratio"])))
        except (KeyError, ValueError):
            continue
    return clean(oi), clean(ls)


def load_close(path: Path) -> Series:
    pts = []
    for row in read_csv(path):
        try:
            pts.append((date.fromisoformat(row["date"][:10]), float(row["close"])))
        except (KeyError, ValueError):
            continue
    return clean(pts)
