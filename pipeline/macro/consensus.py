"""市场一致预期与模型预测。

1. 市场一致预期：ForexFactory 公开的本周日历 JSON（非官方接口，免费、无 key）。
   只留美国的高、中影响条目，按 (发布时间, 标题) 累积到 data/macro/consensus.csv。
   发布前多次看到同一条目时，用最后一次看到的预期；发布后不再改。
   data/macro/consensus_manual.csv（同样的列）可以手工补录或覆盖。
2. 模型预测：克利夫兰联储通胀 Nowcast（同比），存 data/macro/nowcast.csv。

实际值不在这里取，由 build.py 从 FRED 序列里按参考期找。
"""

from __future__ import annotations

import csv
import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
CLEVELAND_URL = "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_year.json"
FIELDS = ["release_at", "title", "impact", "forecast", "previous", "first_seen", "last_seen"]
NOWCAST_FIELDS = ["period", "measure", "nowcast", "actual", "fetched_at"]
BJ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class EventSpec:
    """日历标题 → 本站指标。

    ref: 参考期怎么从发布日推：("M", 天数) 表示「发布日往前推若干天所在的月份」，
    ("Q", 天数) 同理取季度，("W", 天数) 表示发布日往前推若干天那一天（周频）。
    up: 数值高于预期意味着什么（"强" / "弱" / "热" / "冷"）。
    """

    key: str
    name: str
    unit: str
    ref: tuple[str, int]
    up: str


EVENTS: dict[str, EventSpec] = {
    "Non-Farm Employment Change": EventSpec("nfp", "非农新增就业", "千人", ("M", 5), "强"),
    "Unemployment Rate": EventSpec("unrate", "失业率", "%", ("M", 5), "弱"),
    "Average Hourly Earnings m/m": EventSpec("ahe_mom", "平均时薪环比", "%", ("M", 5), "热"),
    "CPI m/m": EventSpec("cpi_mom", "CPI 环比", "%", ("M", 20), "热"),
    "CPI y/y": EventSpec("cpi_yoy", "CPI 同比", "%", ("M", 20), "热"),
    "Core CPI m/m": EventSpec("core_cpi_mom", "核心 CPI 环比", "%", ("M", 20), "热"),
    "Core CPI y/y": EventSpec("core_cpi_yoy", "核心 CPI 同比", "%", ("M", 20), "热"),
    "Core PCE Price Index m/m": EventSpec("core_pce_mom", "核心 PCE 环比", "%", ("M", 35), "热"),
    "Core PCE Price Index y/y": EventSpec("core_pce_yoy", "核心 PCE 同比", "%", ("M", 35), "热"),
    "PCE Price Index m/m": EventSpec("pce_mom", "PCE 环比", "%", ("M", 35), "热"),
    "PCE Price Index y/y": EventSpec("pce_yoy", "PCE 同比", "%", ("M", 35), "热"),
    "Retail Sales m/m": EventSpec("retail_mom", "零售销售环比", "%", ("M", 20), "强"),
    "Core Retail Sales m/m": EventSpec("retail_exauto_mom", "零售销售除汽车环比", "%", ("M", 20), "强"),
    "Advance GDP q/q": EventSpec("gdp_qoq", "实际 GDP 季环比年化（初值）", "%", ("Q", 85), "强"),
    "Prelim GDP q/q": EventSpec("gdp_qoq", "实际 GDP 季环比年化（修正值）", "%", ("Q", 85), "强"),
    "Final GDP q/q": EventSpec("gdp_qoq", "实际 GDP 季环比年化（终值）", "%", ("Q", 85), "强"),
    "JOLTS Job Openings": EventSpec("jolts", "职位空缺", "千个", ("M", 40), "强"),
    "Unemployment Claims": EventSpec("icsa", "初请失业金", "千人", ("W", 5), "弱"),
    "Durable Goods Orders m/m": EventSpec("dgo_mom", "耐用品订单环比", "%", ("M", 30), "强"),
    "Building Permits": EventSpec("permit", "建筑许可", "千套", ("M", 25), "强"),
    "Housing Starts": EventSpec("houst", "新屋开工", "千套", ("M", 25), "强"),
    "New Home Sales": EventSpec("hsn", "新屋销售", "千套", ("M", 30), "强"),
    "Empire State Manufacturing Index": EventSpec("empire", "纽约联储制造业", "指数", ("M", 0), "强"),
    "Philly Fed Manufacturing Index": EventSpec("philly", "费城联储制造业", "指数", ("M", 0), "强"),
    "Prelim UoM Inflation Expectations": EventSpec("mich", "密歇根一年期通胀预期（初值）", "%", ("M", 0), "热"),
}

# 没有 FRED 实际值、但值得在「即将发布」里看预期的条目
WATCH_ONLY = {
    "ISM Manufacturing PMI": "ISM 制造业 PMI",
    "ISM Services PMI": "ISM 服务业 PMI",
    "Federal Funds Rate": "联邦基金利率决议",
    "Prelim UoM Consumer Sentiment": "密歇根消费者信心（初值）",
    "CB Consumer Confidence": "咨商会消费者信心",
    "ADP Non-Farm Employment Change": "ADP 就业",
    "PPI m/m": "PPI 环比",
    "Core PPI m/m": "核心 PPI 环比",
    "FOMC Meeting Minutes": "FOMC 会议纪要",
    "Fed Chair Powell Speaks": "美联储主席讲话",
}


def parse_value(text: str) -> float | None:
    """把 "150K" / "4.2%" / "1.40M" / "-0.3%" 转成数字；K、M、B 统一换算成「千」。"""
    t = (text or "").strip().replace(",", "")
    m = re.fullmatch(r"([<>]?)(-?\d+(?:\.\d+)?)\s*([KMB%]?)", t, re.I)
    if not m:
        return None
    v = float(m.group(2))
    suf = m.group(3).upper()
    return v * {"K": 1, "M": 1e3, "B": 1e6}.get(suf, 1)


def ref_period(spec: EventSpec, release: date) -> date:
    kind, days = spec.ref
    d = release - timedelta(days=days)
    if kind == "M":
        return date(d.year, d.month, 1)
    if kind == "Q":
        return date(d.year, (d.month - 1) // 3 * 3 + 1, 1)
    return d


def release_date(release_at: str) -> date:
    """日历时间是美东时间，参考期按美东日期算。"""
    return datetime.fromisoformat(release_at).date()


def to_beijing(release_at: str) -> str:
    try:
        return datetime.fromisoformat(release_at).astimezone(BJ).strftime("%m-%d %H:%M")
    except ValueError:
        return ""


# ---------------------------------------------------------------------------


def _get(url: str) -> bytes:
    # 这两个来源不是 FRED，按常见浏览器头请求
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_ff(raw: bytes) -> list[dict]:
    rows = []
    for e in json.loads(raw.decode("utf-8-sig")):
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        title = (e.get("title") or "").strip()
        if not title or not e.get("date"):
            continue
        rows.append({"release_at": e["date"], "title": title, "impact": e.get("impact", ""),
                     "forecast": (e.get("forecast") or "").strip(), "previous": (e.get("previous") or "").strip()})
    return rows


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def merge_events(old: list[dict], new: list[dict], now: datetime) -> list[dict]:
    by_key = {(r["release_at"], r["title"]): dict(r) for r in old}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in new:
        k = (r["release_at"], r["title"])
        cur = by_key.get(k)
        try:
            released = datetime.fromisoformat(r["release_at"]) <= now
        except ValueError:
            released = False
        if cur is None:
            by_key[k] = {**r, "first_seen": stamp, "last_seen": stamp}
        elif not released:
            # 发布前：预期可能被调整，取最新；发布后保持发布前最后看到的值
            cur.update({"forecast": r["forecast"] or cur.get("forecast", ""),
                        "previous": r["previous"] or cur.get("previous", ""),
                        "impact": r["impact"], "last_seen": stamp})
        elif not cur.get("previous") and r["previous"]:
            cur["previous"] = r["previous"]
    return [by_key[k] for k in sorted(by_key)]


def update_events(path: Path, now: datetime | None = None) -> tuple[list[dict], str | None]:
    now = now or datetime.now(timezone.utc)
    old = read_csv(path)
    new, errors = [], []
    for url in FF_URLS:
        try:
            new += parse_ff(_get(url))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url.rsplit('/', 1)[-1]}：{str(exc)[:120]}")
    rows = merge_events(old, new, now) if new else old
    if new:
        write_csv(path, rows, FIELDS)
    # 只要本周的取到了，下周的取不到不算错（周末前常常还没发布）
    err = None if new else "；".join(errors) or "没有取到条目"
    return rows, err


def load_events(path: Path, manual: Path) -> list[dict]:
    rows = {(r["release_at"], r["title"]): r for r in read_csv(path)}
    for r in read_csv(manual):
        if r.get("release_at") and r.get("title"):
            rows[(r["release_at"], r["title"])] = {**rows.get((r["release_at"], r["title"]), {}), **r}
    return [rows[k] for k in sorted(rows)]


# ---------------------------------------------------------------------------
# 克利夫兰联储 Nowcast（同比）

CLEVELAND_NAMES = {
    "CPI Inflation": ("CPI", "nowcast"), "Core CPI Inflation": ("核心 CPI", "nowcast"),
    "PCE Inflation": ("PCE", "nowcast"), "Core PCE Inflation": ("核心 PCE", "nowcast"),
    "Actual CPI Inflation": ("CPI", "actual"), "Actual Core CPI Inflation": ("核心 CPI", "actual"),
    "Actual PCE Inflation": ("PCE", "actual"), "Actual Core PCE Inflation": ("核心 PCE", "actual"),
}


def parse_cleveland(raw: bytes, now: datetime) -> list[dict]:
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, list) or not data:
        raise ValueError("格式和预期不同")
    out = []
    for el in data[-3:]:
        period = ((el.get("chart") or {}).get("subcaption") or "").strip()
        vals: dict[str, dict] = {}
        for ds in el.get("dataset") or []:
            name = CLEVELAND_NAMES.get(ds.get("seriesname"))
            if not name:
                continue
            nums = []
            for x in ds.get("data") or []:
                try:
                    nums.append(float(x.get("value")))
                except (TypeError, ValueError):
                    continue
            if nums:
                vals.setdefault(name[0], {})[name[1]] = nums[-1]
        for measure, v in vals.items():
            out.append({"period": period, "measure": measure, "nowcast": v.get("nowcast", ""),
                        "actual": v.get("actual", ""), "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    if not out:
        raise ValueError("没有识别出 nowcast 序列")
    return out


def update_nowcast(path: Path, now: datetime | None = None) -> tuple[list[dict], str | None]:
    now = now or datetime.now(timezone.utc)
    old = read_csv(path)
    try:
        new = parse_cleveland(_get(CLEVELAND_URL), now)
    except Exception as exc:  # noqa: BLE001
        return old, str(exc)[:200]
    by_key = {(r["period"], r["measure"]): r for r in old}
    for r in new:
        by_key[(r["period"], r["measure"])] = r
    rows = [by_key[k] for k in sorted(by_key)]
    write_csv(path, rows, NOWCAST_FIELDS)
    return rows, None
