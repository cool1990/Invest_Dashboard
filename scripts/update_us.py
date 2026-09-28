#!/usr/bin/env python3
"""更新美股板块：旧站盈利与情绪 → FRED → 融资余额 → 前十大权重 → 生成 data/us/dashboard.json。

  python3 scripts/update_us.py            # 下载并生成
  python3 scripts/update_us.py --offline  # 不联网，只用 data/raw 里已有的文件重新生成
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import fred, statelog  # noqa: E402
from pipeline.csvio import read_csv  # noqa: E402
from pipeline.us import finra, holdings, oldsite  # noqa: E402
from pipeline.us.build import Sources, build_dashboard  # noqa: E402
from pipeline.us.indicators import FRED, FRED_READ  # noqa: E402

RAW_FRED = ROOT / "data" / "raw" / "fred"
RAW = ROOT / "data" / "raw" / "us"
OUT = ROOT / "data" / "us"
STATUS = OUT / "status.json"
LOG = OUT / "state_log.csv"

SOURCE_NAMES = {
    "earnings": "盈利跟踪（每日笔记）",
    "sentiment": "情绪序列（每日笔记）",
    "calendar": "日历（旧站）",
    "margin": "融资余额（FINRA）",
    "concentration": "前十大权重（ETF 持仓）",
    "manual": "手工录入（标普远期市盈率）",
}


def last_obs(rows: list[dict], col: str) -> str | None:
    vals = [r[col] for r in rows if r.get(col)]
    return max(vals) if vals else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    old = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}
    today = fred.today()
    ids = list(FRED)
    errors: dict[str, str | None] = dict(old.get("errors", {}))
    if args.offline:
        series_status = old.get("series", {})
    else:
        series_status = fred.fetch_all(ids, RAW_FRED, old.get("series"))
        errors.update(oldsite.update(RAW))
        errors["margin"] = finra.update(RAW / "margin.csv")
        errors["concentration"] = holdings.update(RAW / "concentration.csv")

    raw = fred.load_all(list(ids) + list(FRED_READ), RAW_FRED)
    olddata = oldsite.load(RAW)
    manual = read_csv(OUT / "manual.csv")
    src = Sources(fred=raw, earnings=olddata["earnings"], sentiment=olddata["sentiment"],
                  calendar=olddata["calendar"], margin=finra.load(RAW / "margin.csv"),
                  concentration=holdings.load(RAW / "concentration.csv"), manual=manual)
    dash = build_dashboard(src, today, datetime.now(timezone.utc))

    for sid, (name, unit, freq) in FRED.items():
        st = series_status.setdefault(sid, {})
        st.update({"name": name, "unit": unit, "freq": freq})
        if raw[sid]:
            st["last_obs"] = raw[sid][-1][0].isoformat()

    obs = {
        "earnings": last_obs(olddata["earnings"], "date"),
        "sentiment": last_obs(olddata["sentiment"], "date"),
        "calendar": (olddata["calendar"].get("generated_at") or "")[:10] or None,
        "margin": last_obs(src.margin, "date"),
        "concentration": last_obs(src.concentration, "date"),
        "manual": last_obs(manual, "date"),
    }
    sources = []
    for key, name in SOURCE_NAMES.items():
        err = errors.get(key)
        sources.append({"key": key, "name": name, "last_obs": obs.get(key), "ok": not err,
                        "note": err or ("" if obs.get(key) else "还没有数据")})

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = {"updated_at": now if not args.offline else old.get("updated_at", now),
              "series": dict(sorted(series_status.items())), "errors": errors}
    failed = sorted(k for k, v in series_status.items() if k in FRED and (not v.get("ok", True) or not raw.get(k)))
    dash["status"] = {
        "updated_at": status["updated_at"], "failed": failed,
        "series": {k: {"name": v.get("name"), "last_obs": v.get("last_obs"), "ok": bool(raw.get(k)) and v.get("ok", True),
                       "freq": v.get("freq")} for k, v in status["series"].items() if k in FRED},
        "sources": sources,
        "calendar_error": errors.get("calendar"),
    }

    current = [(d["key"], d["name"], d["label"], d["head"]) for d in dash["dimensions"]]
    current.append(("overall", "整体判断", dash["verdict"]["name"], dash["verdict"]["headline"]))
    recent_obs = [f"{SOURCE_NAMES[s['key']]} 更新到 {s['last_obs']}" for s in sources
                  if s["last_obs"] and s["last_obs"][:10] >= (today - timedelta(days=3)).isoformat()]
    dash["state_log"] = statelog.update(LOG, dash["asof"], current, lambda _k: "；".join(recent_obs), write=not args.offline)

    OUT.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (OUT / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    ok = sum(1 for s in ids if raw[s])
    print(f"FRED {ok}/{len(ids)} 有数据；图 {len(dash['charts'])} 张；整体：{dash['verdict']['headline']}")
    for s in sources:
        if not s["ok"] or s["note"]:
            print(f"  {s['name']}：{s['note'] or '失败'}")
    return 0 if (ok or olddata["earnings"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
