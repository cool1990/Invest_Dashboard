#!/usr/bin/env python3
"""更新加密货币板块：下载各来源 → 生成 data/crypto/dashboard.json。

  python3 scripts/update_crypto.py            # 下载并生成
  python3 scripts/update_crypto.py --offline  # 不联网，只用 data/raw 里已有的文件重新生成
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import statelog  # noqa: E402
from pipeline.crypto import binance, bgeometrics, coinmetrics, dominance, etf, sentiment, stable  # noqa: E402
from pipeline.crypto.build import Sources, build_dashboard  # noqa: E402
from pipeline.crypto.etf import apply_manual  # noqa: E402
from pipeline.csvio import read_csv  # noqa: E402
from pipeline.fred import today  # noqa: E402
from pipeline.series import clean  # noqa: E402

RAW = ROOT / "data" / "raw" / "crypto"
OUT = ROOT / "data" / "crypto"
STATUS = OUT / "status.json"
LOG = OUT / "state_log.csv"

SOURCES = [
    ("coinmetrics", "Coin Metrics（价格、MVRV、发行量）"),
    ("bgeometrics", "BGeometrics（持有者成本、币龄、主导率）"),
    ("binance", "币安 BTCUSDT（资金费率、未平仓、日线）"),
    ("farside", "Farside（现货 ETF 净流入）"),
    ("defillama", "DefiLlama（稳定币）"),
    ("fear_greed", "alternative.me（恐贪指数）"),
]


def _last_date(rows: list[dict], col: str) -> str | None:
    vals = [r[col][:10] for r in rows if r.get(col)]
    return max(vals) if vals else None


def _series_date(s) -> str | None:
    return s[-1][0].isoformat() if s else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    day = today()
    errors: dict[str, str | None] = {}

    if not args.offline:
        print("Coin Metrics…", flush=True)
        _, errors["coinmetrics_btc"] = coinmetrics.update_btc(RAW / "coinmetrics_btc.csv")
        _, errors["coinmetrics_eth"] = coinmetrics.update_eth(RAW / "coinmetrics_eth.csv")
        print("BGeometrics…", flush=True)
        bg_err = bgeometrics.update(RAW)
        errors["bgeometrics"] = "；".join(f"{k}：{v}" for k, v in bg_err.items() if v) or None
        dom_rows = read_csv(RAW / "bg_dominance.csv")
        _, errors["coingecko"] = dominance.merge_today(RAW / "bg_dominance.csv", dom_rows)
        print("币安资金费率与未平仓…", flush=True)
        _, errors["funding"] = binance.update_funding(RAW / "binance_funding.csv", day)
        _, errors["oi"] = binance.update_oi(RAW / "binance_oi.csv", day)
        _, errors["btc_px"] = binance.update_klines(RAW / "binance_btc.csv", "BTCUSDT", datetime(2017, 8, 1).date())
        _, errors["eth_px"] = binance.update_klines(RAW / "binance_eth.csv", "ETHUSDT", datetime(2017, 8, 1).date())
        _, errors["etf_btc"] = etf.update(RAW / "etf_btc.csv", etf.BTC_URL)
        _, errors["etf_eth"] = etf.update(RAW / "etf_eth.csv", etf.ETH_URL)
        _, errors["stable"] = stable.update(RAW / "stable.csv")
        _, errors["fng"] = sentiment.update(RAW / "fng.csv")

    cm = coinmetrics.load_btc(RAW / "coinmetrics_btc.csv")
    manual = read_csv(OUT / "manual.csv")
    etf_btc_rows = apply_manual(read_csv(RAW / "etf_btc.csv"), manual, "etf_btc_usd_mn")
    etf_eth_rows = apply_manual(read_csv(RAW / "etf_eth.csv"), manual, "etf_eth_usd_mn")
    oi_usd, ls = binance.load_oi(RAW / "binance_oi.csv")
    src = Sources(
        price=cm.get("PriceUSD") or [],
        mcap=cm.get("CapMrktCurUSD") or [],
        mvrv=cm.get("CapMVRVCur") or [],
        supply=cm.get("SplyCur") or [],
        issuance=cm.get("IssTotNtv") or [],
        eth_ref=coinmetrics.load_price(RAW / "coinmetrics_eth.csv"),
        btc_px=binance.load_close(RAW / "binance_btc.csv"),
        eth_px=binance.load_close(RAW / "binance_eth.csv"),
        sth=bgeometrics.load_value(RAW / "bg_sth_price.csv"),
        lth=bgeometrics.load_value(RAW / "bg_lth_price.csv"),
        lth_sopr=bgeometrics.load_value(RAW / "bg_lth_sopr.csv"),
        hodl=bgeometrics.load_hodl(RAW / "bg_hodl.csv"),
        funding=binance.load_funding(RAW / "binance_funding.csv"),
        oi_usd=oi_usd,
        ls_ratio=ls,
        etf_btc=_points(etf_btc_rows),
        etf_eth=_points(etf_eth_rows),
        stable=stable.load(RAW / "stable.csv"),
        dominance=bgeometrics.load_value(RAW / "bg_dominance.csv"),
        fng=sentiment.load(RAW / "fng.csv"),
    )

    now = datetime.now(timezone.utc)
    dash = build_dashboard(src, day, now)

    obs = {
        "coinmetrics": _series_date(src.price),
        "bgeometrics": _series_date(src.sth) or _series_date(src.lth_sopr) or _last_date(src.hodl, "date"),
        "binance": _series_date(src.oi_usd) or _funding_date(src.funding),
        "farside": _series_date(src.etf_btc),
        "defillama": _series_date(src.stable),
        "fear_greed": _series_date(src.fng),
    }
    grouped = {
        "coinmetrics": _join(errors.get("coinmetrics_btc"), errors.get("coinmetrics_eth")),
        "bgeometrics": _join(errors.get("bgeometrics"), errors.get("coingecko")),
        "binance": _join(errors.get("funding"), errors.get("oi"), errors.get("btc_px"), errors.get("eth_px")),
        "farside": _join(errors.get("etf_btc"), errors.get("etf_eth")),
        "defillama": errors.get("stable"),
        "fear_greed": errors.get("fng"),
    }
    sources = []
    for key, name in SOURCES:
        err = grouped.get(key)
        sources.append({"key": key, "name": name, "last_obs": obs.get(key), "ok": not err,
                        "note": err or ("" if obs.get(key) else "还没有数据")})

    old = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.exists() else {}
    updated = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    status = {"updated_at": old.get("updated_at", updated) if args.offline else updated, "errors": errors}
    dash["status"] = {"updated_at": status["updated_at"], "failed": [s["key"] for s in sources if not s["ok"]],
                      "sources": sources}

    current = [(d["key"], d["name"], d["label"], d["head"]) for d in dash["dimensions"]]
    current.append(("overall", "比特币周期", dash["verdict"]["name"], dash["verdict"]["headline"]))
    recent = [f"{s['name']} 更新到 {s['last_obs']}" for s in sources
              if s["last_obs"] and s["last_obs"] >= (day - timedelta(days=3)).isoformat()]
    dash["state_log"] = statelog.update(
        LOG, dash["asof"], current, lambda _k: "；".join(recent), write=not args.offline)

    OUT.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (OUT / "dashboard.json").write_text(json.dumps(dash, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"图 {len(dash['charts'])} 张；整体：{dash['verdict']['headline']}")
    for s in sources:
        if not s["ok"] or s["note"]:
            print(f"  {s['name']}：{s['note'] or '失败'}")
    have = any(obs.values())
    return 0 if have else 1


def _points(rows: list[dict]):
    from datetime import date
    pts = []
    for row in rows:
        try:
            pts.append((date.fromisoformat(row["date"][:10]), float(row["total_usd_mn"])))
        except (KeyError, ValueError):
            continue
    return clean(pts)


def _funding_date(rows: list[dict]) -> str | None:
    vals = [r["time"][:10] for r in rows if r.get("time")]
    return max(vals) if vals else None


def _join(*parts: str | None) -> str | None:
    text = "；".join(p for p in parts if p)
    return text or None


if __name__ == "__main__":
    raise SystemExit(main())
