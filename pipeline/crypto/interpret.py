"""比特币周期的规则。

和宏观、半导体一样按锚点判断，不和历史平均比，也不用 MVRV Z-Score。
NUPL 是 MVRV 的变形（1 − 1/MVRV），不进判断，避免同一件事投两票。

每个维度函数拿到一组数（缺的为 None），返回 label / why / head / summary / anchors。
缺数的不投票。
"""

from __future__ import annotations

from ..macro.interpret import _f, _head, _why

# ---------------------------------------------------------------------------
# 锚点

# MVRV：低于 1 即价格低于全体持有者成本。2.4 按 ETF 上市以后的顶部（约 2.5–2.8），
# 不要求回到 2017 年的 4 以上。1 到 2.4 再拆成成本附近 / 盈利扩张。
MVRV_CHEAP = 1.0
MVRV_FAIR = 1.4
MVRV_HOT = 2.4

# 币安 BTCUSDT 永续的基准费率是每 8 小时 0.01%。7 日均值高于 3 倍为多头拥挤。
FUND_HOT = 0.03  # % / 8h
FUND_SHORT = 0.0

# 未平仓名义金额 / 市值，30 天变化。绝对水平随市场变大，没有稳定锚点。
OI_UP = 15.0
OI_DOWN = -15.0

# 稳定币流通量 30 天变化。
STABLE_UP = 2.0
STABLE_DOWN = -1.0

# 收盘相对 200 日均线。
MA_UP = 5.0
MA_DOWN = -5.0

# 比特币主导率 30 天变化（百分点）。以太坊/比特币 30 天超过这个幅度，才和主导率算矛盾。
DOM_BAND = 1.5
ETH_BAND = 3.0

# 恐贪指数沿用 alternative.me 自己的分段。
FG_EXTREME_FEAR, FG_FEAR, FG_NEUTRAL, FG_GREED = 24, 44, 55, 75


def _state(label: str, why: list[dict], summary: str, anchors: dict, **extra) -> dict:
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors, **extra}


def _pct(x: float | None, digits: int = 1) -> str:
    return _f(x, "{:+." + str(digits) + "f}%")


# ---------------------------------------------------------------------------
# 估值


def valuation_label(mvrv: float | None) -> str:
    if mvrv is None:
        return "数据不足"
    if mvrv < MVRV_CHEAP:
        return "低于成本"
    if mvrv < MVRV_FAIR:
        return "成本附近"
    if mvrv <= MVRV_HOT:
        return "盈利扩张"
    return "过热"


def valuation_state(v: dict) -> dict:
    """MVRV 投一票。实现价格 = 价格 / MVRV，和 MVRV 低于 1 是同一件事，不另投。"""
    mvrv = v.get("mvrv")
    label = valuation_label(mvrv)
    price, realized = v.get("price"), v.get("realized")
    why = _why(
        ("MVRV", mvrv is not None and (
            f"MVRV {_f(mvrv, '{:.2f}')}（低于 {MVRV_CHEAP:g} 为低于全体成本，{MVRV_FAIR:g} 到 {MVRV_HOT:g} 为盈利扩张，高于 {MVRV_HOT:g} 为过热）")),
        ("实现价格", price is not None and realized is not None and (
            f"价格 {_f(price, '{:,.0f}')} 美元，全体持有者成本 {_f(realized, '{:,.0f}')} 美元（实现价格 = 价格 / MVRV，不另投一票）")),
    )
    summary = {
        "低于成本": "价格低于全体持有者的成本",
        "成本附近": "价格在全体持有者成本附近",
        "盈利扩张": "持有者已经有盈利，但还没到过热",
        "过热": "估值过热",
        "数据不足": "估值数据不足",
    }[label]
    anchors = {
        "mvrv": f"< {MVRV_CHEAP:g} 低于成本，{MVRV_CHEAP:g}–{MVRV_FAIR:g} 成本附近，{MVRV_FAIR:g}–{MVRV_HOT:g} 盈利扩张，> {MVRV_HOT:g} 过热",
        "realized": "由价格 / MVRV 得到，与 MVRV 低于 1 是同一件事，不另投一票",
        "price": "对照实现价格看，不单独投票",
    }
    return _state(label, why, summary, anchors)


# ---------------------------------------------------------------------------
# 持有者


def holder_state(v: dict) -> dict:
    """投降优先：价格低于短线成本且长线 SOPR 的 7 日均值低于 1。

    否则：1 年以上供给 30 天下降且 SOPR 高于 1 为派发；供给上升为吸筹；其余为持有。
    短线、长线成本只参与投降的条件和展示，不因为贵或便宜再投一票。
    """
    price, sth, lth = v.get("price"), v.get("sth"), v.get("lth")
    supply_chg, sopr = v.get("supply_30d"), v.get("sopr7")
    if price is not None and sth is not None and sopr is not None and price < sth and sopr < 1:
        label = "投降"
    elif supply_chg is not None and sopr is not None and supply_chg < 0 and sopr > 1:
        label = "派发"
    elif supply_chg is not None and supply_chg > 0:
        label = "吸筹"
    elif supply_chg is not None or sopr is not None:
        label = "持有"
    else:
        label = "数据不足"
    why = _why(
        ("币龄", supply_chg is not None and f"1 年以上的供给 30 天 {_pct(supply_chg)}（上升为吸筹，下降为派发）"),
        ("长线卖出", sopr is not None and f"长线 SOPR 7 日均值 {_f(sopr, '{:.2f}')}（高于 1 为盈利卖出，低于 1 为亏损卖出）"),
        ("短线成本", price is not None and sth is not None and (
            f"价格 {_f(price, '{:,.0f}')} 美元，短线成本 {_f(sth, '{:,.0f}')} 美元"
            + (f"，长线成本 {_f(lth, '{:,.0f}')} 美元" if lth is not None else "")
            + "（价格低于短线成本且 SOPR 低于 1 为投降）")),
    )
    summary = {
        "吸筹": "长期没动的币在增加",
        "持有": "持有者既没有明显吸筹，也没有明显派发",
        "派发": "长期持有者在盈利卖出",
        "投降": "近期买入的人整体亏损，并且在亏损卖出",
        "数据不足": "持有者数据不足",
    }[label]
    anchors = {
        "supply_30d": "30 天上升为吸筹，下降为派发",
        "sopr7": "7 日均值高于 1 为盈利卖出，低于 1 为亏损卖出",
        "sth": "价格低于短线成本，且长线 SOPR 7 日均值低于 1，为投降",
        "lth": "长线成本，只对照短线成本看结构，不另投一票",
    }
    return _state(label, why, summary, anchors)


# ---------------------------------------------------------------------------
# 杠杆


def leverage_state(v: dict) -> dict:
    """去杠杆优先于费率方向。费率用 7 日均值，单次结算不改标签。只代表币安 BTCUSDT。"""
    funding, oi = v.get("funding7"), v.get("oi_30d")
    if funding is None and oi is None:
        label = "数据不足"
    elif oi is not None and oi < OI_DOWN:
        label = "去杠杆"
    elif funding is not None and funding > FUND_HOT:
        label = "多头拥挤"
    elif funding is not None and funding < FUND_SHORT:
        label = "空头拥挤"
    elif oi is not None and oi > OI_UP:
        label = "加杠杆"
    else:
        label = "中性"
    why = _why(
        ("资金费率", funding is not None and (
            f"币安 BTCUSDT 8 小时费率的 7 日均值 {_f(funding, '{:.3f}')}%（高于 {FUND_HOT:g}% 为多头拥挤，低于 0 为空头付费；基准是每 8 小时 0.01%）")),
        ("未平仓", oi is not None and (
            f"未平仓名义金额 / 市值，30 天 {_pct(oi)}（高于 +{OI_UP:g}% 为加杠杆，低于 {OI_DOWN:g}% 为去杠杆）")),
    )
    summary = {
        "多头拥挤": "多头在付偏高的资金费",
        "加杠杆": "杠杆在加，费率还没到拥挤",
        "中性": "杠杆中性",
        "空头拥挤": "空头在付资金费",
        "去杠杆": "未平仓明显下降",
        "数据不足": "杠杆数据不足",
    }[label]
    anchors = {
        "funding7": f"7 日均值 > {FUND_HOT:g}% 多头拥挤，< 0 空头付费；基准每 8 小时 0.01%",
        "oi_30d": f"30 天 > +{OI_UP:g}% 加杠杆，< {OI_DOWN:g}% 去杠杆",
        "ls_ratio": "全账户多空比，散户持仓，不进判断",
    }
    return _state(label, why, summary, anchors)


# ---------------------------------------------------------------------------
# 现货需求


def etf_vote(etf_usd: float | None, issuance_usd: float | None) -> int | None:
    """20 个交易日的 ETF 净流入对照同期新产出。正的但没超过新供给，不算流入。"""
    if etf_usd is None or issuance_usd is None:
        return None
    if etf_usd > issuance_usd:
        return 1
    if etf_usd < 0 and abs(etf_usd) > issuance_usd:
        return -1
    return 0


def stable_vote(chg: float | None) -> int | None:
    if chg is None:
        return None
    if chg > STABLE_UP:
        return 1
    if chg < STABLE_DOWN:
        return -1
    return 0


def _avg(votes: list[int | None]) -> float | None:
    vs = [x for x in votes if x is not None]
    return sum(vs) / len(vs) if vs else None


def spot_state(v: dict) -> dict:
    ev, sv = etf_vote(v.get("etf_usd"), v.get("issuance_usd")), stable_vote(v.get("stable_30d"))
    score = _avg([ev, sv])
    if score is None:
        label = "数据不足"
    elif score >= 0.5:
        label = "流入"
    elif score <= -0.5:
        label = "流出"
    else:
        label = "平淡"
    etf, iss, stable = v.get("etf_usd"), v.get("issuance_usd"), v.get("stable_30d")
    why = _why(
        ("ETF", etf is not None and iss is not None and (
            f"比特币现货 ETF 20 个交易日净流入 {_f(etf / 1e6, '{:+,.0f}')} 百万美元，"
            f"同期新产出 {_f(iss / 1e6, '{:,.0f}')} 百万美元（流入要超过新产出；净流出的绝对值超过新产出才算偏弱）")),
        ("稳定币", stable is not None and (
            f"主要稳定币流通量 30 天 {_pct(stable)}（高于 +{STABLE_UP:g}% 为扩张，低于 {STABLE_DOWN:g}% 为收缩）")),
    )
    summary = {
        "流入": "现货需求强于新产出的比特币",
        "平淡": "现货需求平淡",
        "流出": "现货需求在流出",
        "数据不足": "现货需求数据不足",
    }[label]
    anchors = {
        "etf_20d": "20 个交易日净流入高于同期新产出为偏强；净流出且绝对值高于新产出为偏弱",
        "issuance_20d": "对照 ETF 净流入，本身不单独投票",
        "stable_30d": f"30 天 > +{STABLE_UP:g}% 扩张，< {STABLE_DOWN:g}% 收缩",
        "etf_eth_20d": "以太坊现货 ETF，只展示，不进比特币的需求投票",
    }
    return _state(label, why, summary, anchors, etf_vote=ev, stable_vote=sv)


# ---------------------------------------------------------------------------
# 趋势、广度、情绪


def trend_state(v: dict) -> dict:
    x = v.get("ma_dist")
    if x is None:
        label = "数据不足"
    elif x > MA_UP:
        label = "上升趋势"
    elif x < MA_DOWN:
        label = "下降趋势"
    else:
        label = "趋势中性"
    why = _why(("200 日均线", x is not None and (
        f"收盘相对 200 日均线 {_pct(x)}（高于 +{MA_UP:g}% 为上升，低于 {MA_DOWN:g}% 为下降）")))
    summary = {
        "上升趋势": "价格在 200 日均线上方",
        "下降趋势": "价格在 200 日均线下方",
        "趋势中性": "价格在 200 日均线附近",
        "数据不足": "趋势数据不足",
    }[label]
    anchors = {"ma_dist": f"> +{MA_UP:g}% 上升，< {MA_DOWN:g}% 下降；只确认周期，不改周期名字"}
    return _state(label, why, summary, anchors)


def breadth_state(v: dict) -> dict:
    """主导率定标签。以太坊相对比特币的方向和它矛盾时，这一维自己标分化。"""
    dom, eth = v.get("dom_30d"), v.get("eth_30d")
    if dom is None and eth is None:
        label = "数据不足"
    elif dom is None:
        label = "数据不足"
    elif dom > DOM_BAND:
        label = "比特币独强"
    elif dom < -DOM_BAND:
        label = "扩散"
    else:
        label = "结构稳定"
    if eth is not None and (
        (label == "比特币独强" and eth > ETH_BAND) or (label == "扩散" and eth < -ETH_BAND)
    ):
        label = "分化"
    eth_word = None if eth is None else "走强" if eth > ETH_BAND else "走弱" if eth < -ETH_BAND else "大致持平"
    why = _why(
        ("主导率", dom is not None and (
            f"比特币主导率 30 天 {_f(dom, '{:+.1f}')} 个百分点（超过 ±{DOM_BAND:g} 个百分点才改标签）")),
        ("以太坊", eth is not None and (
            f"以太坊/比特币 30 天 {_pct(eth)}（超过 ±{ETH_BAND:g}% 且和主导率相反，这一维标分化）")),
    )
    summary = {
        "比特币独强": "风险偏好停在比特币",
        "扩散": "风险偏好已经从比特币扩散出去",
        "结构稳定": "风险偏好的结构大致稳定",
        "分化": "主导率和以太坊相对比特币的方向不一致",
        "数据不足": "广度数据不足",
    }[label]
    anchors = {
        "dom_30d": f"30 天超过 ±{DOM_BAND:g} 个百分点为独强或扩散",
        "eth_30d": f"30 天超过 ±{ETH_BAND:g}% 且和主导率相反，标分化",
    }
    return _state(label, why, summary, anchors, eth_word=eth_word)


def fear_greed_label(value: float | None) -> str:
    if value is None:
        return "数据不足"
    if value <= FG_EXTREME_FEAR:
        return "极度恐惧"
    if value <= FG_FEAR:
        return "恐惧"
    if value <= FG_NEUTRAL:
        return "中性"
    if value <= FG_GREED:
        return "贪婪"
    return "极度贪婪"


def sentiment_state(v: dict) -> dict:
    """恐贪指数大半是波动率和动量，和趋势重复，不进顶部判断。"""
    value, avg = v.get("fng"), v.get("fng7")
    label = fear_greed_label(value)
    why = _why(
        ("恐贪", value is not None and (
            f"指数 {_f(value, '{:.0f}')}"
            + (f"，7 日均值 {_f(avg, '{:.0f}')}" if avg is not None else "")
            + f"（≤{FG_EXTREME_FEAR} 极度恐惧，≤{FG_FEAR} 恐惧，≤{FG_NEUTRAL} 中性，≤{FG_GREED} 贪婪，以上为极度贪婪）")),
    )
    summary = {
        "极度恐惧": "情绪处于极度恐惧",
        "恐惧": "情绪偏恐惧",
        "中性": "情绪中性",
        "贪婪": "情绪偏贪婪",
        "极度贪婪": "情绪处于极度贪婪",
        "数据不足": "情绪数据不足",
    }[label]
    anchors = {
        "fng": f"≤{FG_EXTREME_FEAR} 极度恐惧，{FG_EXTREME_FEAR + 1}–{FG_FEAR} 恐惧，{FG_FEAR + 1}–{FG_NEUTRAL} 中性，{FG_NEUTRAL + 1}–{FG_GREED} 贪婪，≥{FG_GREED + 1} 极度贪婪；不进顶部判断",
        "fng7": "7 日均值，不进顶部判断",
    }
    return _state(label, why, summary, anchors)


# ---------------------------------------------------------------------------
# 顶部：周期 × 资金。趋势只确认，广度另起一行。情绪不进这里。


def cycle_name(valuation: str, holder: str) -> str:
    if valuation == "数据不足" or holder == "数据不足":
        return "数据不足"
    if valuation == "低于成本" and holder == "投降":
        return "出清"
    if valuation == "成本附近" and holder == "持有":
        return "磨底"
    if valuation == "盈利扩张" and holder in ("吸筹", "持有"):
        return "景气上行"
    if valuation == "过热" and holder == "派发":
        return "见顶风险"
    if valuation == "过热":
        return "过热"
    return "分化"


def funds_name(spot: str, leverage: str, etf_side: int | None) -> str:
    """去杠杆写成资金出清。ETF 净流出超过新产出才写成资金撤退，稳定币单独收缩不顶上这个名字。"""
    if leverage == "数据不足" and spot == "数据不足":
        return "数据不足"
    if leverage == "去杠杆":
        return "资金出清"
    if spot == "流出" and etf_side == -1:
        return "资金撤退"
    if leverage == "多头拥挤" and spot in ("流入", "平淡"):
        return "资金拥挤"
    if spot == "流入" and leverage in ("中性", "加杠杆"):
        return "资金配合"
    if spot == "数据不足" or leverage == "数据不足":
        return "数据不足"
    return "资金平淡"


CYCLE_TEXT = {
    "出清": "估值低于全体持有者成本，持有者在亏损卖出",
    "磨底": "估值在成本附近，持有者既不吸筹也不派发",
    "景气上行": "估值处于盈利扩张，持有者在吸筹或持有",
    "过热": "估值过热，长线持有者还没明显派发",
    "见顶风险": "估值过热，长线持有者在派发",
    "分化": "估值和持有者方向不一致",
    "数据不足": "周期数据还不够",
}

FUNDS_TEXT = {
    "资金配合": "现货需求在流入，杠杆中性或只是温和加杠杆",
    "资金拥挤": "现货仍有需求，但多头资金费率偏高",
    "资金撤退": "现货净流出已经超过同期新产出的比特币",
    "资金出清": "未平仓在下降",
    "资金平淡": "现货需求和杠杆都在中间",
    "数据不足": "资金数据还不够",
}

_SPOT_WORD = {"流入": "现货仍在流入", "流出": "现货也在流出", "平淡": "现货需求平淡", "数据不足": ""}

_UP_CYCLES = {"景气上行", "过热", "见顶风险"}
_DOWN_CYCLES = {"出清", "磨底"}


def confirm_text(cycle: str, trend: str) -> str:
    """价格相对 200 日均线只确认周期，不改周期名字。"""
    if trend == "数据不足":
        return "价格数据还不够确认"
    side = {"上升趋势": "上方", "下降趋势": "下方"}.get(trend, "附近")
    if cycle in _UP_CYCLES and trend == "上升趋势":
        return f"价格已确认，收盘在 200 日均线{side}"
    if cycle in _DOWN_CYCLES and trend == "下降趋势":
        return f"价格已确认，收盘在 200 日均线{side}"
    if cycle == "数据不足":
        return f"周期数据不足，收盘在 200 日均线{side}"
    return f"价格尚未确认，收盘在 200 日均线{side}"


def environment(valuation: str, holder: str, spot: str, leverage: str, etf_side: int | None,
                trend: str, breadth: str, eth_word: str | None) -> dict:
    cycle = cycle_name(valuation, holder)
    funds = funds_name(spot, leverage, etf_side)
    if cycle == "数据不足" and funds == "数据不足":
        name, head = "数据不足", "数据还不够下判断"
    else:
        name = "，".join(x for x in (cycle, funds) if x != "数据不足") or "数据不足"
        head = name
    cycle_line = CYCLE_TEXT[cycle]
    if cycle == "分化":
        cycle_line = f"估值{valuation}，持有者{holder}"
    funds_line = FUNDS_TEXT[funds]
    if funds == "资金出清" and _SPOT_WORD.get(spot):
        funds_line = f"{funds_line}，{_SPOT_WORD[spot]}"
    breadth_line = {
        "比特币独强": "风险偏好停在比特币",
        "扩散": "风险偏好已经从比特币扩散出去",
        "结构稳定": "风险偏好的结构大致稳定",
        "分化": "主导率和以太坊相对比特币的方向不一致",
        "数据不足": "广度数据还不够",
    }[breadth]
    if eth_word and breadth != "数据不足":
        breadth_line = f"{breadth_line}，以太坊相对比特币{eth_word}"
    lines = [
        {"k": "周期", "t": cycle_line},
        {"k": "资金", "t": funds_line},
        {"k": "价格", "t": confirm_text(cycle, trend)},
        {"k": "广度", "t": breadth_line},
    ]
    return {"name": name, "head": head, "lines": lines, "cycle": cycle, "funds": funds}
