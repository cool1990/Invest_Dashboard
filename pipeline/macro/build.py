"""把原始 FRED 序列整理成页面要用的 data/macro/dashboard.json。

页面分四层，JSON 也按这四层组织：
- verdict：整体环境（增长 × 通胀定象限，货币路径与流动性作条件）+ 分歧与风险。
- dimensions：五个维度各自按经济锚点判断的状态、一句话、证据、即将发布。
  不用 z 分数：和「2000 年以来平均」比没有经济含义，等权汇总又会把分歧抵消掉。
- signals：每维 2–6 个核心指标：最新值、较上期、较一年前、下一次发布、规则解读。
- releases：即将发布、最近发布（实际 vs 预期）、克利夫兰联储通胀 Nowcast。
- sections + charts：明细图表，core=True 的默认展开，其余折叠。

缺数据的序列会被跳过；某张图一条序列都没有，就不输出这张图。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from .. import series as ts
from ..series import Series
from . import consensus as cons
from . import interpret as I
from .scenario import Scenario

# 日历指标 → 克利夫兰联储 Nowcast 口径（都是同比）
NOWCAST_OF = {"core_pce_mom": "核心 PCE", "core_pce_yoy": "核心 PCE", "pce_mom": "PCE", "pce_yoy": "PCE",
              "cpi_mom": "CPI", "cpi_yoy": "CPI", "core_cpi_mom": "核心 CPI", "core_cpi_yoy": "核心 CPI"}

DISPLAY_START = date(1990, 1, 1)
PCTILE_START = date(2000, 1, 1)  # 历史分位的起点
RECENT_DAYS = 30
UPCOMING_DAYS = 10


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
# 图表、核心指标、评分项


@dataclass
class Line:
    name: str
    data: Series
    kind: str = "line"  # line | bar
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
    core: bool = False

    def to_json(self) -> dict | None:
        lines = [ln for ln in self.lines if ln.data]
        if not lines:
            return None
        out = {"id": self.id, "title": self.title, "unit": self.unit, "kind": self.kind,
               "stacked": self.stacked, "note": self.note, "core": self.core, "series": []}
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


# 各频率「较上期 / 较一年前」各往回数几个观测。周频和日频单期太吵，「上期」用 4 周前 / 约 1 个月前。
FREQ = {
    "M": (1, "上月", 12),
    "Q": (1, "上季度", 4),
    "W": (4, "4 周前", 52),
    "D": (21, "约 1 个月前", 252),
}


@dataclass
class Signal:
    """核心指标。rule 由 interpret.py 提供。

    cmp：算「较上期 / 较一年前」用的序列，默认就是 data；噪音大的月度流量（非农、零售、环比通胀）
    用 3 个月均值，并在 cmp_note 里注明，解读也用同一个数，箭头和句子不会互相矛盾。
    keys：在经济日历里对应的指标（找「下一次发布」和「上次意外」）。
    nowcast：克利夫兰联储 Nowcast 的口径名（只有同比口径）。
    """

    id: str
    name: str
    data: Series
    unit: str
    fmt: str
    rule: Callable[[I.Ctx], I.Result]
    chart: str | None = None
    freq: str = "M"
    cmp: Series | None = None
    cmp_note: str = ""
    keys: tuple[str, ...] = ()
    nowcast: str | None = None
    extra: dict = field(default_factory=dict)


def signed(fmt: str) -> str:
    return fmt if "+" in fmt else fmt.replace("{:", "{:+", 1)


# ---------------------------------------------------------------------------


class MacroBuilder:
    def __init__(self, raw: dict[str, Series], effr_expect: list[dict] | None = None,
                 asof: date | None = None, events: list[dict] | None = None,
                 nowcast: list[dict] | None = None, now: datetime | None = None):
        self.raw = raw
        self.effr_expect = effr_expect or []
        self.asof = asof or date.today()
        self.events = events or []
        self.nowcast = nowcast or []
        self.now = now or datetime.combine(self.asof, datetime.min.time(), tzinfo=timezone.utc)
        self.charts: dict[str, dict] = {}
        self.actuals: dict[str, Series] = {}  # 日历指标 key → FRED 实际值（与预期同单位）
        # 情景试算要用：各维度状态函数的输入、状态本身、以及几条原始序列
        self.inputs: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.ctx: dict[str, Series] = {}

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

    def yoy_m(self, sid: str) -> Series:
        return ts.pct_change(self.m(sid, "last"), 12, "M")

    # =====================================================================
    # 增长
    def growth(self):
        gdp = self.s("GDPC1")
        gdp_qoq = ts.pct_change(gdp, 1, "Q", annualize=4)
        gdp_yoy = ts.pct_change(gdp, 4, "Q")
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
        mv = self.m("RSMVPD", "last")
        ctrl = ts.combine(lambda a, b, c, d, e: a - b - c - d - e, rs, mv,
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

        dgo = self.m("DGORDER", "last")
        dgo_yoy = ts.pct_change(ts.rolling(dgo, 3), 12, "M")
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

        self.actuals.update({
            "gdp_qoq": gdp_qoq, "nfp": nfp, "unrate": unrate, "ahe_mom": ts.pct_change(ahe, 1, "M"),
            "retail_mom": rs_mom,
            "retail_exauto_mom": ts.pct_change(ts.combine(lambda a, b: a - b, rs, mv), 1, "M"),
            "jolts": jol, "icsa": ts.scale(self.s("ICSA"), 1e-3), "dgo_mom": ts.pct_change(dgo, 1, "M"),
            "permit": permit, "houst": houst, "hsn": hsn, "empire": empire, "philly": philly,
        })

        groups = [
            ("产出", self.add(
                Chart("gdp_q", "实际 GDP：季环比年化与同比", "%", [
                    Line("季环比年化", gdp_qoq, "bar"), Line("同比", gdp_yoy)],
                    note="季度数据，来源 BEA。柱为季环比年化，线为同比。", core=True),
                Chart("gdpnow", "GDPNow：本季度实时预测", "%", [Line("GDPNow", gdpnow)],
                      note="亚特兰大联储对当季实际 GDP 环比年化的模型预测。", start=date(2014, 1, 1)),
                Chart("gdp_a", "实际 GDP：年度增速", "%", [Line("年度增速", gdp_annual, "bar")],
                      note="按全年四个季度均值计算。"),
                Chart("indpro", "工业产出与实际可支配收入同比", "%", [
                    Line("工业产出同比", indpro_yoy), Line("实际可支配收入同比", rdpi_yoy)]),
            )),
            ("就业", self.add(
                Chart("nfp", "非农新增就业", "千人", [
                    Line("当月新增", nfp, "bar"), Line("3 个月均值", nfp3)],
                    note="2020 年疫情期间数值极端，建议看近 5 年。", core=True),
                Chart("unrate", "失业率", "%", [
                    Line("U-3 失业率", unrate), Line("U-6 失业率", u6)], core=True),
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
                    note="控制组 = 零售总额 − 汽车 − 加油站 − 建材 − 餐饮，进入 GDP 的消费核算。", core=True),
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

        signals = [
            Signal("gdpnow", "GDPNow 本季度预测", gdpnow, "%", "{:.1f}", I.gdpnow, chart="gdpnow", freq="Q"),
            Signal("gdp_qoq", "实际 GDP 季环比年化", gdp_qoq, "%", "{:.1f}", I.gdp_q, chart="gdp_q", freq="Q",
                   keys=("gdp_qoq",), extra={"gdpnow": gdpnow[-1][1] if gdpnow else None}),
            Signal("nfp", "非农新增就业", nfp, "千人", "{:,.0f}", I.nfp3, chart="nfp", keys=("nfp",),
                   cmp=nfp3, cmp_note="按 3 个月均值",
                   extra={"avg3": nfp3[-1][1] if nfp3 else None,
                          "avg3_chg": (nfp3[-1][1] - nfp3[-2][1]) if len(nfp3) >= 2 else None}),
            Signal("unrate", "失业率", unrate, "%", "{:.1f}", I.unrate, chart="unrate", keys=("unrate",),
                   extra={"sahm": sahm[-1][1] if sahm else None,
                          "chg12": ts.diff(unrate, 12, "M")[-1][1] if len(unrate) > 12 else None}),
            Signal("retail_ctrl", "零售控制组环比", ctrl_mom, "%", "{:+.2f}", I.retail_ctrl, chart="retail_yoy",
                   cmp=ts.rolling(ctrl_mom, 3), cmp_note="按 3 个月均值", keys=("retail_mom",),
                   extra={"avg3": ts.rolling(ctrl_mom, 3)[-1][1] if len(ctrl_mom) >= 3 else None}),
        ]
        self.inputs["growth"] = {
            "gdpnow": gdpnow[-1][1] if gdpnow else None, "gdp_q": gdp_qoq[-1][1] if gdp_qoq else None,
            "nfp3": nfp3[-1][1] if nfp3 else None, "nfp3_ago": nfp3[-2][1] if len(nfp3) >= 2 else None,
            "unrate_chg12": ts.diff(unrate, 12, "M")[-1][1] if len(unrate) > 12 else None,
            "sahm": sahm[-1][1] if sahm else None,
        }
        self.ctx.update({"nfp": nfp, "unrate": unrate, "gdp_qoq": gdp_qoq})
        state = I.growth_state(self.inputs["growth"])
        return groups, signals, state

    # =====================================================================
    # 通胀：PCE 按贡献拆分
    def pce_breakdown(self) -> dict[str, list[Line]]:
        """PCE 与核心 PCE 按贡献拆分，环比与同比各一套。

        某项对总体的贡献 ≈ 基期（环比用上月、同比用 12 个月前）的名义支出份额 × 该项价格变化。
        FRED 上月度数据不全，权重用最近可得的低频数据近似：
        - 住房名义 = 核心名义 × 上一年住房占核心的比重（DHSGRC1A027NBEA 为年度）
        - 核心商品名义 = 商品 − 食品 − 能源 × 能源商品占能源的比重（季度，沿用最近一季）
        - 超级核心名义 = 核心 − 核心商品 − 住房
        价格：核心 PCEPILFE、核心除住房 IA001176M、超级核心 IA001260M 都是月度。
        住房贡献 = 核心贡献 − 核心除住房贡献；核心商品贡献 = 核心除住房贡献 − 超级核心贡献。
        链式加总不严格可加，误差一般在 0.01 个百分点量级。
        """
        total_n = dict(self.m("PCE", "last"))
        core_n = dict(self.m("DPCCRC1M027SBEA", "last"))
        food_n = dict(self.m("DFXARC1M027SBEA", "last"))
        energy_n = dict(self.m("DNRGRC1M027SBEA", "last"))
        goods_n = dict(self.m("DGDSRC1", "last"))
        eg_share = ts.combine(lambda a, b: a / b if b else None, self.s("DGOERC1Q027SBEA"), self.s("DNRGRC1Q027SBEA"))

        core_year: dict[int, list[float]] = {}
        for d, v in core_n.items():
            core_year.setdefault(d.year, []).append(v)
        housing_w = {d.year: v / (sum(core_year[d.year]) / 12)
                     for d, v in self.s("DHSGRC1A027NBEA") if len(core_year.get(d.year, [])) == 12}

        def hw(d: date) -> float | None:
            for y in range(d.year - 1, d.year - 4, -1):
                if y in housing_w:
                    return housing_w[y]
            return None

        # 各块的月度名义支出
        housing_n, exh_n, super_n = {}, {}, {}
        for d, c in core_n.items():
            w = hw(d)
            if w is None:
                continue
            housing_n[d] = c * w
            exh_n[d] = c * (1 - w)
            eg = ts.asof(eg_share, d, 200)
            if eg is not None and d in goods_n and d in food_n and d in energy_n:
                core_goods = goods_n[d] - food_n[d] - energy_n[d] * eg
                super_n[d] = c - core_goods - housing_n[d]

        def contrib(num: dict, den: dict, price_id: str, lag: int) -> Series:
            out = []
            for d, chg in ts.pct_change(self.m(price_id, "last"), lag, "M"):
                base = ts._shift_month(d, -lag)
                if base in num and den.get(base):
                    out.append((d, num[base] / den[base] * chg))
            return out

        def lines(den: dict, lag: int, with_food_energy: bool) -> list[Line]:
            core_c = contrib(core_n, den, "PCEPILFE", lag)
            exh_c = contrib(exh_n, den, "IA001176M", lag)
            sc_c = contrib(super_n, den, "IA001260M", lag)
            out = []
            if with_food_energy:
                out += [Line("食品", contrib(food_n, den, "DFXARG3M086SBEA", lag), "bar"),
                        Line("能源", contrib(energy_n, den, "DNRGRG3M086SBEA", lag), "bar")]
            if exh_c:
                housing_c = ts.combine(lambda a, b: a - b, core_c, exh_c)
                if sc_c:
                    goods_c = ts.combine(lambda a, b: a - b, exh_c, sc_c)
                    out += [Line("核心商品", goods_c, "bar"), Line("住房", housing_c, "bar"),
                            Line("超级核心", sc_c, "bar")]
                else:
                    out += [Line("住房", housing_c, "bar"), Line("核心除住房", exh_c, "bar")]
            else:
                out.append(Line("核心", core_c, "bar"))
            return out

        return {"total_mom": lines(total_n, 1, True), "total_yoy": lines(total_n, 12, True),
                "core_mom": lines(core_n, 1, False), "core_yoy": lines(core_n, 12, False)}

    def inflation(self):
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
        br = self.pce_breakdown()
        supercore = self.m("IA001260M", "last")
        supercore_yoy = ts.pct_change(supercore, 12, "M")
        supercore_3m = ts.pct_change(supercore, 3, "M", annualize=12)
        goods_yoy = self.yoy_m("DGDSRG3M086SBEA")
        services_yoy = self.yoy_m("DSERRG3M086SBEA")
        fwd = self.w("T5YIFR")
        be10 = self.w("T10YIE")
        mich = self.m("MICH", "last")

        self.actuals.update({
            "pce_mom": pce_mom, "pce_yoy": pce_yoy, "core_pce_mom": core_mom, "core_pce_yoy": core_yoy,
            "cpi_mom": ts.pct_change(cpi, 1, "M"), "cpi_yoy": cpi_yoy, "core_cpi_mom": ccpi_mom,
            "core_cpi_yoy": ccpi_yoy, "mich": mich,
        })
        weight_note = ("权重用最近可得的低频数据近似：住房比重用上一年年度数据，能源商品比重用最近一季"
                       "（FRED 没有月度住房支出和汽油支出）。")

        groups = [
            ("核心 PCE", self.add(
                Chart("core_pce", "核心 PCE：环比与同比", "%", [
                    Line("环比", core_mom, "bar"), Line("同比", core_yoy)],
                    note="美联储 2% 目标针对 PCE 同比；核心环比约 0.17% 对应年化 2%。", core=True),
                Chart("core_contrib_mom", "核心 PCE 环比：按贡献拆分", "百分点",
                      br["core_mom"] + [Line("核心 PCE 环比", core_mom)], stacked="bar", start=date(2015, 1, 1),
                      note="对核心 PCE 的贡献 = 上月占核心的名义份额 × 分项价格环比。超级核心 = 服务除能源、住房。"
                           + weight_note, core=True),
                Chart("core_contrib_yoy", "核心 PCE 同比：按贡献拆分", "百分点",
                      br["core_yoy"] + [Line("核心 PCE 同比", core_yoy)], stacked="bar", start=date(2005, 1, 1),
                      note="对核心 PCE 的贡献 = 12 个月前占核心的名义份额 × 分项价格同比。", core=True),
                Chart("core_pce_ann", "核心 PCE 年化动能", "%", [
                    Line("3 个月年化", core_3m), Line("6 个月年化", core_6m), Line("同比", core_yoy, dash=True)],
                    core=True),
                Chart("supercore", "超级核心与商品、服务价格同比", "%", [
                    Line("超级核心（服务除能源、住房）", supercore_yoy), Line("超级核心 3 个月年化", supercore_3m, dash=True),
                    Line("PCE 商品", goods_yoy), Line("PCE 服务", services_yoy)],
                    note="超级核心是美联储最关注的与工资相关的服务通胀。"),
            )),
            ("整体 PCE", self.add(
                Chart("pce_yoy", "PCE 与核心 PCE 同比", "%", [
                    Line("PCE", pce_yoy), Line("核心 PCE", core_yoy)]),
                Chart("pce_mom", "PCE 与核心 PCE 环比", "%", [
                    Line("PCE", pce_mom, "bar"), Line("核心 PCE", core_mom, "bar")]),
                Chart("pce_contrib_mom", "PCE 环比：按贡献拆分", "百分点",
                      br["total_mom"] + [Line("PCE 环比", pce_mom)], stacked="bar", start=date(2015, 1, 1),
                      note="贡献 = 上月名义支出份额 × 分项价格环比。能源含汽油与电、燃气。" + weight_note),
                Chart("pce_contrib_yoy", "PCE 同比：按贡献拆分", "百分点",
                      br["total_yoy"] + [Line("PCE 同比", pce_yoy)], stacked="bar", start=date(2005, 1, 1),
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
        signals = [
            Signal("core_pce_mom", "核心 PCE 环比", core_mom, "%", "{:.2f}", I.core_mom, chart="core_pce",
                   keys=("core_pce_mom",), cmp=ts.rolling(core_mom, 3), cmp_note="按 3 个月均值",
                   extra={"avg3": ts.rolling(core_mom, 3)[-1][1] if len(core_mom) >= 3 else None}),
            Signal("core_pce_yoy", "核心 PCE 同比", core_yoy, "%", "{:.2f}", I.core_yoy, chart="core_contrib_yoy",
                   keys=("core_pce_yoy",), nowcast="核心 PCE"),
            Signal("core_pce_3m", "核心 PCE 3 个月年化", core_3m, "%", "{:.2f}", I.core_3m, chart="core_pce_ann",
                   extra={"yoy": core_yoy[-1][1] if core_yoy else None}),
            Signal("supercore", "超级核心 PCE 同比", supercore_yoy, "%", "{:.2f}", I.supercore, chart="supercore",
                   extra={"m3": supercore_3m[-1][1] if supercore_3m else None}),
            Signal("core_cpi_mom", "核心 CPI 环比", ccpi_mom, "%", "{:.2f}", I.core_cpi_mom, chart="cpi_mom",
                   keys=("core_cpi_mom",), cmp=ts.rolling(ccpi_mom, 3), cmp_note="按 3 个月均值",
                   nowcast="核心 CPI", extra={"avg3": ts.rolling(ccpi_mom, 3)[-1][1] if len(ccpi_mom) >= 3 else None}),
            Signal("fwd5y5y", "5y5y 远期通胀预期", fwd, "%", "{:.2f}", I.fwd_infl, chart="expect", freq="W"),
        ]
        self.inputs["inflation"] = {
            "core_yoy": core_yoy[-1][1] if core_yoy else None, "core_3m": core_3m[-1][1] if core_3m else None,
            "supercore": supercore_yoy[-1][1] if supercore_yoy else None, "fwd": fwd[-1][1] if fwd else None,
            "nowcast": self.nowcast_after("核心 PCE", core_yoy[-1][0] if core_yoy else None),
        }
        self.ctx.update({"core_idx": core, "ccpi_idx": ccpi})
        state = I.inflation_state(self.inputs["inflation"])
        return groups, signals, state

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

    def liquidity(self):
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

        groups = [
            ("准备金拆分", self.add(
                Chart("reserves_stack", "美联储负债结构：准备金从哪里来", "十亿美元", [
                    Line("准备金", fr["reserves"]), Line("ON RRP", fr["rrp"]), Line("TGA", fr["tga"]),
                    Line("流通中货币", fr["currency"]), Line("其他负债与资本", fr["other"])],
                    stacked="area", start=date(2008, 1, 1), core=True,
                    note="堆叠之和 = 美联储总资产。准备金 = 总资产 − ON RRP − TGA − 流通中货币 − 其他。"),
                Chart("reserves_chg", f"准备金 {n} 周变化：按来源拆分", "十亿美元", [
                    Line("总资产", chg["assets"], "bar"),
                    Line("ON RRP（取负）", neg(chg["rrp"]), "bar"),
                    Line("TGA（取负）", neg(chg["tga"]), "bar"),
                    Line("流通中货币（取负）", neg(chg["currency"]), "bar"),
                    Line("其他（取负）", neg(chg["other"]), "bar"),
                    Line("准备金变化", chg["reserves"])],
                    stacked="bar", start=date(2019, 1, 1), core=True,
                    note="柱子之和 = 准备金变化。正值表示该项在增加准备金，例如 TGA 下降、RRP 下降。"),
                Chart("reserves_lv", "准备金余额", "十亿美元", [Line("准备金（周三）", fr["reserves"])],
                      start=date(2008, 1, 1)),
            )),
            ("资金利率与金融状况", self.add(
                Chart("sofr_iorb", "SOFR − IORB 与 EFFR − IORB", "基点", [
                    Line("SOFR − IORB", ts.to_weekly(sofr_iorb)),
                    Line("EFFR − IORB", ts.to_weekly(effr_iorb))],
                    start=date(2018, 4, 1), core=True,
                    note="周内最后一个交易日。SOFR 持续高于 IORB 说明回购市场资金偏紧、准备金接近不充裕。"
                         "2021-07 前用 IOER。"),
                Chart("nfci", "芝加哥联储金融状况指数 NFCI", "指数", [Line("NFCI", nfci)],
                      note="0 为历史平均，正值偏紧、负值偏松。", core=True),
                Chart("hy", "高收益债利差", "%", [Line("HY OAS", hy)],
                      note="FRED 上的 ICE 数据只保留近几年。"),
                Chart("usd", "美元广义指数", "指数", [Line("美元指数", usd)], start=date(2006, 1, 1)),
            )),
        ]
        gdp = self.s("GDP")
        res = fr["reserves"]
        ratio = res[-1][1] / gdp[-1][1] * 100 if res and gdp else None
        signals = [
            Signal("reserves", "准备金", res, "十亿美元", "{:,.0f}", I.reserves, chart="reserves_stack",
                   freq="W", extra={"gdp_ratio": ratio, "chg4": chg["reserves"][-1][1] if chg["reserves"] else None}),
            Signal("sofr_iorb", "SOFR − IORB", sofr_iorb, "基点", "{:+.0f}", I.sofr_iorb, chart="sofr_iorb",
                   freq="D", extra={"avg4": sum(v for _, v in sofr_iorb[-20:]) / len(sofr_iorb[-20:])
                                      if sofr_iorb else None}),
            Signal("nfci", "金融状况 NFCI", nfci, "指数", "{:.2f}", I.nfci, chart="nfci", freq="W",
                   extra={"chg13": nfci[-1][1] - nfci[-14][1] if len(nfci) > 13 else None}),
            Signal("hy", "高收益债利差", self.s("BAMLH0A0HYM2"), "%", "{:.2f}", I.hy, chart="hy", freq="D"),
        ]
        hy_d = self.s("BAMLH0A0HYM2")
        state = I.liquidity_state({
            "sofr_iorb": sofr_iorb[-1][1] if sofr_iorb else None, "reserves_ratio": ratio,
            "nfci": nfci[-1][1] if nfci else None, "hy": hy_d[-1][1] if hy_d else None,
        })
        return groups, signals, state

    # =====================================================================
    # 财政
    def fiscal(self):
        mts = self.m("MTSDS133FMS", "last")
        deficit12 = ts.scale(ts.rolling(mts, 12, "sum"), -1e-3)  # 正数 = 赤字，十亿美元
        gdp = self.s("GDP")
        deficit_pct = [(d, v / g * 100) for d, v in deficit12 if (g := ts.asof(gdp, d, 200))]
        # 财年数据 FRED 记在当年 1 月 1 日，挪到财年结束的 9 月 30 日，和月度线对齐
        fy_end = lambda s: [(date(d.year, 9, 30), v) for d, v in s]  # noqa: E731
        annual_def = fy_end(ts.scale(self.s("FYFSGDA188S"), -1))
        interest_pct = ts.combine(lambda a, b: a / b * 100 if b else None, self.s("A091RC1Q027SBEA"), gdp)
        interest_fy = fy_end(self.s("FYOIGDA188S"))
        debt = self.s("GFDEGDQ188S")
        deficit_chg = ts.diff(deficit_pct, 12, "M")

        groups = [
            ("赤字", self.add(
                Chart("deficit_pct", "赤字率", "% GDP", [
                    Line("滚动 12 个月 / 名义 GDP", deficit_pct), Line("财年赤字率", annual_def, "bar")],
                    note="正值为赤字。柱子是财年数据，画在财年结束的 9 月底。", core=True),
                Chart("deficit_usd", "联邦赤字：滚动 12 个月", "十亿美元", [Line("12 个月赤字", deficit12, "bar")],
                      note="正值为赤字。按月度财政报告（MTS）的收支差滚动加总。"),
            )),
            ("利息与债务", self.add(
                Chart("interest", "联邦利息支出占 GDP", "% GDP", [
                    Line("季度（年化）", interest_pct), Line("财年", interest_fy, "bar")], core=True),
                Chart("debt", "联邦债务占 GDP", "% GDP", [Line("债务/GDP", debt)]),
            )),
        ]
        signals = [
            Signal("deficit", "赤字率（滚动 12 个月）", deficit_pct, "% GDP", "{:.1f}", I.deficit, chart="deficit_pct",
                   extra={"chg12": deficit_chg[-1][1] if deficit_chg else None}),
            Signal("interest", "利息支出占 GDP", interest_pct, "% GDP", "{:.2f}", I.interest, chart="interest",
                   freq="Q"),
        ]
        state = I.fiscal_state({
            "deficit": deficit_pct[-1][1] if deficit_pct else None,
            "deficit_chg12": deficit_chg[-1][1] if deficit_chg else None,
            "interest": interest_pct[-1][1] if interest_pct else None, "debt": debt[-1][1] if debt else None,
        })
        return groups, signals, state

    # =====================================================================
    # 货币政策
    def latest_expect(self) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for row in self.effr_expect:
            key = row["series_id"]
            if key not in latest or row["date"] > latest[key]["date"]:
                latest[key] = row
        return latest

    def path_values(self) -> dict:
        effr = self.s("EFFR")
        latest = self.latest_expect()
        get = lambda k: float(latest[k]["value"]) if k in latest else None  # noqa: E731
        ref_year = int(max(r["date"] for r in latest.values())[:4]) if latest else self.asof.year
        dmap = {d.year: v for d, v in self.s("FEDTARMD")}
        lr = self.s("FEDTARMDLR")
        return {"current": effr[-1][1] if effr else None, "next_month": get("effr_next"),
                "year_end": get("effr_year"), "next_year": get("effr_ny"),
                "dot_year": dmap.get(ref_year), "dot_next": dmap.get(ref_year + 1),
                "dot_long": lr[-1][1] if lr else None,
                "asof": max((r["date"] for r in latest.values()), default="")}

    def path_chart(self, pv: dict) -> Chart | None:
        cats = ["当前", "下月", "年底", "明年底", "长期"]
        market = [pv["current"], pv["next_month"], pv["year_end"], pv["next_year"], None]
        dots = [pv["current"] if pv["dot_year"] is not None else None, None,
                pv["dot_year"], pv["dot_next"], pv["dot_long"]]
        lines = []
        if any(v is not None for v in market[1:]):
            lines.append(Line("市场隐含", market))  # type: ignore[arg-type]
        if any(v is not None for v in dots[2:]):
            lines.append(Line("点阵图中位数", dots, dash=True))  # type: ignore[arg-type]
        if not lines:
            return None
        return Chart("effr_path", "EFFR 路径：市场隐含 vs 点阵图", "%", lines, kind="path", categories=cats, core=True,
                     note=f"市场隐含来自早晨笔记（Investing Fed Rate Monitor，{pv['asof']}），"
                          "点阵图为最近一次 SEP 中位数。点阵图是区间中值，与 EFFR 通常相差几个基点。")

    def policy(self):
        effr_w = self.w("EFFR")
        upper = self.w("DFEDTARU")
        lower = self.w("DFEDTARL")
        dgs2 = self.w("DGS2")
        effr_m = self.m("EFFR")
        core_yoy = ts.pct_change(self.m("PCEPILFE", "last"), 12, "M")
        real_policy = ts.combine(lambda a, b: a - b, effr_m, core_yoy)
        two_minus = ts.scale(ts.combine(lambda a, b: a - b, self.m("DGS2"), effr_m), 100)
        t10y2y = self.w("T10Y2Y")
        t10y3m_d = self.s("T10Y3M")
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

        pv = self.path_values()
        path = self.path_chart(pv)
        groups = [
            ("政策利率", self.add(
                *([path] if path else []),
                Chart("effr", "EFFR、目标区间与 2 年期国债", "%", [
                    Line("目标上限", upper, dash=True), Line("目标下限", lower, dash=True),
                    Line("EFFR", effr_w), Line("2 年期国债", dgs2)],
                    note="2 年期收益率反映市场对未来两年政策利率的平均预期。", core=True),
                Chart("real_policy", "实际政策利率", "%", [Line("EFFR − 核心 PCE 同比", real_policy)],
                      note="正值越大，政策越紧。中性实际利率一般估计在 0.5–1%。", core=True),
                Chart("effr_expect_hist", "市场隐含 EFFR 的变化", "%", exp_lines,
                      start=date(2026, 1, 1), note="早晨笔记从 2026-09 开始记录，历史会逐日累积。"),
                Chart("two_minus", "2 年期国债 − EFFR", "基点", [Line("2Y − EFFR", two_minus)],
                      note="为正说明市场预期加息，为负说明预期降息。"),
            )),
            ("收益率曲线", self.add(
                Chart("curve", "收益率曲线利差", "百分点", [
                    Line("10Y − 2Y", t10y2y), Line("10Y − 3M", t10y3m)],
                    note="倒挂（小于 0）历史上领先衰退，衰退往往在重新转正后到来。", core=True),
                Chart("yields", "10 年期名义与实际利率", "%", [
                    Line("10 年期国债", dgs10), Line("10 年期 TIPS 实际利率", real10)]),
            )),
        ]
        ye = exp_lines[1].data
        # 最近一次倒挂距今多少个交易日（只看近一年）
        inv_ago = None
        recent = t10y3m_d[-252:]
        for k in range(len(recent) - 1, -1, -1):
            if recent[k][1] < 0:
                inv_ago = len(recent) - 1 - k
                break
        signals = [
            Signal("real_policy", "实际政策利率", real_policy, "%", "{:.2f}", I.real_policy, chart="real_policy"),
            Signal("effr_path", "市场隐含年底 EFFR", ye, "%", "{:.2f}", I.effr_path, chart="effr_path",
                   freq="D", extra=pv),
            Signal("curve", "10Y − 3M 利差", t10y3m_d, "百分点", "{:.2f}", I.curve, chart="curve", freq="D",
                   extra={"inv_days_ago": inv_ago}),
        ]
        state = I.policy_state({**pv, "real_policy": real_policy[-1][1] if real_policy else None})
        return groups, signals, state

    # =====================================================================
    # 克利夫兰联储 Nowcast
    @staticmethod
    def _period(text: str) -> date | None:
        try:
            y, m = text.split("-")[:2]
            return date(int(y), int(m), 1)
        except (ValueError, AttributeError):
            return None

    def nowcast_after(self, measure: str, latest: date | None) -> list[tuple[str, float]]:
        """比最新官方数据更新的各期 Nowcast（同比），按期间排序。"""
        return [(f"{p.month} 月", v) for p, v in self._nowcast_rows(measure) if not latest or p > latest]

    # =====================================================================
    # 预期与意外
    def _find(self, key: str, ref: date) -> float | None:
        s = self.actuals.get(key) or []
        return dict(s).get(ref)

    @staticmethod
    def _decimals(text: str) -> int:
        m = text.strip().rstrip("%KMBkmb").split(".")
        return len(m[1]) if len(m) > 1 else 0

    def _ref_text(self, spec: cons.EventSpec, ref: date) -> str:
        kind = spec.ref[0]
        if kind == "Q":
            return f"{ref.year}Q{(ref.month - 1) // 3 + 1}"
        if kind == "M":
            return f"{ref.year}-{ref.month:02d}"
        return f"截至 {ref.isoformat()} 当周"

    def judge(self, spec: cons.EventSpec, actual: float, forecast: float, decimals: int) -> tuple[float, str, str]:
        """返回 (意外, 文字, 方向 pos/neg/flat)。pos 表示对经济偏强或通胀偏热。"""
        a = round(actual, decimals)
        diff = round(a - forecast, decimals)
        if diff == 0:
            return 0.0, "符合预期", "flat"
        higher = diff > 0
        if spec.up == "强":
            return diff, "好于预期" if higher else "不及预期", "pos" if higher else "neg"
        if spec.up == "弱":  # 失业率、初请：高于预期是坏消息
            return diff, "差于预期" if higher else "好于预期", "neg" if higher else "pos"
        return diff, "高于预期（偏热）" if higher else "低于预期（偏冷）", "pos" if higher else "neg"

    def releases(self) -> dict:
        now = self.now
        recent, upcoming = [], []
        for e in self.events:
            try:
                at = datetime.fromisoformat(e["release_at"])
            except (KeyError, ValueError):
                continue
            title = e.get("title", "")
            spec = cons.EVENTS.get(title)
            name = spec.name if spec else cons.WATCH_ONLY.get(title)
            f_text, p_text = e.get("forecast", ""), e.get("previous", "")
            row = {"title": name or title, "title_en": title, "release_at": e["release_at"],
                   "bj": cons.to_beijing(e["release_at"]), "impact": e.get("impact", ""),
                   "forecast_text": f_text, "previous_text": p_text, "dim": cons.dim_of(title),
                   "key": spec.key if spec else None}
            if at > now:
                if at <= now + timedelta(days=UPCOMING_DAYS) and (spec or name or e.get("impact") == "High"):
                    if spec:
                        ref = cons.ref_period(spec, at.date())
                        row["ref"] = self._ref_text(spec, ref)
                        sc = self.scen.scenarios(spec.key, ref, cons.parse_value(f_text))
                        if sc:
                            row["scenario"] = sc
                        measure = NOWCAST_OF.get(spec.key)
                        if measure:
                            nc = dict((p, v) for p, v in self._nowcast_rows(measure))
                            if ref in nc:
                                row["nowcast_text"] = f"{measure} 同比 Nowcast {nc[ref]:.2f}%"
                    upcoming.append(row)
                continue
            if not spec or at < now - timedelta(days=RECENT_DAYS):
                continue
            ref = cons.ref_period(spec, at.date())
            row["ref"] = self._ref_text(spec, ref)
            row["unit"] = spec.unit
            actual = self._find(spec.key, ref)
            forecast = cons.parse_value(f_text)
            if actual is None:
                row["status"] = "等待 FRED 更新"
            else:
                dec = self._decimals(f_text) if f_text else 1
                row["actual_text"] = f"{round(actual, dec):,.{dec}f}"
                if forecast is not None:
                    diff, verdict, direction = self.judge(spec, actual, forecast, dec)
                    row.update({"surprise": diff, "surprise_text": f"{diff:+,.{dec}f}", "verdict": verdict,
                                "dir": direction})
                imp = self.scen.impact(spec.key, ref, actual)
                if imp:
                    row["impact"] = imp
            row["market"] = self.market_reaction(at.date())
            recent.append(row)
        recent.sort(key=lambda r: r["release_at"], reverse=True)
        upcoming.sort(key=lambda r: r["release_at"])
        # 克利夫兰联储：每个口径取最近两期
        nc = {}
        for r in self.nowcast:
            nc.setdefault(r["measure"], []).append(r)
        nowcast = []
        for measure in ("CPI", "核心 CPI", "PCE", "核心 PCE"):
            for r in nc.get(measure, [])[-2:]:
                nowcast.append({"measure": measure, "period": r["period"], "nowcast": r.get("nowcast", ""),
                                "actual": r.get("actual", "")})
        return {"recent": recent, "upcoming": upcoming, "nowcast": nowcast}

    def market_reaction(self, day: date) -> str:
        """发布当天 2 年期国债的日变化，以及早晨笔记里年底 EFFR 隐含值在发布前后的变化。"""
        bits = []
        y2 = self.s("DGS2")
        on = [v for d, v in y2 if d == day]
        before = [v for d, v in y2 if d < day]
        if on and before:
            bits.append(f"2Y {(on[0] - before[-1]) * 100:+.0f}bp")
        ye = sorted((r["date"], float(r["value"])) for r in self.effr_expect if r.get("series_id") == "effr_year")
        pre = [v for d, v in ye if d <= day.isoformat()]
        post = [v for d, v in ye if d > day.isoformat()]
        if pre and post:
            bits.append(f"年底 EFFR 隐含 {(post[0] - pre[-1]) * 100:+.0f}bp")
        return "；".join(bits)

    def _nowcast_rows(self, measure: str) -> list[tuple[date, float]]:
        out = []
        for r in self.nowcast:
            p = self._period(r.get("period", ""))
            if r.get("measure") == measure and p and r.get("nowcast") not in ("", None):
                out.append((p, float(r["nowcast"])))
        return sorted(out)

    def next_release(self, keys: tuple[str, ...], upcoming: list[dict]) -> dict | None:
        for r in upcoming:
            if r.get("key") in keys:
                return {k: r.get(k) for k in ("bj", "title", "forecast_text", "previous_text", "ref", "nowcast_text")}
        return None

    def consensus_for(self, key: str, ref: date) -> dict | None:
        """找同一参考期、发布日最近的一条预期。所有预期记录（含 30 天以前）都查。"""
        best = None
        spec_keys = [t for t, sp in cons.EVENTS.items() if sp.key == key]
        for e in self.events:
            if e.get("title") not in spec_keys:
                continue
            spec = cons.EVENTS[e["title"]]
            try:
                at = datetime.fromisoformat(e["release_at"])
            except ValueError:
                continue
            if at > self.now or cons.ref_period(spec, at.date()) != ref:
                continue
            if best is None or e["release_at"] > best[0]["release_at"]:
                best = (e, spec)
        if not best:
            return None
        e, spec = best
        f = cons.parse_value(e.get("forecast", ""))
        if f is None:
            return None
        actual = self._find(key, ref)
        dec = self._decimals(e["forecast"])
        out = {"forecast_text": e["forecast"], "source": "市场一致预期"}
        if actual is not None:
            diff, verdict, direction = self.judge(spec, actual, f, dec)
            out.update({"surprise_text": f"{diff:+,.{dec}f}", "verdict": verdict, "dir": direction})
        return out

    # =====================================================================
    def signal_json(self, sg: Signal, upcoming: list[dict]) -> dict | None:
        if not sg.data:
            return None
        d, v = sg.data[-1]
        prev = sg.data[-2][1] if len(sg.data) >= 2 else None
        hist = [x for dd, x in sg.data if dd >= PCTILE_START]
        pct = ts.percentile_rank(hist, v) if len(hist) >= 24 else None
        try:
            text, level = sg.rule(I.Ctx(v, prev, None, pct, sg.extra))
        except Exception as exc:  # noqa: BLE001 解读失败不影响数字
            text, level = f"（解读出错：{exc}）", "ok"

        # 较上期 / 较一年前：差值，单位和数值本身一样
        base = sg.cmp if sg.cmp else sg.data
        n_short, short_label, n_long = FREQ[sg.freq]
        sfmt = signed(sg.fmt)

        def change(n: int, label: str) -> dict | None:
            if len(base) <= n:
                return None
            dv = base[-1][1] - base[-1 - n][1]
            txt = sfmt.format(dv)
            flat = float(txt.replace(",", "").replace("+", "")) == 0  # 按显示精度看是否为 0
            return {"label": label, "text": "持平" if flat else txt, "base": sg.fmt.format(base[-1 - n][1]),
                    "dir": "flat" if flat else "up" if dv > 0 else "down"}

        long_label = "一年前"
        cmp = {"short": change(n_short, short_label), "long": change(n_long, long_label), "note": sg.cmp_note,
               "now": sg.fmt.format(base[-1][1]) if sg.cmp else None}

        nxt = self.next_release(sg.keys, upcoming) if sg.keys else None
        last = None
        for k in sg.keys:
            last = self.consensus_for(k, d)
            if last:
                break
        nowcast = self.nowcast_after(sg.nowcast, d) if sg.nowcast else []
        return {"id": sg.id, "name": sg.name, "text": sg.fmt.format(v), "unit": sg.unit, "date": d.isoformat(),
                "cmp": cmp, "pctile": round(pct) if pct is not None else None, "interp": text, "level": level,
                "chart": sg.chart, "next": nxt, "last_surprise": last,
                "nowcast": [{"period": p, "value": round(x, 2)} for p, x in nowcast]}

    def build(self) -> dict:
        dims = [("growth", "增长", self.growth), ("inflation", "通胀", self.inflation),
                ("liquidity", "流动性", self.liquidity), ("fiscal", "财政", self.fiscal),
                ("policy", "货币政策", self.policy)]
        built = [(key, name, *fn()) for key, name, fn in dims]
        self.states = {key: st for key, _, _, _, st in built}
        self.scen = Scenario(self)
        releases = self.releases()  # 需要 actuals 和各维度状态，放在各维度之后
        upcoming = releases["upcoming"]

        out_dims, sections, signals, states = [], [], {}, {}
        for key, name, groups, sigs, state in built:
            states[key] = state
            signals[key] = [x for x in (self.signal_json(sg, upcoming) for sg in sigs) if x]
            for sj in signals[key]:
                if sj["chart"] in self.charts:
                    self.charts[sj["chart"]].setdefault("interp", []).append(
                        {"name": sj["name"], "text": sj["interp"], "level": sj["level"]})
            out_dims.append({
                "key": key, "name": name, "label": state["label"], "head": state["head"],
                "split": state.get("split", False),
                "points": [{"text": t, "level": lv} for t, lv in state["points"]],
                "next": [r for r in upcoming if r.get("dim") == key][:3],
            })
            sections.append({"key": key, "name": name,
                             "groups": [{"name": g, "charts": ids} for g, ids in groups if ids]})

        env = I.environment(states["growth"], states["inflation"], states["policy"], states["liquidity"])
        risks = []
        for dn in out_dims:
            if dn["split"]:
                risks.append({"dim": dn["name"], "name": "分歧", "value": "", "unit": "", "text": dn["head"],
                              "level": "watch"})
            for sj in sorted(signals[dn["key"]], key=lambda x: I.LEVEL_ORDER[x["level"]]):
                if sj["level"] != "ok":
                    risks.append({"dim": dn["name"], "name": sj["name"], "value": sj["text"], "unit": sj["unit"],
                                  "text": sj["interp"], "level": sj["level"]})
        return {
            "asof": self.asof.isoformat(),
            "method": "每个维度按经济锚点判断：增长对约 2% 的潜在增速，就业看非农 3 个月均值和 Sahm，"
                      "通胀对 2% 目标并看短期动能与 Nowcast，流动性分资金市场、金融条件、信用三块，"
                      "财政分财政脉冲与偿债压力，货币分当前立场与市场路径。整体环境由增长 × 通胀的组合决定。"
                      "阈值都写在 pipeline/macro/interpret.py，历史分位只作为参考。",
            "verdict": {"name": env["name"], "headline": env["head"], "sub": env["sub"], "points": risks},
            "dimensions": out_dims,
            "signals": signals,
            "releases": releases,
            "sections": sections,
            "charts": self.charts,
        }


def build_dashboard(raw: dict[str, Series], effr_expect: list[dict] | None = None, asof: date | None = None,
                    events: list[dict] | None = None, nowcast: list[dict] | None = None,
                    now: datetime | None = None) -> dict:
    return MacroBuilder(raw, effr_expect, asof, events, nowcast, now).build()
