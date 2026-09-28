"""把各来源整理成 data/semis/dashboard.json。结构和宏观页相同，另加 leading（领先指标一览）。

- 结论：verdict（两条线各自的景气象限 + 领先 vs 同步）、leading、dimensions 的 label / why。
- 依据：dimensions 的 metrics 和 sections + charts。
- 时间：releases.upcoming（旧站日历里的半导体条目与云厂商财报，附星级）。

缺数据的来源会被跳过；某张图一条序列都没有，就不输出这张图。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .. import series as ts
from ..page import Chart, Line, Metric, metric_json
from ..series import Series
from . import importance
from . import interpret as I
from .indicators import (DRAM, EPS_AI, EPS_TRAD, GPUS, MANUAL, NAND, SEC, SSD, TW_AI_CONFIRM, TW_TRAD_DEMAND,
                         TWSE)

UPCOMING_DAYS = 14
EPS_NAMES = {"NVDA": "英伟达", "TSM": "台积电", "AVGO": "博通", "QCOM": "高通", "INTC": "英特尔", "MU": "美光"}


def _fl(x) -> float | None:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _month(p: str) -> date:
    return date(int(p[:4]), int(p[5:7]), 1)


def cal_quarter(end: date) -> date:
    """财报期末 → 日历季度（按期中所在季度，甲骨文、英伟达这类非自然季度也能对齐）。"""
    return ts.quarter_start(end - timedelta(days=45))


def qtext(d: date) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def chg_days(s: Series, days: int) -> float | None:
    """最新值比 days 天前（当天或之前最近的一个）变化 %。历史不够长返回 None。"""
    if not s or s[0][0] > s[-1][0] - timedelta(days=days):
        return None
    base = ts.asof(s, s[-1][0] - timedelta(days=days))
    return None if not base else (s[-1][1] / base - 1) * 100


@dataclass
class Sources:
    fred: dict[str, Series] = field(default_factory=dict)
    old: dict = field(default_factory=dict)  # oldsite.load()
    twse: list[dict] = field(default_factory=list)
    sec: list[dict] = field(default_factory=list)
    kosis: list[dict] = field(default_factory=list)
    manual: list[dict] = field(default_factory=list)


class SemisBuilder:
    def __init__(self, src: Sources, asof: date | None = None, now: datetime | None = None):
        self.src = src
        self.asof = asof or date.today()
        self.now = now or datetime.combine(self.asof, datetime.min.time(), tzinfo=timezone.utc)
        self.charts: dict[str, dict] = {}
        self.leading: list[dict] = []

    # -- 取数 ---------------------------------------------------------------
    def fred(self, sid: str) -> Series:
        return self.src.fred.get(sid) or []

    def old(self, name: str) -> list[dict]:
        return self.src.old.get(name) or []

    def old_series(self, name: str, key: str, match: str, col: str) -> Series:
        return ts.clean((_d(r["date"]), _fl(r[col])) for r in self.old(name) if r.get(key) == match and _fl(r.get(col)) is not None)

    def latest_row(self, name: str, key: str, match: str) -> dict | None:
        rows = [r for r in self.old(name) if r.get(key) == match]
        return max(rows, key=lambda r: r["date"]) if rows else None

    def add(self, *charts: Chart) -> list[str]:
        ids = []
        for c in charts:
            js = c.to_json()
            if js:
                self.charts[c.id] = js
                ids.append(c.id)
        return ids

    def lead(self, tier: str, line: str, name: str, value: str, when: str, dir_: str | None, meaning: str,
             score: bool = True) -> None:
        """领先指标一览的一行。score=False 的只显示，不进「领先指标走强/转弱」的合计。"""
        if dir_ is None:
            return
        self.leading.append({"tier": tier, "line": line, "name": name, "value": value, "date": when,
                             "dir": dir_, "meaning": meaning, "score": score})

    # -- 台湾月营收 ---------------------------------------------------------
    def tw_rev(self, code: str) -> Series:
        return ts.clean((_month(r["period"]), _fl(r["revenue"])) for r in self.src.twse
                        if r["code"] == code and _fl(r["revenue"]) is not None)

    def tw_rev_ly(self, code: str) -> dict[date, float]:
        return {_month(r["period"]): _fl(r["revenue_ly"]) for r in self.src.twse
                if r["code"] == code and _fl(r.get("revenue_ly")) is not None}

    def tw_yoy3(self, codes: tuple[str, ...]) -> Series:
        """几家合计的近 3 个月营收同比。去年同月优先用本地历史，没有就用公告里的「去年当月营收」。"""
        cur = [dict(self.tw_rev(c)) for c in codes]
        ly = [self.tw_rev_ly(c) for c in codes]
        months = sorted(set.intersection(*(set(m) for m in cur))) if cur else []
        out = []
        for d in months:
            win = [ts._shift_month(d, -k) for k in range(3)]
            num = den = 0.0
            ok = True
            for c, l in zip(cur, ly):
                for w in win:
                    base = c.get(ts._shift_month(w, -12), l.get(w))
                    if w not in c or base is None:
                        ok = False
                        break
                    num += c[w]
                    den += base
                if not ok:
                    break
            if ok and den:
                out.append((d, (num / den - 1) * 100))
        return out

    def tw_yoy1(self, code: str) -> Series:
        cur, ly = dict(self.tw_rev(code)), self.tw_rev_ly(code)
        out = []
        for d, v in sorted(cur.items()):
            base = cur.get(ts._shift_month(d, -12), ly.get(d))
            if base:
                out.append((d, (v / base - 1) * 100))
        return out

    # -- SEC 季报 -----------------------------------------------------------
    def sec_q(self, ticker: str, item: str) -> Series:
        """按日历季度归档的单季值（存货是期末值）。"""
        return ts.clean((cal_quarter(_d(r["end"])), _fl(r["value"])) for r in self.src.sec
                        if r["ticker"] == ticker and r["item"] == item)

    def sec_sum(self, tickers: list[str], item: str) -> Series:
        """几家合计；某季缺任何一家就不出值（避免少算一家看起来像下滑）。"""
        maps = [dict(self.sec_q(t, item)) for t in tickers]
        if not maps or any(not m for m in maps):
            return []
        common = set.intersection(*(set(m) for m in maps))
        return sorted((d, sum(m[d] for m in maps)) for d in common)

    def group(self, g: str) -> list[str]:
        return [t for t, (_, _, grp) in SEC.items() if grp == g]

    def dio(self, ticker: str) -> Series:
        inv, cogs = dict(self.sec_q(ticker, "inventory")), dict(self.sec_q(ticker, "cogs"))
        return sorted((d, inv[d] / cogs[d] * 91) for d in inv if cogs.get(d))

    # -- 手工 --------------------------------------------------------------
    def manual(self, key: str) -> Series:
        return ts.clean((_d(r["date"]), _fl(r["value"])) for r in self.src.manual
                        if r.get("key") == key and _fl(r.get("value")) is not None)

    # =====================================================================
    # AI 需求
    def ai_demand(self):
        v: dict = {}
        cloud = self.group("cloud")
        capex = self.sec_sum(cloud, "capex")
        capex_yoy = ts.pct_change(capex, 4, "Q")
        if capex_yoy:
            v["capex_yoy"], v["capex_q"] = capex_yoy[-1][1], qtext(capex_yoy[-1][0])
            v["capex_sum"] = dict(capex)[capex_yoy[-1][0]] / 1e8
            if len(capex_yoy) > 1 and capex_yoy[-2][0] == ts._shift_month(capex_yoy[-1][0], -3):
                v["capex_yoy_prev"] = capex_yoy[-2][1]
        tok_rows = sorted((r for r in self.old("openrouter") if r["window"] == "30日"), key=lambda r: r["date"])
        tok_chg = ts.clean((_d(r["date"]), _fl(r["change_pct"])) for r in tok_rows)
        tok_lvl = ts.clean((_d(r["date"]), _fl(r["tokens"])) for r in tok_rows)
        if tok_chg:
            v["tokens_30d"], v["tokens_30d_level"] = tok_chg[-1][1], tok_lvl[-1][1] if tok_lvl else None
        eps_ai, names = self.eps_avg(EPS_AI)
        if eps_ai:
            v["eps_ai"], v["eps_ai_names"] = eps_ai[-1][1], names

        st = I.ai_demand_state(v)
        charts = self.add(
            Chart("capex", "云厂商资本开支（单季）", "亿美元",
                  [Line(SEC[t][1], ts.scale(self.sec_q(t, "capex"), 1e-8), "bar") for t in cloud],
                  stacked="bar", core=True, start=date(2018, 1, 1),
                  note="微软、Alphabet、亚马逊、Meta、甲骨文。按财报期中所在的日历季度归档；现金流量表的累计数已差分成单季。"),
            Chart("capex_yoy", "云厂商资本开支合计：同比", "%", [Line("同比", capex_yoy)], core=True,
                  start=date(2018, 1, 1), note=f"锚点：> {I.CAPEX_UP:g}% 扩张，< {I.CAPEX_DOWN:g}% 收缩。某季缺任何一家就不算合计。"),
            Chart("tokens", "OpenRouter token 用量：最近 30 天", "万亿 token", [Line("30 天合计", tok_lvl)],
                  note="来自每日笔记；历史从 2026-09 开始。"),
            Chart("tokens_chg", "OpenRouter 最近 30 天较前 30 天", "%", [Line("变化", tok_chg)]),
            Chart("eps_ai", "AI 芯片：下财年 EPS 30 天修正", "%",
                  [Line(EPS_NAMES.get(t, t), self.eps_series(t)) for t in EPS_AI], core=True,
                  note="来自每日盈利笔记。"),
            Chart("silicon", "SiliconData LLM token 价格指数", "美元 / 百万 token",
                  [Line("指数", self.old_series("silicon", "unit", "USD/M tokens", "value"))],
                  note="token 越便宜、用量越大，算力需求弹性越高；参考，不进判断。"),
            Chart("ai_rev", "AI 芯片公司营收：同比", "%",
                  [Line(SEC[t][1], ts.pct_change(self.sec_q(t, "revenue"), 4, "Q")) for t in self.group("ai")],
                  start=date(2018, 1, 1), note="英伟达、AMD、博通单季营收同比；同步指标，参考。"),
        )
        metrics = [
            Metric("capex_yoy", "云厂商资本开支同比", capex_yoy, "%", "{:+.1f}", "Q", "capex_yoy",
                   note=f"合计 {v['capex_sum']:,.0f} 亿美元" if v.get("capex_sum") else ""),
            Metric("tokens_30d", "OpenRouter 30 天用量变化", tok_chg, "%", "{:+.1f}", "DW", "tokens_chg"),
            Metric("eps_ai", "AI 芯片 EPS 30 天修正（平均）", eps_ai, "%", "{:+.2f}", "DW", "eps_ai",
                   note=names),
        ]
        # 领先指标一览
        if capex_yoy:
            self.lead("1–2 个季度", "ai", "云厂商资本开支同比", f"{capex_yoy[-1][1]:+.1f}%", v["capex_q"],
                      I.direction(capex_yoy[-1][1], I.CAPEX_UP, I.CAPEX_DOWN), "AI 需求的源头，领先英伟达、台积电营收 1–2 个季度")
        if tok_chg:
            self.lead("几天到几周", "ai", "OpenRouter 30 天用量", f"{tok_chg[-1][1]:+.1f}%", tok_chg[-1][0].isoformat(),
                      I.direction(tok_chg[-1][1], I.TOKENS_UP, I.TOKENS_DOWN), "推理需求的高频读数")
        if eps_ai:
            self.lead("几天到几周", "ai", "AI 芯片 EPS 修正", f"{eps_ai[-1][1]:+.2f}%", eps_ai[-1][0].isoformat(),
                      I.direction(eps_ai[-1][1], I.EPS_UP, I.EPS_DOWN), "分析师根据订单和渠道调整预期，常领先财报")
        groups = [("资本开支", charts[:2]), ("用量与价格", [c for c in charts if c in ("tokens", "tokens_chg", "silicon")]),
                  ("预期与营收", [c for c in charts if c in ("eps_ai", "ai_rev")])]
        return groups, metrics, st

    def eps_series(self, ticker: str) -> Series:
        return self.old_series("eps", "ticker", ticker, "revision_30d")

    def eps_avg(self, tickers: tuple[str, ...]) -> tuple[Series, str]:
        ss = {t: self.eps_series(t) for t in tickers}
        have = [t for t in tickers if ss[t]]
        if not have:
            return [], ""
        avg = ts.combine(lambda *xs: sum(xs) / len(xs), *(ss[t] for t in have))
        return avg, "、".join(EPS_NAMES.get(t, t) for t in have)

    # =====================================================================
    # 传统需求
    def trad_demand(self):
        v: dict = {}
        orders = self.fred("A34SNO")
        orders_yoy = ts.pct_change(ts.rolling(orders, 3, "mean", "M"), 12, "M")
        if orders_yoy:
            v["orders_yoy"] = orders_yoy[-1][1]
        tw = self.tw_yoy3(TW_TRAD_DEMAND)
        if tw:
            v["tw_trad_yoy"] = tw[-1][1]
        analog = self.sec_sum(self.group("analog"), "revenue")
        analog_yoy = ts.pct_change(analog, 4, "Q")
        if analog_yoy:
            v["analog_yoy"], v["analog_q"] = analog_yoy[-1][1], qtext(analog_yoy[-1][0])
            if len(analog_yoy) > 1 and analog_yoy[-2][0] == ts._shift_month(analog_yoy[-1][0], -3):
                v["analog_yoy_prev"] = analog_yoy[-2][1]
        eps, names = self.eps_avg(EPS_TRAD)
        if eps:
            v["eps_trad"], v["eps_trad_names"] = eps[-1][1], names
        st = I.trad_demand_state(v)
        charts = self.add(
            Chart("orders", "美国计算机与电子产品新订单：3 个月同比", "%", [Line("同比", orders_yoy)], core=True,
                  start=date(2000, 1, 1), note="FRED A34SNO。M3 调查不单独公布半导体订单，用上一级行业代替。"),
            Chart("tw_trad", "联发科 + 联电：近 3 个月营收同比", "%", [Line("同比", tw)], core=True,
                  start=date(2019, 1, 1), note="手机芯片与成熟制程代工。"),
            Chart("analog", "模拟与 MCU：单季营收同比", "%",
                  [Line("三家合计", analog_yoy)] + [Line(SEC[t][1], ts.pct_change(self.sec_q(t, "revenue"), 4, "Q"), dash=True)
                                                 for t in self.group("analog")],
                  core=True, start=date(2016, 1, 1), note="德州仪器、微芯、亚德诺：工业、汽车、消费电子的需求最先反映在这里。"),
            Chart("tw_trad_each", "传统芯片公司月营收：同比", "%",
                  [Line(TWSE[c][0], self.tw_yoy1(c)) for c in ("2454", "2303", "3711", "2408", "2344")],
                  start=date(2019, 1, 1)),
            Chart("eps_trad", "高通、英特尔：下财年 EPS 30 天修正", "%",
                  [Line(EPS_NAMES.get(t, t), self.eps_series(t)) for t in EPS_TRAD]),
        )
        metrics = [
            Metric("orders_yoy", "美国电子产品新订单（3 个月同比）", orders_yoy, "%", "{:+.1f}", "M", "orders"),
            Metric("tw_trad_yoy", "联发科 + 联电营收（3 个月同比）", tw, "%", "{:+.1f}", "M", "tw_trad"),
            Metric("analog_yoy", "模拟与 MCU 营收同比", analog_yoy, "%", "{:+.1f}", "Q", "analog"),
            Metric("eps_trad", "高通、英特尔 EPS 30 天修正", eps, "%", "{:+.2f}", "DW", "eps_trad", note=names),
        ]
        if orders_yoy:
            self.lead("1–3 个月", "trad", "美国电子产品新订单", f"{orders_yoy[-1][1]:+.1f}%", ts.month_start(orders_yoy[-1][0]).isoformat()[:7],
                      I.direction(orders_yoy[-1][1], I.ORDERS_UP, I.ORDERS_DOWN), "订单领先出货 1–3 个月")
        if eps:
            self.lead("几天到几周", "trad", "高通、英特尔 EPS 修正", f"{eps[-1][1]:+.2f}%", eps[-1][0].isoformat(),
                      I.direction(eps[-1][1], I.EPS_UP, I.EPS_DOWN), "手机与 PC 需求的预期")
        groups = [("订单与营收", [c for c in charts if c in ("orders", "tw_trad", "analog")]),
                  ("细分", [c for c in charts if c in ("tw_trad_each", "eps_trad")])]
        return groups, metrics, st

    # =====================================================================
    # 库存
    def inventory(self):
        v: dict = {}
        kos = {item: ts.clean((_month(r["period"]), _fl(r["value"])) for r in self.src.kosis if r["item"] == item)
               for item in ("ship", "inv")}
        if len(kos["ship"]) > 15 and len(kos["inv"]) > 15:
            ship_s, inv_s, src = kos["ship"], kos["inv"], "韩国半导体"
            note = "韩国统计局半导体出货、库存指数。"
        else:
            ship_s, inv_s, src = self.fred("A34SVS"), self.fred("A34STI"), "美国计算机与电子产品"
            note = "韩国统计局数据还没接入（需要 KOSIS_API_KEY），暂用美国计算机与电子产品的出货（A34SVS）与库存（A34STI），口径比半导体宽。"
        ship_yoy = ts.pct_change(ts.rolling(ship_s, 3, "mean", "M"), 12, "M")
        inv_yoy = ts.pct_change(inv_s, 12, "M")
        gap = ts.combine(lambda a, b: a - b, ship_yoy, inv_yoy)
        if gap:
            d = gap[-1][0]
            v.update(ship_yoy=dict(ship_yoy)[d], inv_yoy=dict(inv_yoy)[d], inv_source=src)
        dio_names, dio_yoy = [], []
        for t in ("MU", "TXN", "MCHP"):
            y = ts.pct_change(self.dio(t), 4, "Q")
            if y:
                dio_names.append(SEC[t][1])
                dio_yoy.append(y[-1][1])
        if dio_yoy:
            v["dio_yoy"], v["dio_names"] = sum(dio_yoy) / len(dio_yoy), "、".join(dio_names)
        st = I.inventory_state(v)
        charts = self.add(
            Chart("inv_cycle", f"库存周期：{src}出货与库存同比", "%",
                  [Line("出货（3 个月均值）同比", ship_yoy), Line("库存同比", inv_yoy)], core=True,
                  start=date(2005, 1, 1), note=note),
            Chart("inv_gap", "出货同比 − 库存同比", "个百分点", [Line("差", gap, "bar")], core=True,
                  start=date(2005, 1, 1), note="> 0 库存压力在减轻，常领先价格和营收拐点 1–3 个月。"),
            Chart("dio", "公司库存天数", "天", [Line(SEC[t][1], self.dio(t)) for t in ("MU", "TXN", "MCHP", "NVDA")],
                  start=date(2016, 1, 1), note="期末存货 ÷ 单季销货成本 × 91。偏滞后，用来确认。"),
        )
        metrics = [
            Metric("ship_yoy", f"{src}出货同比（3 个月均值）", ship_yoy, "%", "{:+.1f}", "M", "inv_cycle"),
            Metric("inv_yoy", f"{src}库存同比", inv_yoy, "%", "{:+.1f}", "M", "inv_cycle"),
            Metric("inv_gap", "出货 − 库存", gap, "个百分点", "{:+.1f}", "M", "inv_gap"),
        ]
        if dio_yoy:
            dio_avg = ts.combine(lambda *xs: sum(xs) / len(xs), *(ts.pct_change(self.dio(t), 4, "Q") for t in ("MU", "TXN", "MCHP") if self.dio(t)))
            metrics.append(Metric("dio_yoy", "公司库存天数同比（平均）", dio_avg, "%", "{:+.1f}", "Q", "dio",
                                  note=v["dio_names"], ref=True))
        if gap:
            self.lead("1–3 个月", "both", "出货 − 库存", f"{gap[-1][1]:+.1f} 个百分点", gap[-1][0].isoformat()[:7],
                      I.direction(gap[-1][1], 0, 0), "库存压力减轻领先价格和营收拐点")
        groups = [("库存周期", [c for c in charts if c in ("inv_cycle", "inv_gap")]), ("公司库存", [c for c in charts if c == "dio"])]
        return groups, metrics, st

    # =====================================================================
    # 价格
    def price(self):
        v: dict = {}
        dram_chg, window, names = [], 30, []
        per: dict[str, tuple[float, int]] = {}
        for p in DRAM:
            s = self.old_series("memory", "product", p, "value")
            row = self.latest_row("memory", "product", p)
            c30 = chg_days(s, 30) if s else None
            if c30 is None and row:
                c30 = _fl(row.get("chg_30d_pct"))
            if c30 is not None:
                per[p] = (c30, 30)
                continue
            c7 = chg_days(s, 7) if s else None
            if c7 is None and row:
                c7 = _fl(row.get("chg_7d_pct"))
            if c7 is not None:
                per[p] = (c7, 7)
        if per:
            window = min(w for _, w in per.values())
            dram_chg = [c for c, w in per.values() if w == window]
            names = [p.split()[0] for p, (_, w) in per.items() if w == window]
            v.update(dram_chg=sum(dram_chg) / len(dram_chg), dram_window=window, dram_names="/".join(names))
        nand_s = self.old_series("memory", "product", NAND, "value")
        nrow = self.latest_row("memory", "product", NAND)
        nand = chg_days(nand_s, 30)
        if nand is not None:
            v.update(nand_chg=nand, nand_window=30)
        elif nrow and _fl(nrow.get("chg_7d_pct")) is not None:
            v.update(nand_chg=_fl(nrow["chg_7d_pct"]), nand_window=7)
        # 现货 vs 合约
        prem = []
        for p, key in ((DRAM[0], "dram_contract_ddr5"), (DRAM[1], "dram_contract_ddr4")):
            spot, con = self.old_series("memory", "product", p, "value"), self.manual(key)
            if spot and con and (spot[-1][0] - con[-1][0]).days <= 60 and con[-1][1]:
                prem.append((spot[-1][1] / con[-1][1] - 1) * 100)
        if prem:
            v["premium"] = sum(prem) / len(prem)
        ppi_yoy = ts.pct_change(self.fred("PCU33443344"), 12, "M")
        if ppi_yoy:
            v["ppi_yoy"] = ppi_yoy[-1][1]
        st = I.price_state(v)

        # GPU 租金：算力的「价格」，进 AI 的供给松紧，不进本维标签
        gpu = {}
        for g in GPUS:
            s = self.old_series("gpu", "gpu", g, "price")
            row = self.latest_row("gpu", "gpu", g)
            c = chg_days(s, 90)
            if c is None and row:
                c = _fl(row.get("chg_90d_pct"))
            if c is not None:
                gpu[g] = c
        self.gpu_90d = statistics.median(gpu.values()) if gpu else None

        charts = self.add(
            Chart("dram", "DRAM 现货价", "美元 / GB",
                  [Line(p, self.old_series("memory", "product", p, "value")) for p in DRAM], core=True,
                  note="来自每日笔记（Session Average，按颗粒容量折成每 GB）；历史从 2026-09 开始。"),
            Chart("gpu", "GPU 租金", "美元 / GPU·小时",
                  [Line(g, self.old_series("gpu", "gpu", g, "price")) for g in GPUS], core=True,
                  note="算力的价格：进 AI 算力的供给松紧，不进本维标签。"),
            Chart("nand", "NAND wafer 现货（TLC 512Gb）", "美元 / GB", [Line("wafer 现货", nand_s)],
                  note="周度报价；参考，不进标签。"),
            Chart("ssd", "零售 SSD 2TB", "美元 / TB", [Line("零售价", self.old_series("memory", "product", SSD, "value"))],
                  note="终端渠道价格，反映消费端传导；参考。"),
            Chart("contract", "DRAM 合约价（手工录入）", "美元 / GB",
                  [Line(MANUAL[k][0], self.manual(k)) for k in ("dram_contract_ddr5", "dram_contract_ddr4")]),
            Chart("ppi", "美国半导体 PPI：同比", "%", [Line("同比", ppi_yoy)], start=date(2005, 1, 1)),
        )
        dram_series = self.old_series("memory", "product", DRAM[0], "value")
        metrics = [
            Metric("dram_chg", f"DRAM 现货 {window} 天变化", self._chg_series(DRAM, window), "%", "{:+.1f}", "DW", "dram",
                   note=f"{v.get('dram_names', '')}；最新 DDR5 {dram_series[-1][1]:.2f} 美元/GB" if dram_series else ""),
            Metric("premium", "现货比合约", [(self.asof, v["premium"])] if prem else [], "%", "{:+.1f}", "O", "contract"),
            Metric("gpu_90d", "GPU 租金 90 天变化（中位数）", [(self.asof, self.gpu_90d)] if gpu else [], "%", "{:+.1f}", "O", "gpu",
                   note="进 AI 算力的供给松紧，不进本维标签；" + "，".join(f"{g} {c:+.0f}%" for g, c in gpu.items())),
            Metric("nand_chg", f"NAND wafer {v.get('nand_window', 7)} 天变化", [(self.asof, v["nand_chg"])] if "nand_chg" in v else [],
                   "%", "{:+.1f}", "O", "nand", ref=True),
            Metric("ppi_yoy", "美国半导体 PPI 同比", ppi_yoy, "%", "{:+.1f}", "M", "ppi", ref=True),
        ]
        if "dram_chg" in v:
            th = I.DRAM_30 if window >= 30 else I.DRAM_7
            self.lead("几天到几周", "both", f"DRAM 现货 {window} 天", f"{v['dram_chg']:+.1f}%", self._last_date("memory"),
                      I.direction(v["dram_chg"], th, -th), "存储周期最快的信号，领先合约价 1–2 个季度")
        if prem:
            self.lead("1–3 个月", "both", "现货比合约", f"{v['premium']:+.1f}%", self.manual("dram_contract_ddr5")[-1][0].isoformat() if self.manual("dram_contract_ddr5") else "",
                      I.direction(v["premium"], I.PREMIUM, -I.PREMIUM), "现货高于合约，下季合约价大概率跟涨")
        if gpu:
            self.lead("几天到几周", "ai", "GPU 租金 90 天（中位数）", f"{self.gpu_90d:+.1f}%", self._last_date("gpu"),
                      I.direction(self.gpu_90d, I.GPU_UP, I.GPU_DOWN), "算力是否紧缺的直接价格")
        groups = [("存储", [c for c in charts if c in ("dram", "nand", "ssd", "contract")]), ("算力", [c for c in charts if c == "gpu"]),
                  ("出厂价格", [c for c in charts if c == "ppi"])]
        return groups, metrics, st

    def _last_date(self, name: str) -> str:
        rows = self.old(name)
        return max(r["date"] for r in rows) if rows else ""

    def _chg_series(self, products: tuple[str, ...], window: int) -> Series:
        """DRAM 平均 window 天变化的序列（给「较上期」用）。"""
        ss = [self.old_series("memory", "product", p, "value") for p in products]
        ss = [s for s in ss if s]
        if not ss:
            return []
        outs = []
        for s in ss:
            o = []
            for i, (d, x) in enumerate(s):
                base = ts.asof(s, d - timedelta(days=window))
                if base and s[0][0] <= d - timedelta(days=window):
                    o.append((d, (x / base - 1) * 100))
            outs.append(o)
        out = ts.combine(lambda *xs: sum(xs) / len(xs), *outs)
        if out:
            return out
        # 历史不够：用笔记里自带的变化列
        col = "chg_30d_pct" if window >= 30 else "chg_7d_pct"
        per = [ts.clean((_d(r["date"]), _fl(r[col])) for r in self.old("memory") if r["product"] == p and _fl(r.get(col)) is not None)
               for p in products]
        per = [p for p in per if p]
        return ts.combine(lambda *xs: sum(xs) / len(xs), *per) if per else []

    # =====================================================================
    # 产能
    def capacity(self):
        v: dict = {}
        equip = self.sec_sum(self.group("equip"), "revenue")
        equip_yoy = ts.pct_change(equip, 4, "Q")
        if equip_yoy:
            v["equip_yoy"], v["equip_q"] = equip_yoy[-1][1], qtext(equip_yoy[-1][0])
        book, sales = dict(self.manual("asml_bookings")), dict(self.manual("asml_sales"))
        btb = sorted((d, book[d] / sales[d]) for d in book if sales.get(d))
        if btb:
            v["asml_btb"] = btb[-1][1]
        util = self.fred("CAPUTLG3344S")
        if util:
            v["util"] = util[-1][1]
        cg = self.manual("tsmc_capex_guide")
        if cg:
            v["tsmc_capex"] = cg[-1][1]
        st = I.capacity_state(v)
        charts = self.add(
            Chart("equip", "设备商单季营收：同比", "%",
                  [Line("三家合计", equip_yoy)] + [Line(SEC[t][1], ts.pct_change(self.sec_q(t, "revenue"), 4, "Q"), dash=True)
                                                 for t in self.group("equip")],
                  core=True, start=date(2016, 1, 1), note="应用材料、泛林、科磊。设备营收领先晶圆产能 1–2 个季度。"),
            Chart("util", "美国半导体产能利用率", "%", [Line("产能利用率", util)], core=True, start=date(2000, 1, 1),
                  note=f"FRED CAPUTLG3344S。≥ {I.UTIL_TIGHT:g}% 偏紧，< {I.UTIL_LOOSE:g}% 偏松。"),
            Chart("asml", "ASML 订单出货比（手工录入）", "倍", [Line("订单 ÷ 销售", btb)]),
        )
        metrics = [
            Metric("equip_yoy", "设备商营收同比", equip_yoy, "%", "{:+.1f}", "Q", "equip"),
            Metric("asml_btb", "ASML 订单出货比", btb, "倍", "{:.2f}", "O", "asml"),
            Metric("util", "美国半导体产能利用率", util, "%", "{:.1f}", "M", "util", ref=True),
            Metric("tsmc_capex", "台积电资本开支指引（中值）", cg, "亿美元", "{:,.0f}", "O", ref=True),
        ]
        if equip_yoy:
            self.lead("1–2 个季度", "both", "设备商营收同比", f"{equip_yoy[-1][1]:+.1f}%", v["equip_q"],
                      "扩产" if equip_yoy[-1][1] > I.EQUIP_UP else "收缩" if equip_yoy[-1][1] < I.EQUIP_DOWN else "中性",
                      "扩产 1–2 个季度后供给增加；看作供给信号，不计入景气合计", score=False)
        groups = [("设备与产能", charts)]
        return groups, metrics, st

    # =====================================================================
    # 出货确认
    def shipments(self, price_label: str):
        v: dict = {}
        rows = {}
        for r in self.old("korea"):
            rows[r["period"]] = r  # 同一期间多行时后一行（asof 更新）覆盖
        k_month = ts.clean((_month(p), _fl(r["month_usd_mn"])) for p, r in rows.items() if _fl(r.get("month_usd_mn")) is not None)
        k_yoy = ts.clean((_month(p), _fl(r["month_yoy"])) for p, r in rows.items() if _fl(r.get("month_yoy")) is not None)
        k_d20 = ts.clean((_month(p), _fl(r["d20_yoy"])) for p, r in rows.items() if _fl(r.get("d20_yoy")) is not None)
        if rows:
            p = max(rows)
            r = rows[p]
            for col, txt in (("month_yoy", "全月"), ("d20_yoy", "前 20 日"), ("d10_yoy", "前 10 日")):
                if _fl(r.get(col)) is not None:
                    v["korea_yoy"], v["korea_period"] = _fl(r[col]), f"{p} {txt}"
                    break
            if v.get("korea_yoy") is not None and v["korea_yoy"] > 30 and price_label == "涨价":
                v["korea_mix"] = "同期存储现货在涨，增长里价格占了相当部分，出货量的增长要小得多"
        tsmc = self.tw_yoy3(TW_AI_CONFIRM)
        if tsmc:
            v["tsmc_yoy"] = tsmc[-1][1]
        ip_yoy = ts.pct_change(ts.rolling(self.fred("IPG3344S"), 3, "mean", "M"), 12, "M")
        if ip_yoy:
            v["ip_yoy"] = ip_yoy[-1][1]
        st = I.shipments_state(v)
        charts = self.add(
            Chart("korea", "韩国芯片出口：同比", "%", [Line("全月", k_yoy), Line("前 20 日", k_d20, dash=True)], core=True,
                  note="来自每日笔记（关税厅速报、产业通商部全月初值）；历史从 2026-06 开始。"),
            Chart("korea_usd", "韩国芯片出口：全月金额", "百万美元", [Line("金额", k_month, "bar")]),
            Chart("tsmc", "台积电：近 3 个月营收同比", "%", [Line("3 个月同比", tsmc), Line("单月同比", self.tw_yoy1("2330"), dash=True)],
                  core=True, start=date(2019, 1, 1)),
            Chart("tw_ai", "AI 服务器与载板：月营收同比", "%", [Line(TWSE[c][0], self.tw_yoy1(c)) for c in ("2382", "6669", "3037")],
                  start=date(2019, 1, 1), note="广达、纬颖（服务器组装）、欣兴（ABF 载板）；参考。"),
            Chart("ip", "美国半导体工业产出：3 个月同比", "%", [Line("同比", ip_yoy)], start=date(2000, 1, 1)),
        )
        latest_korea = []
        if rows:
            p = max(rows)
            if v.get("korea_yoy") is not None:
                latest_korea = [(_month(p), v["korea_yoy"])]
        metrics = [
            Metric("korea_yoy", "韩国芯片出口同比", latest_korea, "%", "{:+.1f}", "M", "korea", note=v.get("korea_period", "")),
            Metric("tsmc_yoy", "台积电营收（3 个月同比）", tsmc, "%", "{:+.1f}", "M", "tsmc"),
            Metric("ip_yoy", "美国半导体工业产出（3 个月同比）", ip_yoy, "%", "{:+.1f}", "M", "ip"),
        ]
        groups = [("出货", [c for c in charts if c in ("korea", "tsmc", "korea_usd")]),
                  ("更多", [c for c in charts if c in ("tw_ai", "ip")])]
        return groups, metrics, st

    # =====================================================================
    # 日历
    def releases(self) -> dict:
        cal = (self.src.old.get("calendar") or {}).get("events", [])
        today_bj = (self.now + timedelta(hours=8)).date()
        end = today_bj + timedelta(days=UPCOMING_DAYS)
        up = []
        for e in cal:
            try:
                d = _d(e["date"])
            except (KeyError, ValueError):
                continue
            if not (today_bj <= d <= end):
                continue
            prev = e.get("previous") or ""
            cons = e.get("consensus") or ""
            up.append({"bj_date": d.isoformat(), "bj": f"{d.isoformat()} {e.get('time_bj') or ''}".strip(),
                       "title": e["title"], "ref": "", "forecast_text": f"EPS 预期 {cons}" if cons else "",
                       "previous_text": prev, "importance": importance.rate(e["title"]), "url": e.get("url", "")})
        up.sort(key=lambda x: (x["bj_date"], x["bj"]))
        return {"upcoming": up, "recent": [], "importance_rule": importance.RULE}

    # =====================================================================
    def build(self) -> dict:
        ai = self.ai_demand()
        trad = self.trad_demand()
        inv = self.inventory()
        price = self.price()
        cap = self.capacity()
        ship = self.shipments(price[2]["label"])
        dims = [("ai_demand", "AI 需求", ai), ("trad_demand", "传统需求", trad), ("inventory", "库存周期", inv),
                ("price", "价格", price), ("capacity", "产能", cap), ("shipments", "出货确认", ship)]
        st = {k: s for k, _, (_, _, s) in dims}

        def has(s: dict) -> int | None:
            return None if s["label"] == "数据不足" else s["level"]

        gpu_vote = I.vote(self.gpu_90d, I.GPU_UP, I.GPU_DOWN) if self.gpu_90d is not None else None
        ai_t = I.tightness([gpu_vote, has(st["price"]), has(st["capacity"])])
        trad_t = I.tightness([has(st["inventory"]), has(st["price"]), has(st["capacity"])])
        ai_why = "、".join(x for x in (
            gpu_vote is not None and f"GPU 租金 90 天 {self.gpu_90d:+.0f}%",
            has(st["price"]) is not None and f"存储{st['price']['label']}",
            has(st["capacity"]) is not None and f"设备投资{st['capacity']['label']}") if x)
        trad_why = "、".join(x for x in (
            has(st["inventory"]) is not None and st["inventory"]["label"],
            has(st["price"]) is not None and f"存储{st['price']['label']}",
            has(st["capacity"]) is not None and f"设备投资{st['capacity']['label']}") if x)
        ai_v = I.line_verdict("AI 算力", st["ai_demand"], ai_t, ai_why)
        trad_v = I.line_verdict("传统芯片", st["trad_demand"], trad_t, trad_why)
        scored = [{"偏多": 1, "偏空": -1, "中性": 0}[x["dir"]] for x in self.leading if x["score"]]
        lead_score = sum(scored) / len(scored) if scored else None
        confirm = I.confirm_text(lead_score, st["shipments"])
        env = I.environment(ai_v, trad_v, confirm)

        out_dims, sections = [], []
        for key, name, (groups, metrics, state) in dims:
            out_dims.append({"key": key, "name": name, "label": state["label"], "why": state.get("why", []),
                             "head": state["head"],
                             "metrics": [x for x in (metric_json(m, state.get("anchors", {}), self.charts) for m in metrics) if x]})
            sections.append({"key": key, "name": name, "groups": [{"name": g, "charts": ids} for g, ids in groups if ids]})
        tier_order = {"几天到几周": 0, "1–3 个月": 1, "1–2 个季度": 2}
        leading = sorted(self.leading, key=lambda x: (tier_order[x["tier"]], x["line"]))
        return {
            "asof": self.asof.isoformat(),
            "method": "AI 算力与传统芯片分两条线判断，每条线按「需求 × 供给松紧」落到象限：需求看领先指标"
                      "（AI：云厂商资本开支、OpenRouter 用量、AI 芯片 EPS 修正；传统：美国电子产品新订单、联发科 + 联电营收、"
                      "模拟芯片营收、高通与英特尔 EPS 修正），每个数按锚点投一票取平均；供给松紧由库存周期（出货 − 库存）、"
                      "存储价格、设备投资（扩产记为未来偏松）合成，AI 这条线另看 GPU 租金。"
                      "出货确认（韩国芯片出口、台积电营收、美国半导体产出）是同步指标，用来检查领先信号有没有被确认，不进象限。"
                      "阈值都写在 pipeline/semis/interpret.py。",
            "verdict": {"name": env["name"], "headline": env["head"], "lines": env["lines"], "label": "整体景气",
                        "lead_score": lead_score},
            "leading": leading,
            "dimensions": out_dims,
            "releases": self.releases(),
            "sections": sections,
            "charts": self.charts,
        }


def build_dashboard(src: Sources, asof: date | None = None, now: datetime | None = None) -> dict:
    return SemisBuilder(src, asof, now).build()
