#!/usr/bin/env python3
"""更新半导体板块：读旧站笔记数据 → FRED → 台湾月营收 → SEC 季报 → KOSIS（有 key 时）→ 生成 data/semis/dashboard.json。

  python3 scripts/update_semis.py            # 下载并生成
  python3 scripts/update_semis.py --offline  # 不联网，只用 data/raw 里已有的文件重新生成
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
from pipeline.semis import kosis, oldsite, sec, twse  # noqa: E402
from pipeline.semis.build import Sources, build_dashboard  # noqa: E402
from pipeline.semis.indicators import FRED  # noqa: E402

RAW_FRED = ROOT / "data" / "raw" / "fred"
RAW = ROOT / "data" / "raw" / "semis"
OUT = ROOT / "data" / "semis"
STATUS = OUT / "status.json"
LOG = OUT / "state_log.csv"

# 页面「数据状态」里列的来源：名称 → 怎么找最新观测
SOURCE_NAMES = {
    "memory": "存储现货（每日笔记）", "gpu": "GPU 租金（每日笔记）", "openrouter": "OpenRouter 用量（每日笔记）",
    "silicon": "SiliconData token 价格（每日笔记）", "korea": "韩国芯片出口（每日笔记）", "eps": "EPS 修正（盈利笔记）",
    "calendar": "日历（旧站）", "twse": "台湾月营收（证交所）", "sec": "美股季报（SEC）", "kosis": "韩国出货与库存（KOSIS）",
    "manual": "手工录入（合约价、ASML 订单等）",
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
        twse_rows = read_csv(RAW / "twse_revenue.csv")
        sec_rows = read_csv(RAW / "sec_quarterly.csv")
        kosis_rows = read_csv(RAW / "kosis.csv")
    else:
        series_status = fred.fetch_all(ids, RAW_FRED, old.get("series"))
        errors.update(oldsite.update(RAW / "oldsite"))
        twse_rows, errors["twse"] = twse.update(RAW / "twse_revenue.csv", today)
        sec_rows, sec_err = sec.update(RAW / "sec_quarterly.csv")
        errors["sec"] = "；".join(f"{k}：{v}" for k, v in sec_err.items()) or None
        kosis_rows, errors["kosis"] = kosis.update(RAW / "kosis.csv")

    raw = fred.load_all(ids, RAW_FRED)
    olddata = oldsite.load(RAW / "oldsite")
    manual = read_csv(OUT / "manual.csv")
    src = Sources(fred=raw, old=olddata, twse=twse_rows, sec=sec_rows, kosis=kosis_rows, manual=manual)
    dash = build_dashboard(src, today, datetime.now(timezone.utc))

    for sid, (name, unit, freq) in FRED.items():
        st = series_status.setdefault(sid, {})
        st.update({"name": name, "unit": unit, "freq": freq})
        if raw[sid]:
            st["last_obs"] = raw[sid][-1][0].isoformat()

    obs = {name: last_obs(olddata[name], "period" if name == "korea" else "date") for name in oldsite.TABLES}
    obs["calendar"] = olddata["calendar"].get("generated_at", "")[:10] or None
    obs["twse"] = last_obs(twse_rows, "period")
    obs["sec"] = last_obs(sec_rows, "end")
    obs["kosis"] = last_obs(kosis_rows, "period")
    obs["manual"] = last_obs(manual, "date")
    sources = []
    for key, name in SOURCE_NAMES.items():
        err = errors.get(key)
        # KOSIS 没有 key 不算失败；手工表空着也不算
        pending = key == "kosis" and err and err.startswith("未接入")
        sources.append({"key": key, "name": name, "last_obs": obs.get(key), "ok": not err or pending,
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

    # 判断变化日志：六个维度 + 整体景气
    current = [(d["key"], d["name"], d["label"], d["head"]) for d in dash["dimensions"]]
    current.append(("overall", "整体景气", dash["verdict"]["name"], dash["verdict"]["headline"]))
    recent_obs = [f"{SOURCE_NAMES[s['key']]} 更新到 {s['last_obs']}" for s in sources
                  if s["last_obs"] and s["last_obs"][:10] >= (today - timedelta(days=3)).isoformat()[: len(s["last_obs"][:10])]]
    dash["state_log"] = statelog.update(LOG, dash["asof"], current, lambda _k: "；".join(recent_obs), write=not args.offline)

    OUT.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (OUT / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    ok = sum(1 for s in ids if raw[s])
    print(f"FRED {ok}/{len(ids)} 有数据；图 {len(dash['charts'])} 张；整体：{dash['verdict']['headline']}")
    for s in sources:
        if not s["ok"] or s["note"]:
            print(f"  {s['name']}：{s['note'] or '失败'}")
    # 旧站笔记和 FRED 都没数据，说明网络整体不通，让工作流失败以便注意到
    return 0 if (ok or any(olddata[n] for n in oldsite.TABLES)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
