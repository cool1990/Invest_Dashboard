#!/usr/bin/env python3
"""更新宏观板块：下载 FRED → 读市场隐含 EFFR → 生成 data/macro/dashboard.json。

  python3 scripts/update_macro.py            # 下载并生成
  python3 scripts/update_macro.py --offline  # 不联网，只用 data/raw/fred 里已有的文件重新生成
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import fred  # noqa: E402
from pipeline.macro import consensus, effr_expect  # noqa: E402
from pipeline.macro.build import build_dashboard  # noqa: E402
from pipeline.macro.indicators import FRED  # noqa: E402

RAW = ROOT / "data" / "raw" / "fred"
OUT = ROOT / "data" / "macro"
STATUS = OUT / "status.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    old = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}
    ids = list(FRED)
    if args.offline:
        series_status = old.get("series", {})
        expect = effr_expect.read_local(OUT / "effr_expectations.csv")
        expect_err = old.get("effr_expect_error")
        cal_err, nc_err = old.get("calendar_error"), old.get("nowcast_error")
        nowcast = consensus.read_csv(OUT / "nowcast.csv")
    else:
        series_status = fred.fetch_all(ids, RAW, old.get("series"))
        expect, expect_err = effr_expect.update(OUT / "effr_expectations.csv")
        _, cal_err = consensus.update_events(OUT / "consensus.csv")
        nowcast, nc_err = consensus.update_nowcast(OUT / "nowcast.csv")
    events = consensus.load_events(OUT / "consensus.csv", OUT / "consensus_manual.csv")

    raw = fred.load_all(ids, RAW)
    dash = build_dashboard(raw, expect, fred.today(), events, nowcast, datetime.now(timezone.utc))
    for sid, (name, unit, freq) in FRED.items():
        st = series_status.setdefault(sid, {})
        st.update({"name": name, "unit": unit, "freq": freq})
        if raw[sid]:
            st["last_obs"] = raw[sid][-1][0].isoformat()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = {"updated_at": now if not args.offline else old.get("updated_at", now),
              "series": dict(sorted(series_status.items())),
              "effr_expect_error": expect_err, "calendar_error": cal_err, "nowcast_error": nc_err}
    dash["status"] = {
        "updated_at": status["updated_at"],
        "failed": sorted(k for k, v in series_status.items() if not v.get("ok", True) or not raw.get(k)),
        "series": {k: {"name": v.get("name"), "last_obs": v.get("last_obs"), "ok": bool(raw.get(k)) and v.get("ok", True),
                       "freq": v.get("freq")} for k, v in status["series"].items()},
        "effr_expect_error": expect_err,
        "calendar_error": cal_err,
        "nowcast_error": nc_err,
        "calendar_events": len(events),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (OUT / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False, separators=(",", ":")) + "\n",
                                        encoding="utf-8")
    ok = sum(1 for s in ids if raw[s])
    print(f"序列 {ok}/{len(ids)} 有数据；图 {len(dash['charts'])} 张；失败：{', '.join(dash['status']['failed']) or '无'}")
    for label, err in (("市场隐含 EFFR", expect_err), ("经济日历预期", cal_err), ("克利夫兰联储 Nowcast", nc_err)):
        if err:
            print(f"{label}读取失败：{err}")
    print(f"日历预期累计 {len(events)} 条；Nowcast {len(nowcast)} 条")
    # 一条数据都没有说明网络整体不通，让工作流失败以便注意到
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
