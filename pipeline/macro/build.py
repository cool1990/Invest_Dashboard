"""把原始 FRED 序列整理成页面要用的 data/macro/dashboard.json。

输出分四块：
- dimensions：增长 / 通胀 / 流动性 / 财政 / 货币政策 五维评分（月度，2000 年起）。
- kpis：每个维度的关键读数（最新值、前值、历史分位）。
- charts：每张图的序列。日频压成周频，月/季频原样；从 1990 年起。
- sections：页面按什么顺序放哪些图。

缺数据的序列会被跳过；某张图一条序列都没有，就不输出这张图。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from .. import series as ts
from ..series import Series

DISPLAY_START = date(1990, 1, 1)
SCORE_START = date(2000, 1, 1)
Z_CLIP = 3.0
LABEL_BAND = 0.5


def _r(v: float) -> float:
    if v == 0 or not math.isfinite(v):
        return 0.0
    a = abs(v)
    if a >= 1000:
        return round(v, 1)
    if a >= 10:
        return round(v, 2)
    return round(v, 4)


def _pack(s: Series, start: date = DISPLAY_START) -> list[list]:
    return [[d.isoformat(), _r(v)] for d, v in s if d >= start]


# ---------------------------------------------------------------------------
# 图表与关键读数的描述


@dataclass
class Line:
    name: str
    data: Series
    kind: str = "line"  # line | bar
    stack: str | None = None
    dash: bool = False


@dataclass
class Chart:
    id: str
    title: str
    unit: str
    lines: list[Line]
    kind: str = "time"  # time | path
    stacked: str | None = None  # None | bar | area
    note: str = ""
    start: date = DISPLAY_START
    categories: list[str] = field(default_factory=list)

    def to_json(self) -> dict | None:
        lines = [ln for ln in self.lines if ln.data]
        if not lines:
            return None
        out = {
            "id": self.id,
            "title": self.title,
            "unit": self.unit,
            "kind": self.kind,
            "stacked": self.stacked,
            "note": self.note,
            "series": [],
        }
        if self.kind == "path":
            out["categories"] = self.categories
            for ln in lines:
                out["series"].append({"name": ln.name, "type": ln.kind, "dash": ln.dash,
                                      "data": [None if v is None else _r(v) for v in ln.data]})
            return out
        for ln in lines:
            out["series"].append({"name": ln.name, "type": ln.kind, "dash": ln.dash,
                                  "data": _pack(ln.data, self.start)})
        out["series"] = [s for s in out["series"] if s["data"]]
        if not out["series"]:
            return None
        out["last_date"] = max(s["data"][-1][0] for s in out["series"])
        return out


@dataclass
class Kpi:
    name: str
    data: Series
    unit: str
    fmt: str = "{:.1f}"
    hint: str = ""

    def to_json(self) -> dict | None:
        if not self.data:
            return None
        d, v = self.data[-1]
        prev = self.data[-2] if len(self.data) >= 2 else None
        hist = [x for dd, x in self.data if dd >= SCORE_START]
        return {
            "name": self.name,
            "value": _r(v),
            "text": self.fmt.format(v),
            "date": d.isoformat(),
            "prev": _r(prev[1]) if prev else None,
            "prev_text": self.fmt.format(prev[1]) if prev else None,
            "prev_date": prev[0].isoformat() if prev else None,
            "unit": self.unit,
            "pctile": round(ts.percentile_rank(hist, v)) if len(hist) >= 24 else None,
            "hint": self.hint,
        }


@dataclass
class Component:
    """评分里的一项：一条月度序列 + 方向（+1 表示越高分越高）。"""

    name: str
    monthly: Series
    sign: int
    max_gap_days: int = 125


# ---------------------------------------------------------------------------


class MacroBuilder:
    def __init__(self, raw: dict[str, Series], effr_expect: list[dict] | None = None,
                 asof: date | None = None):
        self.raw = raw
        self.effr_expect = effr_expect or []
        self.asof = asof or date.today()
        self.charts: dict[str, dict] = {}
        self.missing: set[str] = {k for k, v in raw.items() if not v}

    # -- 取数 ---------------------------------------------------------------
    def s(self, sid: str) -> Series:
        return self.raw.get(sid) or []

    def m(self, sid: str, how: str = "mean") -> Series:
        return ts.to_monthly(self.s(sid), how)

    def w(self, sid: str, k: float = 1.0) -> Series:
        return ts.scale(ts.to_weekly(self.s(sid)), k)

    def add(self, *charts: Chart) -> list[str]:
        ids = []
        for c in charts:
            js = c.to_json()
            if js:
                self.charts[c.id] = js
                ids.append(c.id)
        return ids

    # -- 通用派生 -----------------------------------------------------------
    def yoy_m(self, sid: str) -> Series:
        return ts.pct_change(self.m(sid, "last"), 12, "M")

    # =====================================================================
    # 增长
    def growth(self) -> tuple[list, list, list[Component]]:
        gdp = self.s("GDPC1")
        gdp_qoq = ts.pct_change(gdp, 1, "Q", annualize=4)
        gdp_yoy = ts.pct_change(gdp, 4, "Q")
        # 年度：只用四个季度齐全的年份
        by_year: dict[int, list[float]] = {}
        for d, v in gdp:
            by_year.setdefault(d.year, []).append(v)
        annual = [(date(y, 1, 1), sum(v) / 4) for y, v in sorted(by_year.items()) if len(v) == 4]
        gdp_annual = ts.pct_change(annual, 1, "A")
        gdpnow = self.s("GDPNOW")

        payems = self.m("PAYEMS", "last")
        nfp = ts.diff(payems, 1, "M")
        nfp3 = ts.rolling(nfp, 3)
        unrate = self.m("UNRATE", "last")
        u6 = self.m("U6RATE", "last")
        sahm = self.m("SAHMREALTIME", "last")
        civpart = self.m("CIVPART", "last")
        jol = self.m("JTSJOL", "last")
        vu = ts.combine(lambda a, b: a / b if b else None, jol, self.m("UNEMPLOY", "last"))
        quits = self.m("JTSQUR", "last")
        ahe = self.m("CES0500000003", "last")
        ahe_yoy = ts.pct_change(ahe, 12, "M")
        ahe_3m = ts.pct_change(ahe, 3, "M", annualize=12)
        icsa4 = ts.scale(ts.rolling_obs(self.s("ICSA"), 4), 1e-3)
        ccsa = ts.scale(self.s("CCSA"), 1e-3)

        rs = self.m("RSAFS", "last")
        ctrl = ts.combine(lambda a, b, c, d, e: a - b - c - d - e, rs, self.m("RSMVPD", "last"),
                          self.m("RSGASS", "last"), self.m("RSBMGESD", "last"), self.m("RSFSDP", "last"))
        rs_yoy = ts.pct_change(rs, 12, "M")
        rs_mom = ts.pct_change(rs, 1, "M")
        ctrl_yoy = ts.pct_change(ctrl, 12, "M")
        ctrl_mom = ts.pct_change(ctrl, 1, "M")
        rpce_yoy = self.yoy_m("PCEC96")
        rpce_mom = ts.pct_change(self.m("PCEC96", "last"), 1, "M")
        rdpi_yoy = self.yoy_m("DSPIC96")
        saving = self.m("PSAVERT", "last")
        credit_yoy = self.yoy_m("TOTALSL")
        revol_yoy = self.yoy_m("REVOLSL")
        cc_delinq = self.s("DRCCLACBS")

        dgo_yoy = ts.pct_change(ts.rolling(self.m("DGORDER", "last"), 3), 12, "M")
        core_capex_yoy = ts.pct_change(ts.rolling(self.m("NEWORDER", "last"), 3), 12, "M")
        philly = self.m("GACDFSA066MSFRBPHI", "last")
        empire = self.m("GACDISA066MSFRBNY", "last")
        indpro_yoy = self.yoy_m("INDPRO")

        houst = self.m("HOUST", "last")
        permit = self.m("PERMIT", "last")
        hsn = self.m("HSN1F", "last")
        msacsr = self.m("MSACSR", "last")
        cs_yoy = self.yoy_m("CSUSHPINSA")
        mort = self.w("MORTGAGE30US")

        groups = [
            ("产出", self.add(
                Chart("gdp_q", "实际 GDP：季环比年化与同比", "%", [
                    Line("季环比年化", gdp_qoq, "bar"), Line("同比", gdp_yoy)],
                    note="季度数据，来源 BEA。柱为季环比年化，线为同比。"),
                Chart("gdp_a", "实际 GDP：年度增速", "%", [Line("年度增速", gdp_annual, "bar")],
                      note="按全年四个季度均值计算。"),
                Chart("gdpnow", "GDPNow：本季度实时预测", "%", [Line("GDPNow", gdpnow)],
                      note="亚特兰大联储对当季实际 GDP 环比年化的模型预测。", start=date(2014, 1, 1)),
                Chart("indpro", "工业产出与实际可支配收入同比", "%", [
                    Line("工业产出同比", indpro_yoy), Line("实际可支配收入同比", rdpi_yoy)]),
            )),
            ("就业", self.add(
                Chart("nfp", "非农新增就业", "千人", [
                    Line("当月新增", nfp, "bar"), Line("3 个月均值", nfp3)],
                    note="2020 年疫情期间数值极端，建议看近 5 年。"),
                Chart("unrate", "失业率", "%", [
                    Line("U-3 失业率", unrate), Line("U-6 失业率", u6)]),
                Chart("sahm", "Sahm 规则衰退指标", "百分点", [Line("Sahm 实时", sahm)],
                      note="失业率 3 个月均值较前 12 个月低点上升 ≥0.5 个百分点，历史上对应衰退开始。"),
                Chart("jolts", "职位空缺 / 失业人数", "倍", [Line("空缺/失业", vu)],
                      note="大于 1 表示职位多于求职者，劳动力市场偏紧。"),
                Chart("jolts_lv", "职位空缺数", "百万个", [Line("职位空缺", ts.scale(jol, 1e-3))]),
                Chart("quits", "离职率与劳动参与率", "%", [
                    Line("离职率", quits), Line("劳动参与率", civpart)]),
                Chart("ahe", "平均时薪增速", "%", [
                    Line("同比", ahe_yoy), Line("3 个月年化", ahe_3m, dash=True)]),
                Chart("claims", "初请失业金（4 周均值）", "千人", [Line("初请 4 周均值", icsa4)],
                      start=date(2000, 1, 1)),
                Chart("ccsa", "续请失业金", "千人", [Line("续请", ccsa)], start=date(2000, 1, 1)),
            )),
            ("消费", self.add(
                Chart("retail_yoy", "零售销售同比", "%", [
                    Line("零售总额", rs_yoy), Line("控制组", ctrl_yoy)],
                    note="控制组 = 零售总额 − 汽车 − 加油站 − 建材 − 餐饮，进入 GDP 的消费核算。"),
                Chart("retail_mom", "零售销售环比", "%", [
                    Line("零售总额", rs_mom, "bar"), Line("控制组", ctrl_mom, "bar")]),
                Chart("real_pce", "实际个人消费支出", "%", [
                    Line("同比", rpce_yoy), Line("环比", rpce_mom, "bar")]),
                Chart("saving", "个人储蓄率", "%", [Line("储蓄率", saving)]),
                Chart("credit", "消费者信贷同比", "%", [
                    Line("消费者信贷总额", credit_yoy), Line("循环信贷（信用卡为主）", revol_yoy)]),
                Chart("cc_delinq", "信用卡贷款拖欠率", "%", [Line("拖欠率", cc_delinq)],
                      note="季度，商业银行口径，来源美联储。"),
            )),
            ("企业与制造业", self.add(
                Chart("durables", "耐用品订单同比（3 个月均值）", "%", [
                    Line("耐用品新订单", dgo_yoy), Line("核心资本品（非国防除飞机）", core_capex_yoy)]),
                Chart("regional", "地区联储制造业调查", "扩散指数", [
                    Line("费城联储", philly), Line("纽约联储", empire)],
                    note="ISM PMI 不在 FRED，这里先用地区联储调查代替；0 以上为扩张。"),
            )),
            ("地产", self.add(
                Chart("housing_starts", "新屋开工与建筑许可", "千套（年化）", [
                    Line("新屋开工", houst), Line("建筑许可", permit)]),
                Chart("new_home", "新屋销售", "千套（年化）", [Line("新屋销售", hsn)]),
                Chart("home_supply", "新屋库存月数", "月", [Line("库存月数", msacsr)]),
                Chart("home_price", "房价同比（Case-Shiller 全国）", "%", [Line("同比", cs_yoy)]),
                Chart("mortgage", "30 年期房贷利率", "%", [Line("30 年固定", mort)]),
            )),
        ]
        kpis = [
            Kpi("实际 GDP 季环比年化", gdp_qoq, "%"),
            Kpi("实际 GDP 同比", gdp_yoy, "%"),
            Kpi("GDPNow", gdpnow, "%"),
            Kpi("非农新增", nfp, "千人", "{:,.0f}"),
            Kpi("失业率", unrate, "%"),
            Kpi("Sahm 指标", sahm, "百分点", "{:.2f}"),
            Kpi("职位空缺", ts.scale(jol, 1e-3), "百万个", "{:.2f}"),
            Kpi("时薪同比", ahe_yoy, "%"),
            Kpi("初请 4 周均值", icsa4, "千人", "{:.0f}"),
            Kpi("零售控制组环比", ctrl_mom, "%", "{:.2f}"),
            Kpi("实际 PCE 同比", rpce_yoy, "%"),
            Kpi("储蓄率", saving, "%"),
            Kpi("信用卡拖欠率", cc_delinq, "%", "{:.2f}"),
            Kpi("新屋开工", houst, "千套", "{:,.0f}"),
        ]
        comps = [
            Component("实际 GDP 同比", ts.to_monthly(gdp_yoy), +1, 200),
            Component("非农 3 个月均值", nfp3, +1),
            Component("失业率 12 个月变化", ts.diff(unrate, 12, "M"), -1),
            Component("初请同比", ts.pct_change(ts.to_monthly(self.s("ICSA")), 12, "M"), -1),
            Component("实际 PCE 同比", rpce_yoy, +1),
            Component("核心资本品订单同比", core_capex_yoy, +1),
            Component("地区联储制造业", ts.combine(lambda a, b: (a + b) / 2, philly, empire) or philly, +1),
            Component("新屋开工同比", ts.pct_change(houst, 12, "M"), +1),
        ]
        return groups, kpis, comps

    # =====================================================================
    # 通胀
    def pce_contributions(self) -> tuple[list[Line], list[Line]]:
        """按贡献拆分 PCE 价格。

        第 i 项对总体的贡献 ≈ 上一期（或 12 个月前）的名义支出份额 × 该项价格变化。
        链式加总不严格可加，误差通常在 0.01 个百分点量级。
        能源分成能源商品（汽油等）与能源服务（电、燃气），分别从商品和服务中扣出。
        """
        total_n = self.m("PCE", "last")
        pairs = {
            "goods": ("DGDSRG3M086SBEA", "DGDSRC1"),
            "services": ("DSERRG3M086SBEA", "PCES"),
            "food": ("DFXARG3M086SBEA", "DFXARC1"),
            "energy": ("DNRGRG3M086SBEA", "DNRGRC1"),
            "energy_goods": ("DGOERG3M086SBEA", "DGOERC1"),
            "housing": ("DHSGRG3M086SBEA", "DHSGRC1"),
        }

        def contrib(key: str, lag: int) -> Series:
            p_id, n_id = pairs[key]
            p = self.m(p_id, "last")
            n = self.m(n_id, "last")
            share = ts.combine(lambda a, b: a / b if b else None, n, total_n)
            chg = ts.pct_change(p, lag, "M")
            share_lag = {ts._shift_month(d, lag): v for d, v in share}
            return [(d, share_lag[d] * v) for d, v in chg if d in share_lag]

        def build(lag: int) -> list[Line]:
            c = {k: contrib(k, lag) for k in pairs}
            sub = lambda *xs: ts.combine(lambda a, *rest: a - sum(rest), *xs)  # noqa: E731
            energy_services = sub(c["energy"], c["energy_goods"])
            core_goods = sub(c["goods"], c["food"], c["energy_goods"])
            core_services = sub(c["services"], energy_services)
            lines = [Line("食品", c["food"], "bar", stack="pce"),
                     Line("能源", c["energy"], "bar", stack="pce"),
                     Line("核心商品", core_goods, "bar", stack="pce")]
            if c["housing"]:
                lines += [Line("住房", c["housing"], "bar", stack="pce"),
                          Line("核心服务除住房", sub(core_services, c["housing"]), "bar", stack="pce")]
            else:
                lines.append(Line("核心服务", core_services, "bar", stack="pce"))
            return lines

        return build(1), build(12)

    def inflation(self) -> tuple[list, list, list[Component]]:
        pce = self.m("PCEPI", "last")
        core = self.m("PCEPILFE", "last")
        pce_yoy = ts.pct_change(pce, 12, "M")
        core_yoy = ts.pct_change(core, 12, "M")
        pce_mom = ts.pct_change(pce, 1, "M")
        core_mom = ts.pct_change(core, 1, "M")
        core_3m = ts.pct_change(core, 3, "M", annualize=12)
        core_6m = ts.pct_change(core, 6, "M", annualize=12)
        cpi = self.m("CPIAUCSL", "last")
        ccpi = self.m("CPILFESL", "last")
        cpi_yoy = ts.pct_change(cpi, 12, "M")
        ccpi_yoy = ts.pct_change(ccpi, 12, "M")
        ccpi_mom = ts.pct_change(ccpi, 1, "M")
        mom_lines, yoy_lines = self.pce_contributions()
        fwd = self.w("T5YIFR")
        be10 = self.w("T10YIE")
        mich = self.m("MICH", "last")

        groups = [
            ("PCE", self.add(
                Chart("pce_yoy", "PCE 与核心 PCE 同比", "%", [
                    Line("PCE", pce_yoy), Line("核心 PCE", core_yoy)],
                    note="美联储 2% 目标针对的是 PCE 同比。"),
                Chart("pce_mom", "PCE 与核心 PCE 环比", "%", [
                    Line("PCE", pce_mom, "bar"), Line("核心 PCE", core_mom, "bar")]),
                Chart("core_pce_ann", "核心 PCE 年化动能", "%", [
                    Line("3 个月年化", core_3m), Line("6 个月年化", core_6m), Line("同比", core_yoy, dash=True)]),
                Chart("pce_contrib_mom", "PCE 环比：按贡献拆分", "百分点",
                      mom_lines + [Line("PCE 环比", pce_mom)], stacked="bar", start=date(2015, 1, 1),
                      note="贡献 = 上月名义支出份额 × 分项价格环比。能源含汽油与电、燃气；"
                           "核心服务除住房即常说的「超级核心」。"),
                Chart("pce_contrib_yoy", "PCE 同比：按贡献拆分", "百分点",
                      yoy_lines + [Line("PCE 同比", pce_yoy)], stacked="bar", start=date(2005, 1, 1),
                      note="贡献 = 12 个月前名义支出份额 × 分项价格同比。"),
            )),
            ("CPI 与预期", self.add(
                Chart("cpi", "CPI 与核心 CPI 同比", "%", [
                    Line("CPI", cpi_yoy), Line("核心 CPI", ccpi_yoy)]),
                Chart("cpi_mom", "核心 CPI 环比", "%", [Line("核心 CPI 环比", ccpi_mom, "bar")]),
                Chart("expect", "通胀预期", "%", [
                    Line("5y5y 远期", fwd), Line("10 年盈亏平衡", be10), Line("密歇根一年期", mich)]),
            )),
        ]
        kpis = [
            Kpi("PCE 同比", pce_yoy, "%", "{:.2f}"),
            Kpi("核心 PCE 同比", core_yoy, "%", "{:.2f}"),
            Kpi("核心 PCE 环比", core_mom, "%", "{:.2f}"),
            Kpi("核心 PCE 3 个月年化", core_3m, "%", "{:.2f}"),
            Kpi("CPI 同比", cpi_yoy, "%", "{:.2f}"),
            Kpi("核心 CPI 同比", ccpi_yoy, "%", "{:.2f}"),
            Kpi("核心 CPI 环比", ccpi_mom, "%", "{:.2f}"),
            Kpi("5y5y 远期通胀", fwd, "%", "{:.2f}"),
            Kpi("密歇根一年期预期", mich, "%"),
        ]
        comps = [
            Component("核心 PCE 同比", core_yoy, +1),
            Component("核心 PCE 3 个月年化", core_3m, +1),
            Component("核心 CPI 同比", ccpi_yoy, +1),
            Component("时薪同比", ts.pct_change(self.m("CES0500000003", "last"), 12, "M"), +1),
            Component("5y5y 远期通胀", self.m("T5YIFR"), +1),
            Component("密歇根一年期预期", mich, +1),
        ]
        return groups, kpis, comps

    # =====================================================================
    # 流动性
    def reserves_frame(self) -> dict[str, Series]:
        """以美联储资产负债表的周三为基准，拆出准备金。

        准备金 = 总资产 − ON RRP − TGA − 流通中货币 − 其他负债与资本（倒挤）。
        「其他」把外国官方逆回购、存款机构以外的存款、资本等都算在里面。
        """
        assets = ts.scale(self.s("WALCL"), 1e-3)
        tga = ts.scale(self.s("WDTGAL"), 1e-3)
        reserves = ts.scale(self.s("WRBWFRBL"), 1e-3)
        rrp = ts.asof_align(assets, self.s("RRPONTSYD"), 4)
        cur = ts.asof_align(assets, ts.scale(self.s("WCURCIR"), 1e-3), 6)
        # RRP 早年没有日度数据时按 0 处理，避免整段缺失
        rrp_map = dict(rrp)
        rrp = [(d, rrp_map.get(d, 0.0)) for d, _ in assets]
        other = ts.combine(lambda a, r, t, c, res: a - r - t - c - res, assets, rrp, tga, cur, reserves)
        keep = {d for d, _ in other}
        f = lambda s: [(d, v) for d, v in s if d in keep]  # noqa: E731
        return {"assets": f(assets), "reserves": f(reserves), "rrp": f(rrp), "tga": f(tga),
                "currency": f(cur), "other": other}

    def liquidity(self) -> tuple[list, list, list[Component]]:
        fr = self.reserves_frame()
        n = 4
        chg = {k: ts.diff_obs(v, n) for k, v in fr.items()}
        neg = lambda s: ts.scale(s, -1)  # noqa: E731
        iorb = ts.splice(self.s("IOER"), self.s("IORB"))
        sofr_iorb = ts.scale(ts.combine(lambda a, b: a - b, self.s("SOFR"), iorb), 100)
        effr_iorb = ts.scale(ts.combine(lambda a, b: a - b, self.s("EFFR"), iorb), 100)
        nfci = self.s("NFCI")
        hy = self.w("BAMLH0A0HYM2")
        usd = self.w("DTWEXBGS")
        walcl_yoy = ts.pct_change(ts.to_monthly(fr["assets"], "last"), 12, "M")

        groups = [
            ("准备金拆分", self.add(
                Chart("reserves_stack", "美联储负债结构：准备金从哪里来", "十亿美元", [
                    Line("准备金", fr["reserves"], stack="bs"),
                    Line("ON RRP", fr["rrp"], stack="bs"),
                    Line("TGA", fr["tga"], stack="bs"),
                    Line("流通中货币", fr["currency"], stack="bs"),
                    Line("其他负债与资本", fr["other"], stack="bs")],
                    stacked="area", start=date(2008, 1, 1),
                    note="堆叠之和 = 美联储总资产。准备金 = 总资产 − ON RRP − TGA − 流通中货币 − 其他。"),
                Chart("reserves_chg", f"准备金 {n} 周变化：按来源拆分", "十亿美元", [
                    Line("总资产", chg["assets"], "bar", stack="d"),
                    Line("ON RRP（取负）", neg(chg["rrp"]), "bar", stack="d"),
                    Line("TGA（取负）", neg(chg["tga"]), "bar", stack="d"),
                    Line("流通中货币（取负）", neg(chg["currency"]), "bar", stack="d"),
                    Line("其他（取负）", neg(chg["other"]), "bar", stack="d"),
                    Line("准备金变化", chg["reserves"])],
                    stacked="bar", start=date(2019, 1, 1),
                    note="柱子之和 = 准备金变化。正值表示该项在增加准备金，例如 TGA 下降、RRP 下降。"),
                Chart("reserves_lv", "准备金余额", "十亿美元", [Line("准备金（周三）", fr["reserves"])],
                      start=date(2008, 1, 1)),
            )),
            ("资金利率与金融状况", self.add(
                Chart("sofr_iorb", "SOFR − IORB 与 EFFR − IORB", "基点", [
                    Line("SOFR − IORB", ts.to_weekly(sofr_iorb)),
                    Line("EFFR − IORB", ts.to_weekly(effr_iorb))],
                    start=date(2018, 4, 1),
                    note="周内最后一个交易日。SOFR 持续高于 IORB 说明回购市场资金偏紧、准备金接近不充裕。"
                         "2021-07 前用 IOER。"),
                Chart("nfci", "芝加哥联储金融状况指数 NFCI", "指数", [Line("NFCI", nfci)],
                      note="0 为历史平均，正值偏紧、负值偏松。"),
                Chart("hy", "高收益债利差", "%", [Line("HY OAS", hy)],
                      note="FRED 上的 ICE 数据只保留近几年。"),
                Chart("usd", "美元广义指数", "指数", [Line("美元指数", usd)], start=date(2006, 1, 1)),
            )),
        ]
        last_res = fr["reserves"]
        kpis = [
            Kpi("准备金", last_res, "十亿美元", "{:,.0f}"),
            Kpi(f"准备金 {n} 周变化", chg["reserves"], "十亿美元", "{:+,.0f}"),
            Kpi("美联储总资产", fr["assets"], "十亿美元", "{:,.0f}"),
            Kpi("ON RRP", fr["rrp"], "十亿美元", "{:,.0f}"),
            Kpi("TGA", fr["tga"], "十亿美元", "{:,.0f}"),
            Kpi("SOFR − IORB", sofr_iorb, "基点", "{:+.0f}"),
            Kpi("NFCI", nfci, "指数", "{:.2f}"),
            Kpi("高收益利差", hy, "%", "{:.2f}"),
        ]
        comps = [
            Component("准备金同比", ts.pct_change(ts.to_monthly(fr["reserves"], "mean"), 12, "M"), +1),
            Component("美联储总资产同比", walcl_yoy, +1),
            Component("SOFR − IORB", ts.to_monthly(sofr_iorb), -1),
            Component("NFCI", ts.to_monthly(nfci), -1),
            Component("高收益利差", self.m("BAMLH0A0HYM2"), -1),
        ]
        return groups, kpis, comps

    # =====================================================================
    # 财政
    def fiscal(self) -> tuple[list, list, list[Component]]:
        mts = self.m("MTSDS133FMS", "last")
        deficit12 = ts.scale(ts.rolling(mts, 12, "sum"), -1e-3)  # 正数 = 赤字，十亿美元
        gdp = self.s("GDP")
        deficit_pct = [(d, v / g * 100) for d, v in deficit12
                       if (g := ts.asof(gdp, d, 200))]
        annual_def = ts.scale(self.s("FYFSGDA188S"), -1)
        interest_pct = ts.combine(lambda a, b: a / b * 100 if b else None,
                                  self.s("A091RC1Q027SBEA"), gdp)
        interest_fy = self.s("FYOIGDA188S")
        debt = self.s("GFDEGDQ188S")

        groups = [
            ("赤字", self.add(
                Chart("deficit_usd", "联邦赤字：滚动 12 个月", "十亿美元", [Line("12 个月赤字", deficit12, "bar")],
                      note="正值为赤字。按月度财政报告（MTS）的收支差滚动加总。"),
                Chart("deficit_pct", "赤字率", "% GDP", [
                    Line("滚动 12 个月 / 名义 GDP", deficit_pct), Line("财年赤字率", annual_def, "bar")],
                    note="正值为赤字。财年截至 9 月。"),
            )),
            ("利息与债务", self.add(
                Chart("interest", "联邦利息支出占 GDP", "% GDP", [
                    Line("季度（年化）", interest_pct), Line("财年", interest_fy, "bar")]),
                Chart("debt", "联邦债务占 GDP", "% GDP", [Line("债务/GDP", debt)]),
            )),
        ]
        kpis = [
            Kpi("12 个月赤字", deficit12, "十亿美元", "{:,.0f}"),
            Kpi("赤字率（12 个月）", deficit_pct, "% GDP"),
            Kpi("财年赤字率", annual_def, "% GDP"),
            Kpi("利息支出占 GDP", interest_pct, "% GDP", "{:.2f}"),
            Kpi("债务占 GDP", debt, "% GDP"),
        ]
        comps = [
            Component("赤字率", deficit_pct, +1),
            Component("赤字率 12 个月变化", ts.diff(deficit_pct, 12, "M"), +1),
            Component("利息支出占 GDP", ts.to_monthly(interest_pct), +1, 200),
            Component("债务占 GDP", ts.to_monthly(debt), +1, 200),
        ]
        return groups, kpis, comps

    # =====================================================================
    # 货币政策
    def path_chart(self) -> Chart | None:
        effr = self.s("EFFR")
        latest = {}
        for row in self.effr_expect:
            key = row["series_id"]
            if key not in latest or row["date"] > latest[key]["date"]:
                latest[key] = row
        cats = ["当前", "下月", "年底", "明年底", "长期"]
        market: list[float | None] = [effr[-1][1] if effr else None, None, None, None, None]
        idx = {"effr_next": 1, "effr_year": 2, "effr_ny": 3}
        ref_year = None
        for key, i in idx.items():
            if key in latest:
                market[i] = float(latest[key]["value"])
                ref_year = int(latest[key]["date"][:4])
        dots: list[float | None] = [None] * 5
        dmap = {d.year: v for d, v in self.s("FEDTARMD")}
        ref_year = ref_year or self.asof.year
        dots[2] = dmap.get(ref_year)
        dots[3] = dmap.get(ref_year + 1)
        if self.s("FEDTARMDLR"):
            dots[4] = self.s("FEDTARMDLR")[-1][1]
        if dots[2] is not None and effr:
            dots[0] = effr[-1][1]
        lines = []
        if any(v is not None for v in market[1:]):
            lines.append(Line("市场隐含", market))  # type: ignore[arg-type]
        if any(v is not None for v in dots[2:]):
            lines.append(Line("点阵图中位数", dots, dash=True))  # type: ignore[arg-type]
        if not lines:
            return None
        asof_txt = max((r["date"] for r in latest.values()), default="")
        return Chart("effr_path", "EFFR 路径：市场隐含 vs 点阵图", "%", lines, kind="path", categories=cats,
                     note=f"市场隐含来自早晨笔记（Investing Fed Rate Monitor，{asof_txt}），"
                          "点阵图为最近一次 SEP 中位数。点阵图是区间中值，与 EFFR 通常相差几个基点。")

    def policy(self) -> tuple[list, list, list[Component]]:
        effr_w = self.w("EFFR")
        upper = self.w("DFEDTARU")
        lower = self.w("DFEDTARL")
        dgs2 = self.w("DGS2")
        effr_m = self.m("EFFR")
        core_yoy = ts.pct_change(self.m("PCEPILFE", "last"), 12, "M")
        real_policy = ts.combine(lambda a, b: a - b, effr_m, core_yoy)
        two_minus = ts.scale(ts.combine(lambda a, b: a - b, self.m("DGS2"), effr_m), 100)
        t10y2y = self.w("T10Y2Y")
        t10y3m = self.w("T10Y3M")
        dgs10 = self.w("DGS10")
        real10 = self.w("DFII10")

        exp_lines = []
        names = {"effr_next": "下月 EFFR", "effr_year": "年底 EFFR", "effr_ny": "明年底 EFFR"}
        for key, nm in names.items():
            pts = []
            for r in self.effr_expect:
                if r["series_id"] == key:
                    try:
                        pts.append((date.fromisoformat(r["date"]), float(r["value"])))
                    except ValueError:
                        continue
            exp_lines.append(Line(nm, ts.clean(pts)))

        path = self.path_chart()
        groups = [
            ("政策利率", self.add(
                *( [path] if path else [] ),
                Chart("effr_expect_hist", "市场隐含 EFFR 的变化", "%", exp_lines,
                      start=date(2026, 1, 1), note="早晨笔记从 2026-09 开始记录，历史会逐日累积。"),
                Chart("effr", "EFFR、目标区间与 2 年期国债", "%", [
                    Line("目标上限", upper, dash=True), Line("目标下限", lower, dash=True),
                    Line("EFFR", effr_w), Line("2 年期国债", dgs2)],
                    note="2 年期收益率反映市场对未来两年政策利率的平均预期。"),
                Chart("real_policy", "实际政策利率", "%", [Line("EFFR − 核心 PCE 同比", real_policy)],
                      note="正值越大，政策越紧。"),
                Chart("two_minus", "2 年期国债 − EFFR", "基点", [Line("2Y − EFFR", two_minus)],
                      note="为正说明市场预期加息，为负说明预期降息。"),
            )),
            ("收益率曲线", self.add(
                Chart("curve", "收益率曲线利差", "百分点", [
                    Line("10Y − 2Y", t10y2y), Line("10Y − 3M", t10y3m)],
                    note="倒挂（小于 0）历史上领先衰退，衰退往往在重新转正后到来。"),
                Chart("yields", "10 年期名义与实际利率", "%", [
                    Line("10 年期国债", dgs10), Line("10 年期 TIPS 实际利率", real10)]),
            )),
        ]
        kpis = [
            Kpi("EFFR", self.s("EFFR"), "%", "{:.2f}"),
            Kpi("目标区间上限", self.s("DFEDTARU"), "%", "{:.2f}"),
            Kpi("2 年期国债", self.s("DGS2"), "%", "{:.2f}"),
            Kpi("10 年期国债", self.s("DGS10"), "%", "{:.2f}"),
            Kpi("实际政策利率", real_policy, "%", "{:.2f}"),
            Kpi("10Y − 2Y", self.s("T10Y2Y"), "百分点", "{:.2f}"),
            Kpi("10Y − 3M", self.s("T10Y3M"), "百分点", "{:.2f}"),
        ]
        for key, nm in names.items():
            line = next(ln for ln in exp_lines if ln.name == nm)
            kpis.append(Kpi(nm + "（市场隐含）", line.data, "%", "{:.2f}"))
        comps = [
            Component("实际政策利率", real_policy, +1),
            Component("EFFR 12 个月变化", ts.diff(effr_m, 12, "M"), +1),
            Component("2Y − EFFR", two_minus, +1),
            Component("10 年期实际利率", self.m("DFII10"), +1),
            Component("10Y − 3M 利差", self.m("T10Y3M"), -1),
        ]
        return groups, kpis, comps

    # =====================================================================
    # 评分
    def score(self, comps: list[Component]) -> dict:
        """每项按 2000 年以来的均值和标准差做 z 分数，方向统一后取平均。"""
        months = []
        d = SCORE_START
        end = ts.month_start(self.asof)
        while d <= end:
            months.append(d)
            d = ts._shift_month(d, 1)
        items = []
        for c in comps:
            hist = [(dd, v) for dd, v in c.monthly if dd >= SCORE_START]
            if len(hist) < 24:
                continue
            mu, sd = ts.mean_std([v for _, v in hist])
            if sd == 0:
                continue
            items.append((c, hist, mu, sd))
        history = []
        latest_parts = []
        for mth in months:
            zs = []
            parts = []
            for c, hist, mu, sd in items:
                v = ts.asof(hist, mth, c.max_gap_days)
                if v is None:
                    continue
                z = max(-Z_CLIP, min(Z_CLIP, (v - mu) / sd)) * c.sign
                zs.append(z)
                parts.append({"name": c.name, "value": _r(v), "z": round(z, 2),
                              "sign": c.sign, "date": next(dd for dd, x in reversed(hist) if dd <= mth).isoformat()})
            if len(zs) >= max(2, len(items) // 2):
                history.append((mth, sum(zs) / len(zs)))
                latest_parts = parts
        cur = history[-1] if history else None
        ago = ts.asof(history, ts._shift_month(cur[0], -3)) if cur else None
        return {
            "score": round(cur[1], 2) if cur else None,
            "month": cur[0].isoformat() if cur else None,
            "score_3m_ago": round(ago, 2) if ago is not None else None,
            "history": [[d.isoformat(), round(v, 3)] for d, v in history],
            "components": latest_parts,
            "n_components": len(items),
        }

    # =====================================================================
    def build(self) -> dict:
        dims = [
            ("growth", "增长", self.growth, ("偏强", "中性", "偏弱"),
             "越高表示增长越强：GDP、就业、消费、订单、地产相对 2000 年以来的常态。"),
            ("inflation", "通胀", self.inflation, ("偏热", "温和", "偏冷"),
             "越高表示通胀压力越大：核心 PCE、核心 CPI、工资与通胀预期。"),
            ("liquidity", "流动性", self.liquidity, ("宽松", "中性", "偏紧"),
             "越高表示流动性越宽松：准备金、美联储资产、资金利率、金融状况与信用利差。"),
            ("fiscal", "财政", self.fiscal, ("扩张/压力大", "中性", "收缩/压力小"),
             "越高表示财政越扩张、利息与债务压力越大。"),
            ("policy", "货币政策", self.policy, ("偏紧", "中性", "偏松"),
             "越高表示货币政策越紧：实际政策利率、加息幅度、市场预期与曲线形态。"),
        ]
        out_dims, sections, kpis = [], [], {}
        for key, name, fn, labels, desc in dims:
            groups, kp, comps = fn()
            sc = self.score(comps)
            s = sc["score"]
            if s is None:
                label = "数据不足"
            elif s > LABEL_BAND:
                label = labels[0]
            elif s < -LABEL_BAND:
                label = labels[2]
            else:
                label = labels[1]
            out_dims.append({"key": key, "name": name, "label": label, "desc": desc, **sc})
            kpis[key] = [x for x in (k.to_json() for k in kp) if x]
            sections.append({"key": key, "name": name,
                             "groups": [{"name": g, "charts": ids} for g, ids in groups if ids]})
        return {
            "asof": self.asof.isoformat(),
            "score_method": f"每项按 {SCORE_START.year} 年以来的均值与标准差做 z 分数（截断在 ±{Z_CLIP:g}），"
                            f"方向统一后等权平均。高于 +{LABEL_BAND} 或低于 −{LABEL_BAND} 视为明显偏离常态。"
                            "刚发布的数据还没出来时，沿用最近一次读数（月频最多 4 个月、季频最多 6 个月）。",
            "dimensions": out_dims,
            "kpis": kpis,
            "sections": sections,
            "charts": self.charts,
        }


def build_dashboard(raw: dict[str, Series], effr_expect: list[dict] | None = None,
                    asof: date | None = None) -> dict:
    return MacroBuilder(raw, effr_expect, asof).build()

