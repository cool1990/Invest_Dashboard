"""从 FRED 公开 CSV 下载序列，不需要 API key。

每条序列全历史下载，原样存到 data/raw/fred/<ID>.csv（列：date,value）。
下载失败时保留旧文件，并在状态里记下失败原因。

不要给请求加自定义 User-Agent：cool1990/invest-dashboard_v1 仓库（原 macro-dashboard） 2026-09-27 实测，
自定义 UA 会让 FRED 在 HTTP/2 上立刻报错、在 HTTP/1.1 上挂起。
"""

from __future__ import annotations

import csv
import io
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from .series import Series

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"
TIMEOUT_SEC = 60
RETRIES = 3
WORKERS = 6


class FetchError(Exception):
    pass


def parse_csv(text: str) -> Series:
    """解析 FRED CSV。缺失值（. / 空）跳过。"""
    rows: Series = []
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    next(reader, None)
    for row in reader:
        if len(row) < 2:
            continue
        raw = row[1].strip()
        if raw in {"", ".", "NA", "ND"}:
            continue
        try:
            rows.append((datetime.strptime(row[0].strip(), "%Y-%m-%d").date(), float(raw)))
        except ValueError:
            continue
    rows.sort()
    return rows


def download(series_id: str) -> str:
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(URL.format(id=series_id), timeout=TIMEOUT_SEC) as resp:
                text = resp.read().decode("utf-8-sig", errors="replace")
            head = text[:300].lower()
            if "observation_date" not in head and "date" not in head.split("\n", 1)[0]:
                raise FetchError(f"返回的不是 CSV：{text[:120]!r}")
            if "<html" in head or "<!doctype" in head:
                raise FetchError("返回了网页而不是 CSV")
            return text
        except urllib.error.HTTPError as exc:
            last = FetchError(f"HTTP {exc.code}")
            if exc.code == 404:
                break
        except (urllib.error.URLError, TimeoutError, OSError, FetchError) as exc:
            last = exc
        if attempt < RETRIES:
            time.sleep(2 ** attempt)
    raise FetchError(str(last))


def write_series(path: Path, s: Series) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "value"])
        for d, v in s:
            w.writerow([d.isoformat(), repr(v) if v != int(v) else str(int(v))])


def read_series(path: Path) -> Series:
    if not path.exists():
        return []
    return parse_csv(path.read_text(encoding="utf-8"))


def fetch_all(ids: list[str], raw_dir: Path, old_status: dict | None = None) -> dict:
    """下载全部序列。返回每条序列的状态：ok、last_obs、error、fetched_at。"""
    old_status = old_status or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def one(sid: str) -> tuple[str, dict]:
        prev = old_status.get(sid, {})
        try:
            s = parse_csv(download(sid))
            if not s:
                raise FetchError("没有有效观测")
            write_series(raw_dir / f"{sid}.csv", s)
            return sid, {"ok": True, "first_obs": s[0][0].isoformat(), "last_obs": s[-1][0].isoformat(),
                         "fetched_at": now, "last_ok_at": now}
        except Exception as exc:  # noqa: BLE001 单条失败不影响其他序列
            print(f"FAIL {sid}: {exc}", flush=True)
            return sid, {**prev, "ok": False, "error": str(exc)[:200], "fetched_at": now}

    status: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for sid, st in pool.map(one, ids):
            status[sid] = st
    return status


def load_all(ids: list[str], raw_dir: Path) -> dict[str, Series]:
    return {sid: read_series(raw_dir / f"{sid}.csv") for sid in ids}


def today() -> date:
    return datetime.now(timezone.utc).date()
