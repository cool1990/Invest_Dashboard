"""把盈利笔记、情绪序列、FRED、融资余额和持仓权重组装成 data/us/dashboard.json。"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .. import series as ts
from ..page import Chart, Line, Metric, metric_json
from ..series import Series
from . import importance
from . import interpret as I
from .indicators import CORE, MANUAL_PE

MIN_N = 4  # 中位数至少要有这么多家，避免只剩一两家时被单一个股带着走
UPCOMING_DAYS = 14
CORE_SET = set(CORE)


def _fl(x) -> float | None:
    try:
        return float(str(x).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _ok_rev(r: dict) -> bool:
    if r.get("status") == "NEGATIVE_EPS":
        return False
    if "样本不足" in (r.get("revision_signal") or ""):
        return False
    if "LOW_SAMPLE" in (r.get("flags") or ""):
        return False
    return _fl(r.get("revision_30d")) is not None


def _ok_pe(r: dict) -> bool:
    if r.get("status") == "NEGATIVE_EPS":
        return False
    pe = _fl(r.get("forward_pe"))
    return pe is not None and pe > 0


def _q(s: Series) -> Series:
    """季度序列统一记到季初，方便和差 4 期的同比对齐。"""
    return ts.clean((ts.quarter_start(d), v) for d, v in s)


@dataclass
class Sources:
    fred: dict[str, Series] = field(default_factory=dict)
    earnings: list[dict] = field(default_factory=list)
    sentiment: list[dict] = field(default_factory=list)
    calendar: dict = field(default_factory=dict)
    margin: list[dict] = field(default_factory=list)
    concentration: list[dict] = field(default_factory=list)
    manual: list[dict] = field(default_factory=list)


class UsBuilder:
    def __init__(self, src: Sources, asof: date | None = None, now: datetime | None = None):
        self.src = src
        self.asof = asof or date.today()
        self.now = now or datetime.combine(self.asof, datetime.min.time(), tzinfo=timezone.utc)
        self.charts: dict[str, dict] = {}

    def fred(self, sid: str) -> Series:
        return self.src.fred.get(sid) or []

    def add(self, *charts: Chart) -> list[str]:
        ids = []
        for c in charts:
            js = c.to_json()
            if js:
                self.charts[c.id] = js
                ids.append(c.id)
        return ids

    def manual(self, key: str) -> Series:
        return ts.clean((_d(r["date"]), _fl(r["value"])) for r in self.src.manual
                        if r.get("key") == key and _fl(r.get("value")) is not None)

    def sent(self, sid: str) -> Series:
        pts = []
        for r in self.src.sentiment:
            if r.get("series_id") != sid:
                continue
            v = _fl(r.get("value"))
            if v is None:
                continue
            when = (r.get("obs_date") or r.get("date") or "")[:10]
            if not when:
                continue
            pts.append((_d(when), v, r.get("date") or ""))
        pts.sort(key=lambda x: (x[0], x[2]))
        return ts.clean((d, v) for d, v, _ in pts)

    def by_date(self) -> list[tuple[str, list[dict]]]:
        buckets: dict[str, list[dict]] = {}
        for r in self.src.earnings:
            if r.get("date") and r.get("ticker"):
                buckets.setdefault(r["date"], []).append(r)
        return sorted(buckets.items())

    # -- 盈利 -----------------------------------------------------------------
    def earnings(self):
        days = []
        watch: Series = []
        pe_days = []
        for d, rows in self.by_date():
            core = [r for r in rows if r["ticker"] in CORE_SET and _ok_rev(r)]
            if len(core) >= MIN_N:
                days.append({"date": _d(d), "rev": statistics.median(_fl(r["revision_30d"]) for r in core),
                             "n": len(core),
                             "n_up": sum(r["revision_signal"] == "强上修" for r in core),
                             "n_down": sum(r["revision_signal"] == "强下修" for r in core)})
            us = [r for r in rows if _ok_rev(r)]
            if len(us) >= MIN_N:
                watch.append((_d(d), statistics.median(_fl(r["revision_30d"]) for r in us)))
            core_pe = [r for r in rows if r["ticker"] in CORE_SET and _ok_pe(r)]
            if len(core_pe) >= MIN_N:
                pe_days.append((_d(d), statistics.median(_fl(r["forward_pe"]) for r in core_pe)))
        self.pe_basket: Series = [(d, v) for d, v in pe_days]
        rev: Series = [(x["date"], x["rev"]) for x in days]
        n_up: Series = [(x["date"], x["n_up"]) for x in days]
        last = days[-1] if days else {}
        st = I.earnings_state({"rev": last.get("rev"), "n": last.get("n"),
                               "n_up": last.get("n_up"), "n_down": last.get("n_down")})
        profits = ts.pct_change(_q(self.fred("CPATAX")), 4, "Q")
        charts = self.add(
            Chart("rev", "核心篮子：下财年 EPS 30 日修正中位数", "%",
                  [Line("核心篮子", rev), Line("观察名单", watch, dash=True)], core=True,
                  note="8 家大盘股的中位数。样本不足或 EPS 为负的不计入。观察名单含中概和加密相关公司，只作参考。历史从笔记开始的 2026 年 9 月起。"),
            Chart("n_up", "核心篮子：强上修与强下修家数", "家",
                  [Line("强上修", n_up, "bar"), Line("强下修", [(x["date"], x["n_down"]) for x in days], "bar")],
                  note=f"至少 {I.SPREAD_N} 家且多于另一边，才在理由里写成扩散。"),
            Chart("profits", "税后企业利润同比", "%", [Line("同比", profits)],
                  start=date(1990, 1, 1), note="FRED 税后企业利润（CPATAX），全经济口径，不是标普每股盈利。只作利润周期的参考，不改盈利标签。"),
        )
        metrics = [
            Metric("rev", "核心篮子 EPS 修正中位数", rev, "%", "{:+.2f}", "D", "rev"),
            Metric("n_up", "强上修家数", n_up, "家", "{:.0f}", "D", "n_up",
                   note="" if not last else f"强下修 {last['n_down']} 家，有数的 {last['n']} 家"),
            Metric("watch_rev", "观察名单修正中位数", watch, "%", "{:+.2f}", "D", "rev", ref=True),
            Metric("profits_yoy", "税后企业利润同比", profits, "%", "{:+.1f}", "Q", "profits", ref=True),
        ]
        groups = [("修正", [c for c in charts if c in ("rev", "n_up")]),
                  ("更多", [c for c in charts if c == "profits"])]
        self._names()
        return groups, metrics, st

    def _names(self) -> None:
        days = self.by_date()
        if not days:
            self.names, self.names_date = [], ""
            return
        d, rows = days[-1]
        order = {t: i for i, t in enumerate(CORE)}

        def cell_pe(r):
            pe = _fl(r.get("forward_pe"))
            return f"{pe:.2f}" if pe and pe > 0 else "—"

        def cell_rev(r):
            x = _fl(r.get("revision_30d"))
            return f"{x:+.2f}%" if x is not None else "—"

        def cell_rsi(r):
            x = _fl(r.get("rsi"))
            return f"{x:.0f}" if x is not None else "—"

        rows = sorted(rows, key=lambda r: (0 if r["ticker"] in CORE_SET else 1, order.get(r["ticker"], 99), r["ticker"]))
        self.names_date = d
        self.names = [{"ticker": r["ticker"], "core": r["ticker"] in CORE_SET, "forward_pe": cell_pe(r),
                       "revision": cell_rev(r), "signal": r.get("revision_signal") or "—", "rsi": cell_rsi(r)}
                      for r in rows]

    # -- 估值 -----------------------------------------------------------------
    def valuation(self):
        basket: Series = getattr(self, "pe_basket", [])
        manual = self.manual(MANUAL_PE)
        use, manual_on = (manual, True) if manual else (basket, False)
        real = self.fred("DFII10")
        erp: Series = []
        real_at: Series = []
        for d, v in use:
            ry = ts.asof(real, d, max_gap_days=10)
            if ry is not None and v:
                erp.append((d, 100.0 / v - ry))
                real_at.append((d, ry))
        ey = [(d, 100.0 / v) for d, v in use]
        vstate = {"pe": use[-1][1] if use else None, "manual": manual_on,
                  "erp": erp[-1][1] if erp else None, "real": real_at[-1][1] if real_at else None}
        st = I.valuation_state(vstate)
        real_w = ts.to_weekly(real)
        buffett = ts.combine(lambda m, g: m / 1000.0 / g * 100, _q(self.fred("NCBEILQ027S")), _q(self.fred("GDP")))
        lines = [Line("核心篮子中位数", basket)]
        if manual:
            lines.append(Line("标普（手工）", manual, dash=True))
        charts = self.add(
            Chart("pe", "远期市盈率", "倍", lines, core=True,
                  note="核心篮子 8 家的中位数，来自盈利跟踪笔记。若 data/us/manual.csv 写了 spx_fwd_pe，判断改用那个数，篮子降为参考。"),
            Chart("erp", "股权风险溢价", "百分点", [Line("盈利收益率 − 实际利率", erp)], core=True,
                  note="盈利收益率 = 100 / 远期市盈率。低于 2 为贵，高于 4 为便宜。实际利率用 FRED 10 年期 TIPS（DFII10）。"),
            Chart("ey", "盈利收益率与 10 年实际利率", "%",
                  [Line("盈利收益率", ey), Line("10 年实际利率", ts.to_weekly(real) if len(real) > 30 else real, dash=True)],
                  note="两条线的差就是上面的风险溢价。"),
            Chart("spx", "标普 500", "指数", [Line("收盘", ts.to_weekly(self.fred("SP500")))],
                  start=date(2016, 1, 1), note="FRED SP500，日频压成周频。不进判断。"),
            Chart("buffett", "非金融企业股权市值 / GDP", "%", [Line("市值 / GDP", buffett)],
                  start=date(1990, 1, 1), note="FRED 股权负债（NCBEILQ027S）除以名义 GDP。这是市值相对经济体量，不是远期市盈率，不进判断。"),
            Chart("real", "10 年期实际利率", "%", [Line("TIPS", real_w)], start=date(2003, 1, 1)),
        )
        metrics = [
            Metric("pe", "远期市盈率" + ("（手工）" if manual_on else ""), use, "倍", "{:.1f}",
                   "O" if manual_on else "D", "pe"),
            Metric("erp", "股权风险溢价", erp, "百分点", "{:.2f}", "O" if manual_on else "D", "erp"),
            Metric("real", "10 年期实际利率", real_at, "%", "{:.2f}", "D", "real"),
        ]
        if manual_on:
            metrics.append(Metric("basket_pe", "核心篮子远期市盈率中位数", basket, "倍", "{:.1f}", "D", "pe", ref=True))
        groups = [("估值", [c for c in charts if c in ("pe", "erp", "ey")]),
                  ("更多", [c for c in charts if c in ("spx", "buffett", "real")])]
        return groups, metrics, st

    # -- 情绪 -----------------------------------------------------------------
    def sentiment(self):
        fred_vix = self.fred("VIXCLS")
        note_vix = self.sent("vix")
        if fred_vix and note_vix:
            vix = fred_vix + [p for p in note_vix if p[0] > fred_vix[-1][0]]
        else:
            vix = fred_vix or note_vix
        cnn, aaii = self.sent("cnn_fg"), self.sent("aaii")
        breadth = self.sent("spx_breadth_200")
        margin = ts.clean((_d(r["date"]), _fl(r["debit"])) for r in self.src.margin if _fl(r.get("debit")) is not None)
        margin_yoy = ts.pct_change(margin, 12, "M")
        spx_yoy = ts.pct_change(ts.to_monthly(self.fred("SP500"), "last"), 12, "M")
        gap = ts.combine(lambda m, s: m - s, margin_yoy, spx_yoy)
        conc = ts.clean((_d(r["date"]), _fl(r["top10"])) for r in self.src.concentration if _fl(r.get("top10")) is not None)
        latest_conc = self.src.concentration[-1] if self.src.concentration else {}
        st = I.sentiment_state({
            "vix": vix[-1][1] if vix else None,
            "cnn": cnn[-1][1] if cnn else None,
            "aaii": aaii[-1][1] if aaii else None,
            "breadth": breadth[-1][1] if breadth else None,
            "margin_yoy": margin_yoy[-1][1] if margin_yoy else None,
            "margin_gap": gap[-1][1] if gap and margin_yoy and gap[-1][0] == margin_yoy[-1][0] else None,
            "top10": conc[-1][1] if conc else None,
        })
        who = {"IVV": "iShares IVV", "SPY": "SPDR SPY"}.get(latest_conc.get("source", ""), "ETF")
        names = (latest_conc.get("names") or "").replace(",", "、")
        charts = self.add(
            Chart("vix", "VIX", "指数", [Line("VIX", ts.to_weekly(vix))], core=True, start=date(1990, 1, 1),
                  note="FRED VIXCLS，日频压成周频。低于 15 平静，高于 25 紧张。笔记里更新的日子会接到末尾。"),
            Chart("breadth", "标普 500：站上 200 日均线的比例", "%",
                  [Line("200 日", breadth), Line("50 日", self.sent("spx_breadth_50"), dash=True),
                   Line("20 日", self.sent("spx_breadth_20"), dash=True)], core=True,
                  note="来自每日笔记，历史很短。低于 40% 为面窄。20 日和 50 日只作参考。"),
            Chart("margin", "客户融资余额同比", "%",
                  [Line("融资余额", margin_yoy), Line("标普 500", spx_yoy, dash=True)], core=True,
                  start=date(1997, 1, 1),
                  note="FINRA 保证金账户借方余额的同比，对照标普指数同比。融资高过指数 10 个百分点以上，视为比指数涨得更快。"),
            Chart("top10", "标普 500 前十大权重", "%", [Line("前十大合计", conc)], core=True,
                  note=f"来自 {who} 日持仓，前 10 只权相加。达到 30% 为集中。历史从第一次成功下载开始。"
                       + (f" 当前：{names}。" if names else "")),
            Chart("margin_level", "客户融资余额", "十亿美元", [Line("借方余额", ts.scale(margin, 0.001), "bar")],
                  start=date(1997, 1, 1), note="FINRA，月频，原表单位是百万美元。"),
            Chart("cnn", "CNN 恐贪与 AAII 牛熊差", "点",
                  [Line("CNN 恐贪", cnn), Line("AAII 牛熊差", aaii, dash=True)],
                  note="CNN 是 0–100。AAII 是牛市比例减熊市比例（百分点），和 CNN 不是同一个刻度，放在一起只为对照方向。"),
            Chart("rsi", "指数 RSI(14)", "点",
                  [Line("标普 500", self.sent("spx_rsi")), Line("纳斯达克", self.sent("nasdaq_rsi"), dash=True)],
                  note="每日笔记里的 Wilder RSI。不进判断。"),
            Chart("ndx_breadth", "纳斯达克 100 参与度", "%",
                  [Line("200 日", self.sent("ndx_breadth_200")), Line("50 日", self.sent("ndx_breadth_50"), dash=True)],
                  note="参考，不进判断。"),
        )
        metrics = [
            Metric("vix", "VIX", vix, "", "{:.1f}", "D", "vix"),
            Metric("cnn", "CNN 恐贪", cnn, "", "{:.0f}", "D", "cnn"),
            Metric("aaii", "AAII 牛熊差", aaii, "百分点", "{:+.1f}", "O", "cnn"),
            Metric("breadth", "标普站上 200 日均线", breadth, "%", "{:.0f}", "D", "breadth"),
            Metric("margin_yoy", "融资余额同比", margin_yoy, "%", "{:+.1f}", "M", "margin"),
            Metric("margin_gap", "融资同比 − 标普同比", gap, "百分点", "{:+.1f}", "M", "margin"),
            Metric("top10", "前十大权重", conc, "%", "{:.1f}", "D", "top10",
                   note=(who + ("：" + names if names else ""))),
        ]
        groups = [("情绪", [c for c in charts if c in ("vix", "breadth", "margin", "top10")]),
                  ("更多", [c for c in charts if c in ("margin_level", "cnn", "rsi", "ndx_breadth")])]
        return groups, metrics, st

    # -- 日历 -----------------------------------------------------------------
    def releases(self) -> dict:
        watch = {r["ticker"] for r in self.src.earnings}
        today_bj = (self.now + timedelta(hours=8)).date()
        end = today_bj + timedelta(days=UPCOMING_DAYS)
        up = []
        for e in (self.src.calendar or {}).get("events", []):
            imp = importance.rate(e.get("title", ""), watch)
            if not imp:
                continue
            try:
                d = _d(e["date"])
            except (KeyError, ValueError):
                continue
            if not (today_bj <= d <= end):
                continue
            cons = e.get("consensus") or ""
            up.append({"bj_date": d.isoformat(), "bj": f"{d.isoformat()} {e.get('time_bj') or ''}".strip(),
                       "title": e["title"], "ref": "", "forecast_text": f"预期 {cons}" if cons else "",
                       "previous_text": e.get("previous") or "", "importance": imp})
        up.sort(key=lambda x: (x["bj_date"], x["bj"]))
        return {"upcoming": up, "recent": [], "importance_rule": importance.RULE}

    def build(self) -> dict:
        dims_in = [("earnings", "盈利", self.earnings()), ("valuation", "估值", self.valuation()),
                   ("sentiment", "情绪", self.sentiment())]
        st = {k: s for k, _, (_, _, s) in dims_in}
        env = I.environment(st["earnings"], st["valuation"], st["sentiment"])
        out_dims, sections = [], []
        for key, name, (groups, metrics, state) in dims_in:
            out_dims.append({"key": key, "name": name, "label": state["label"], "why": state.get("why", []),
                             "head": state["head"],
                             "metrics": [x for x in (metric_json(m, state.get("anchors", {}), self.charts) for m in metrics) if x]})
            sections.append({"key": key, "name": name, "groups": [{"name": g, "charts": ids} for g, ids in groups if ids]})
        core = "、".join(CORE)
        return {
            "asof": self.asof.isoformat(),
            "method": "盈利和估值只看盈利跟踪笔记里的核心篮子（" + core + "）的中位数：30 日 EPS 修正高于 +2% 为上修、低于 −2% 为下修；"
                      "远期市盈率换成盈利收益率后减去 10 年实际利率，低于 2 个百分点为贵、高于 4 个百分点为便宜。"
                      "情绪四块分开写：VIX（低于 15 平静、高于 25 紧张）、标普站上 200 日均线的比例（低于 40% 为面窄）、"
                      "FINRA 融资余额同比（高于 +20% 为扩张）、标普 500 前十大权重（达到 30% 为集中）。"
                      "环境名只由盈利和估值决定，情绪不参与起名。阈值都写在 pipeline/us/interpret.py。",
            "verdict": {"name": env["name"], "headline": env["head"], "lines": env["lines"], "label": "整体判断"},
            "dimensions": out_dims,
            "names": getattr(self, "names", []),
            "names_date": getattr(self, "names_date", ""),
            "releases": self.releases(),
            "sections": sections,
            "charts": self.charts,
        }


def build_dashboard(src: Sources, asof: date | None = None, now: datetime | None = None) -> dict:
    return UsBuilder(src, asof, now).build()
