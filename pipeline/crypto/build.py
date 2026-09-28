"""把各来源整理成 data/crypto/dashboard.json。结构和宏观、半导体页相同。

结论是「周期 × 资金」。趋势只确认，恐贪只作参考。
缺数据的图不输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .. import series as ts
from ..page import Chart, Line, Metric, metric_json
from ..series import Series
from . import bgeometrics as bg
from . import interpret as I

ETF_DAYS = 20


def chg_days(s: Series, days: int) -> float | None:
    if not s or s[0][0] > s[-1][0] - timedelta(days=days):
        return None
    base = ts.asof(s, s[-1][0] - timedelta(days=days))
    if not base:
        return None
    return (s[-1][1] / base - 1) * 100


def chg_series(s: Series, days: int) -> Series:
    out = []
    for d, v in s:
        target = d - timedelta(days=days)
        if s[0][0] > target:
            continue
        base = ts.asof(s, target)
        if base:
            out.append((d, (v / base - 1) * 100))
    return out


def diff_series(s: Series, days: int) -> Series:
    """相差的百分点，不是百分比。"""
    out = []
    for d, v in s:
        target = d - timedelta(days=days)
        if not s or s[0][0] > target:
            continue
        base = ts.asof(s, target)
        if base is not None:
            out.append((d, v - base))
    return out


def until(s: Series, day: date | None) -> Series:
    if day is None:
        return s
    return [(d, v) for d, v in s if d <= day]


def funding_7d(rows: list[dict], days: int = 7) -> Series:
    """每个有结算的日期，取截至当天最后一笔、往前 days 天的 8 小时费率均值（百分数）。"""
    pts = []
    for row in rows:
        try:
            t = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            pts.append((t, float(row["rate"]) * 100))
        except (KeyError, ValueError):
            continue
    pts.sort()
    out: dict[date, float] = {}
    for i, (t, _) in enumerate(pts):
        cutoff = t - timedelta(days=days)
        window = [v for tt, v in pts[: i + 1] if cutoff < tt <= t]
        if window:
            out[t.date()] = sum(window) / len(window)
    return sorted(out.items())


def rolling_sum_usd(flows_mn: Series, issuance: Series, price: Series, n: int = ETF_DAYS) -> tuple[Series, Series]:
    """ETF 为百万美元。返回最近 n 个交易日的净流入、以及这些日子上新产出的美元价值。"""
    etf_out, iss_out = [], []
    for i in range(n - 1, len(flows_mn)):
        window = flows_mn[i - n + 1 : i + 1]
        etf_usd = sum(v * 1e6 for _, v in window)
        issued = 0.0
        ok = True
        for day, _ in window:
            coins = ts.asof(issuance, day, max_gap_days=4)
            px = ts.asof(price, day, max_gap_days=4)
            if coins is None or px is None:
                ok = False
                break
            issued += coins * px
        if not ok:
            continue
        etf_out.append((window[-1][0], etf_usd))
        iss_out.append((window[-1][0], issued))
    return etf_out, iss_out


def sum_last(flows_mn: Series, n: int = ETF_DAYS) -> Series:
    out = []
    for i in range(n - 1, len(flows_mn)):
        window = flows_mn[i - n + 1 : i + 1]
        out.append((window[-1][0], sum(v for _, v in window)))
    return out


def hodl_series(rows: list[dict]) -> tuple[Series, Series]:
    coins, share = [], []
    for row in rows:
        got = bg.hodl_share(row)
        if got is None:
            continue
        try:
            day = date.fromisoformat(str(row["date"])[:10])
        except (KeyError, ValueError):
            continue
        coins.append((day, got[0]))
        share.append((day, got[1]))
    return ts.clean(coins), ts.clean(share)


def oi_ratio(oi_usd: Series, mcap: Series) -> Series:
    out = []
    for d, oi in oi_usd:
        cap = ts.asof(mcap, d, max_gap_days=3)
        if cap:
            out.append((d, oi / cap * 100))
    return out


def eth_btc(btc: Series, eth: Series) -> Series:
    return ts.combine(lambda b, e: e / b if b else None, btc, eth)


@dataclass
class Sources:
    price: Series = field(default_factory=list)
    mcap: Series = field(default_factory=list)
    mvrv: Series = field(default_factory=list)
    supply: Series = field(default_factory=list)
    issuance: Series = field(default_factory=list)
    eth_ref: Series = field(default_factory=list)  # Coin Metrics，币安日线没有时用
    btc_px: Series = field(default_factory=list)
    eth_px: Series = field(default_factory=list)
    sth: Series = field(default_factory=list)
    lth: Series = field(default_factory=list)
    lth_sopr: Series = field(default_factory=list)
    hodl: list = field(default_factory=list)
    funding: list = field(default_factory=list)
    oi_usd: Series = field(default_factory=list)
    ls_ratio: Series = field(default_factory=list)
    etf_btc: Series = field(default_factory=list)  # 百万美元
    etf_eth: Series = field(default_factory=list)
    stable: Series = field(default_factory=list)
    dominance: Series = field(default_factory=list)
    fng: Series = field(default_factory=list)


class CryptoBuilder:
    def __init__(self, src: Sources, asof: date | None = None, now: datetime | None = None):
        self.src = src
        self.asof = asof or date.today()
        self.now = now or datetime.combine(self.asof, datetime.min.time(), tzinfo=timezone.utc)
        self.charts: dict[str, dict] = {}

    def add(self, *charts: Chart) -> list[str]:
        ids = []
        for c in charts:
            js = c.to_json()
            if js:
                self.charts[c.id] = js
                ids.append(c.id)
        return ids

    def _last(self, s: Series, day: date | None = None) -> float | None:
        if day is not None:
            return ts.asof(s, day, max_gap_days=10)
        return s[-1][1] if s else None

    def valuation(self):
        price, mvrv = self.src.price, self.src.mvrv
        realized = ts.combine(lambda p, m: p / m if m else None, price, mvrv)
        v = {"mvrv": self._last(mvrv), "price": self._last(price), "realized": self._last(realized)}
        st = I.valuation_state(v)
        ids = self.add(
            Chart("mvrv", "MVRV", "倍", [Line("MVRV", mvrv)], core=True,
                  note=f"低于 {I.MVRV_CHEAP:g} 为低于全体成本，高于 {I.MVRV_HOT:g} 为过热（ETF 时代的顶部，不是 2017 年的 4）。"),
            Chart("price_cost", "价格与全体持有者成本", "美元",
                  [Line("价格", price), Line("实现价格", realized)], core=True,
                  note="实现价格 = 价格 / MVRV，即全部比特币最后一次移动时的平均成本。"),
        )
        metrics = [
            Metric("mvrv", "MVRV", mvrv, "倍", "{:.2f}", "D", "mvrv"),
            Metric("price", "价格", price, "美元", "{:,.0f}", "D", "price_cost"),
            Metric("realized", "实现价格", realized, "美元", "{:,.0f}", "D", "price_cost"),
        ]
        return [("估值", ids)], metrics, st

    def holders(self):
        coins, share = hodl_series(self.src.hodl)
        supply_chg = chg_series(coins, 30)
        sopr = self.src.lth_sopr
        sopr7 = ts.rolling_obs(sopr, 7) if len(sopr) >= 7 else []
        sth, lth, price = self.src.sth, self.src.lth, self.src.price
        days = [s[-1][0] for s in (sth, sopr7, supply_chg) if s]
        # 免费接口里成本和 SOPR 大约滞后 7 天。只把相差两周以内的日期放在一起，
        # 再用其中最早的一天，避免拿今天的价格去比过期的成本，也不被一条停更很久的序列拖住。
        anchor = None
        if days:
            latest = max(days)
            close = [d for d in days if (latest - d).days <= 14]
            anchor = min(close) if close else latest
        v = {
            "price": self._last(price, anchor),
            "sth": self._last(sth, anchor),
            "lth": self._last(lth, anchor),
            "supply_30d": self._last(supply_chg, anchor),
            "sopr7": self._last(sopr7, anchor),
        }
        st = I.holder_state(v)
        ids = self.add(
            Chart("cost_ladder", "价格与短线、长线成本", "美元",
                  [Line("价格", price), Line("短线成本", sth), Line("长线成本", lth)], core=True,
                  note="短线、长线成本来自 BGeometrics 免费接口，大约滞后 7 天。价格低于短线成本表示近期买入的人整体亏损。"),
            Chart("hodl", "1 年以上供给占比", "%", [Line("1 年以上", share)], core=True,
                  note="HODL 波段里 1 年以上的合计。标准波段对不齐 155 天，所以不用 Glassnode 的长线定义。"),
            Chart("sopr", "长线 SOPR", "倍", [Line("当日", sopr), Line("7 日均值", sopr7)], core=True,
                  note="高于 1 为盈利卖出，低于 1 为亏损卖出。判断用 7 日均值。"),
        )
        metrics = [
            Metric("supply_30d", "1 年以上供给（30 天）", until(supply_chg, anchor), "%", "{:+.2f}", "D", "hodl"),
            Metric("sopr7", "长线 SOPR（7 日）", until(sopr7, anchor), "倍", "{:.2f}", "DW", "sopr"),
            Metric("sth", "短线成本", until(sth, anchor), "美元", "{:,.0f}", "D", "cost_ladder"),
            Metric("lth", "长线成本", until(lth, anchor), "美元", "{:,.0f}", "D", "cost_ladder"),
        ]
        return [("成本与币龄", ids)], metrics, st

    def leverage(self):
        fund = funding_7d(self.src.funding)
        ratio = oi_ratio(self.src.oi_usd, self.src.mcap)
        oi_chg = chg_series(ratio, 30)
        v = {"funding7": self._last(fund), "oi_30d": self._last(oi_chg)}
        st = I.leverage_state(v)
        ids = self.add(
            Chart("funding", "资金费率（7 日均值）", "% / 8 小时", [Line("7 日均值", fund)], core=True,
                  note=f"币安 BTCUSDT 永续。基准每 8 小时 0.01%，7 日均值高于 {I.FUND_HOT:g}% 为多头拥挤。不是全市场。"
                       "月度压缩包不含当月，最近一段用 BGeometrics 转载的同一费率，大约滞后 7 天。"),
            Chart("oi", "未平仓 / 市值", "%", [Line("未平仓名义金额占市值", ratio)], core=True,
                  note=f"币安 BTCUSDT。30 天变化高于 +{I.OI_UP:g}% 为加杠杆，低于 {I.OI_DOWN:g}% 为去杠杆。"),
            Chart("ls", "全账户多空比（参考）", "倍", [Line("多空比", self.src.ls_ratio)],
                  note="大于 1 表示多头账户更多。偏散户，不进判断。"),
        )
        metrics = [
            Metric("funding7", "资金费率（7 日均值）", fund, "%", "{:.3f}", "DW", "funding"),
            Metric("oi_30d", "未平仓/市值（30 天）", oi_chg, "%", "{:+.1f}", "D", "oi"),
            Metric("ls_ratio", "全账户多空比", self.src.ls_ratio, "倍", "{:.2f}", "D", "ls", ref=True),
        ]
        return [("杠杆", [i for i in ids if i != "ls"]), ("参考", [i for i in ids if i == "ls"])], metrics, st

    def spot(self):
        etf, iss = rolling_sum_usd(self.src.etf_btc, self.src.issuance, self.src.price)
        etf_mn = [(d, v / 1e6) for d, v in etf]
        iss_mn = [(d, v / 1e6) for d, v in iss]
        stable_chg = chg_series(self.src.stable, 30)
        eth20 = sum_last(self.src.etf_eth)
        v = {
            "etf_usd": etf[-1][1] if etf else None,
            "issuance_usd": iss[-1][1] if iss else None,
            "stable_30d": stable_chg[-1][1] if stable_chg else None,
        }
        st = I.spot_state(v)
        note = ""
        if self.src.etf_btc:
            d, flow = self.src.etf_btc[-1]
            note = f"最新交易日 {d.isoformat()} 为 {flow:+,.1f} 百万美元，单日不改标签。"
        ids = self.add(
            Chart("etf_daily", "比特币现货 ETF 每日净流入", "百万美元",
                  [Line("净流入", self.src.etf_btc, "bar")], core=True,
                  note="Farside 合计，单位百万美元。" + note),
            Chart("etf_20", "ETF 20 日净流入与新产出", "百万美元",
                  [Line("ETF 20 日", etf_mn), Line("新产出", iss_mn, dash=True)], core=True,
                  note="新产出用 Coin Metrics 的每日发行量（含手续费）乘当天价格。流入要盖过这条虚线。"),
            Chart("stables", "稳定币流通量", "美元", [Line("流通量", self.src.stable)], core=True,
                  note=f"DefiLlama 全部稳定币。30 天高于 +{I.STABLE_UP:g}% 为扩张，低于 {I.STABLE_DOWN:g}% 为收缩。"),
            Chart("etf_eth", "以太坊现货 ETF 每日净流入（参考）", "百万美元",
                  [Line("净流入", self.src.etf_eth, "bar")],
                  note="同一张 Farside 表。第一期不进比特币的需求投票。"),
        )
        metrics = [
            Metric("etf_20d", "比特币 ETF（20 个交易日）", etf_mn, "百万美元", "{:+,.0f}", "D", "etf_20", note=note),
            Metric("issuance_20d", "同期新产出", iss_mn, "百万美元", "{:,.0f}", "D", "etf_20"),
            Metric("stable_30d", "稳定币流通量（30 天）", stable_chg, "%", "{:+.1f}", "D", "stables"),
            Metric("etf_eth_20d", "以太坊 ETF（20 个交易日）", eth20, "百万美元", "{:+,.0f}", "D", "etf_eth", ref=True),
        ]
        return ([("现货", [i for i in ids if i != "etf_eth"]), ("参考", [i for i in ids if i == "etf_eth"])],
                metrics, st)

    def trend(self):
        price = self.src.price
        ma = ts.rolling_obs(price, 200) if len(price) >= 200 else []
        dist = ts.combine(lambda p, m: (p / m - 1) * 100 if m else None, price, ma)
        st = I.trend_state({"ma_dist": self._last(dist)})
        ids = self.add(
            Chart("ma", "价格与 200 日均线", "美元", [Line("价格", price), Line("200 日均线", ma)], core=True,
                  note=f"收盘高于均线 {I.MA_UP:g}% 为上升，低于 {I.MA_DOWN:g}% 为下降。只确认周期，不改周期名字。"),
        )
        metrics = [Metric("ma_dist", "相对 200 日均线", dist, "%", "{:+.1f}", "D", "ma")]
        return [("趋势", ids)], metrics, st

    def breadth(self):
        if self.src.btc_px and self.src.eth_px:
            ratio = eth_btc(self.src.btc_px, self.src.eth_px)
            ratio_note = "币安现货日线收盘。"
        else:
            ratio = eth_btc(self.src.price, self.src.eth_ref)
            ratio_note = "Coin Metrics 价格。"
        eth_chg = chg_series(ratio, 30)
        dom_chg = diff_series(self.src.dominance, 30)
        v = {"dom_30d": self._last(dom_chg), "eth_30d": self._last(eth_chg)}
        st = I.breadth_state(v)
        ids = self.add(
            Chart("dom", "比特币主导率", "%", [Line("主导率", self.src.dominance)], core=True,
                  note=f"30 天变化超过 ±{I.DOM_BAND:g} 个百分点为独强或扩散。历史来自 BGeometrics。"),
            Chart("ethbtc", "以太坊 / 比特币", "倍", [Line("ETH/BTC", ratio)], core=True, note=ratio_note),
        )
        metrics = [
            Metric("dom_30d", "主导率（30 天）", dom_chg, "个百分点", "{:+.1f}", "D", "dom"),
            Metric("eth_30d", "以太坊/比特币（30 天）", eth_chg, "%", "{:+.1f}", "D", "ethbtc"),
        ]
        return [("广度", ids)], metrics, st

    def sentiment(self):
        fng = self.src.fng
        fng7 = ts.rolling_obs(fng, 7) if len(fng) >= 7 else []
        st = I.sentiment_state({"fng": self._last(fng), "fng7": self._last(fng7)})
        ids = self.add(
            Chart("fng", "恐贪指数", "", [Line("当日", fng), Line("7 日均值", fng7, dash=True)], core=True,
                  note="alternative.me。大半是波动率和动量，和 200 日均线重复，不进顶部判断。"),
        )
        metrics = [
            Metric("fng", "恐贪指数", fng, "", "{:.0f}", "DW", "fng", ref=True),
            Metric("fng7", "恐贪指数（7 日）", fng7, "", "{:.0f}", "DW", "fng", ref=True),
        ]
        return [("情绪", ids)], metrics, st

    def build(self) -> dict:
        parts = [
            ("valuation", "估值", self.valuation()),
            ("holders", "持有者", self.holders()),
            ("leverage", "杠杆", self.leverage()),
            ("spot", "现货需求", self.spot()),
            ("trend", "趋势", self.trend()),
            ("breadth", "广度", self.breadth()),
            ("sentiment", "情绪", self.sentiment()),
        ]
        out_dims, sections = [], []
        st = {}
        for key, name, (groups, metrics, state) in parts:
            st[key] = state
            out_dims.append({
                "key": key, "name": name, "label": state["label"], "why": state.get("why", []),
                "head": state["head"],
                "metrics": [x for x in (metric_json(m, state.get("anchors", {}), self.charts) for m in metrics) if x],
            })
            sections.append({"key": key, "name": name,
                             "groups": [{"name": g, "charts": ids} for g, ids in groups if ids]})
        env = I.environment(
            st["valuation"]["label"], st["holders"]["label"], st["spot"]["label"], st["leverage"]["label"],
            st["spot"].get("etf_vote"), st["trend"]["label"], st["breadth"]["label"], st["breadth"].get("eth_word"),
        )
        return {
            "asof": self.asof.isoformat(),
            "method": "比特币周期按「估值 × 持有者」起名字，资金按「现货需求 × 杠杆」起名字，两边矛盾就写分化，不平均。"
                      "估值看 MVRV（低于 1 为低于全体成本，高于 2.4 为过热）；持有者看 1 年以上供给的 30 天变化和长线 SOPR 的 7 日均值。"
                      "杠杆只看币安 BTCUSDT：资金费率 7 日均值和未平仓占市值的 30 天变化，去杠杆优先。"
                      "现货需求看美国比特币现货 ETF 20 个交易日净流入有没有超过同期新产出，稳定币流通量 30 天变化投第二票。"
                      "价格相对 200 日均线只确认，不改周期名字。恐贪指数只作参考。"
                      "阈值都写在 pipeline/crypto/interpret.py。",
            "verdict": {"name": env["name"], "headline": env["head"], "lines": env["lines"], "label": "比特币周期"},
            "dimensions": out_dims,
            "releases": {"upcoming": [], "recent": []},
            "sections": sections,
            "charts": self.charts,
        }


def build_dashboard(src: Sources, asof: date | None = None, now: datetime | None = None) -> dict:
    return CryptoBuilder(src, asof, now).build()
