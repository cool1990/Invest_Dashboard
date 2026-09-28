"""美股三个维度的状态，以及整体判断。

和宏观页一样按经济锚点判断，不和历史平均比。阈值都写在这里。
情绪的四块（风险偏好、参与度、杠杆、集中度）分开写，不合成一个分数。
"""

from __future__ import annotations

from ..macro.interpret import _f, _head, _why

# 盈利：30 日 EPS 修正 %，和半导体页同一套锚点
EPS_UP, EPS_DOWN = 2.0, -2.0
SPREAD_N = 3  # 强上修或强下修至少这么多家，且多于另一边，才算扩散

# 估值：盈利收益率（100 / 远期市盈率）减去 10 年实际利率，单位百分点
ERP_RICH, ERP_CHEAP = 2.0, 4.0  # 低于 2 贵，高于 4 便宜，中间大致合理

# 情绪
VIX_CALM, VIX_STRESS = 15.0, 25.0
CNN_FEAR, CNN_GREED = 25.0, 75.0
AAII_FEAR, AAII_GREED = -15.0, 20.0  # 牛熊差，百分点
BREADTH_NARROW = 40.0  # 站上 200 日均线的比例 %
MARGIN_UP, MARGIN_DOWN = 20.0, 0.0  # 融资余额同比 %
MARGIN_FASTER = 10.0  # 融资同比 − 标普同比，百分点
TOP10 = 30.0  # 前十大权重 %


def _state(label: str, level: int | None, why: list[dict], summary: str, anchors: dict) -> dict:
    return {"label": label, "level": level, "why": why, "head": _head(why), "summary": summary, "anchors": anchors}


def _pct(x: float | None, digits: int = 1) -> str:
    return _f(x, "{:+." + str(digits) + "f}%")


def _spread(n_up: int | None, n_down: int | None) -> str | None:
    if n_up is None or n_down is None:
        return None
    if n_up >= SPREAD_N and n_up > n_down:
        return "强上修在扩散"
    if n_down >= SPREAD_N and n_down > n_up:
        return "强下修在扩散"
    return "强弱没有一边倒"


def earnings_state(v: dict) -> dict:
    """核心篮子下财年 EPS 的 30 日修正中位数。扩散只写进理由，不改中位数定的档。"""
    rev, n = v.get("rev"), v.get("n")
    n_up, n_down = v.get("n_up"), v.get("n_down")
    anchors = {
        "rev": f"高于 +{EPS_UP:g}% 为上修，低于 {EPS_DOWN:g}% 为下修",
        "n_up": f"至少 {SPREAD_N} 家且多于另一边，才算扩散",
    }
    if rev is None:
        return _state("数据不足", None, [], "盈利数据不足", anchors)
    if rev > EPS_UP:
        label, level, base = "上修", 1, "核心篮子的盈利预期在上修"
    elif rev < EPS_DOWN:
        label, level, base = "下修", -1, "核心篮子的盈利预期在下修"
    else:
        label, level, base = "平稳", 0, "核心篮子的盈利修正接近持平"
    spread = _spread(n_up, n_down)
    if spread == "强上修在扩散" and label != "上修":
        summary = base + "，不过强上修的家数更多"
    elif spread == "强下修在扩散" and label != "下修":
        summary = base + "，不过强下修的家数更多"
    elif spread and spread != "强弱没有一边倒":
        summary = base + "，而且" + spread
    else:
        summary = base
    why = _why(
        ("修正", f"核心篮子有数的 {n} 家，30 日 EPS 修正中位数 {_pct(rev, 2)}"
         f"（高于 +{EPS_UP:g}% 为上修，低于 {EPS_DOWN:g}% 为下修）") if n else
        ("修正", f"30 日 EPS 修正中位数 {_pct(rev, 2)}（高于 +{EPS_UP:g}% 为上修，低于 {EPS_DOWN:g}% 为下修）"),
        ("扩散", f"强上修 {n_up} 家、强下修 {n_down} 家，{spread}"
         f"（至少 {SPREAD_N} 家且多于另一边才算扩散）") if spread else None,
    )
    return _state(label, level, why, summary, anchors)


def valuation_state(v: dict) -> dict:
    """远期市盈率换成盈利收益率，再减去 10 年实际利率。手工标普远期市盈率优先于篮子中位数。"""
    pe, erp, real = v.get("pe"), v.get("erp"), v.get("real")
    manual = bool(v.get("manual"))
    who = "手工录入的标普远期市盈率" if manual else "核心篮子远期市盈率中位数"
    anchors = {
        "pe": "不和历史平均市盈率比；判断看盈利收益率减去实际利率",
        "erp": f"低于 {ERP_RICH:g}% 为贵，{ERP_RICH:g}–{ERP_CHEAP:g}% 大致合理，高于 {ERP_CHEAP:g}% 为便宜",
        "real": "10 年期实际利率，从盈利收益率里减去",
    }
    if pe is None:
        return _state("数据不足", None, [], "估值数据不足", anchors)
    ey = 100.0 / pe
    pe_t = f"{who} {_f(pe, '{:.1f}')} 倍，盈利收益率 {_f(ey, '{:.2f}')}%"
    if erp is None or real is None:
        why = _why(("市盈率", pe_t), ("实际利率", "还没有 10 年期实际利率，算不出风险溢价"))
        return _state("数据不足", None, why, "还没法把远期市盈率换成相对实际利率的风险溢价", anchors)
    if erp < ERP_RICH:
        label, level, summary = "贵", -1, "远期盈利收益率盖不住实际利率，估值偏贵"
    elif erp > ERP_CHEAP:
        label, level, summary = "便宜", 1, "远期盈利收益率明显高于实际利率，估值不贵"
    else:
        label, level, summary = "大致合理", 0, "远期盈利收益率相对实际利率大致合理"
    why = _why(
        ("市盈率", pe_t + ("（核心篮子中位数只作参考）" if manual else "（不是标普指数官方的远期市盈率）")),
        ("风险溢价", f"10 年实际利率 {_f(real, '{:.2f}')}%，盈利收益率减去它是 {_f(erp, '{:.2f}')}%"
         f"（低于 {ERP_RICH:g}% 为贵，高于 {ERP_CHEAP:g}% 为便宜）"),
    )
    return _state(label, level, why, summary, anchors)


def _risk_tag(vix: float | None, cnn: float | None, aaii: float | None) -> str | None:
    if vix is None and cnn is None and aaii is None:
        return None
    if vix is None:
        if (aaii is not None and aaii <= AAII_FEAR) or (cnn is not None and cnn < CNN_FEAR):
            return "调查偏悲观"
        if (aaii is not None and aaii >= AAII_GREED) or (cnn is not None and cnn > CNN_GREED):
            return "调查偏贪婪"
        return "中性"
    tag = "平静" if vix < VIX_CALM else "紧张" if vix > VIX_STRESS else "正常"
    fear = (aaii is not None and aaii <= AAII_FEAR) or (cnn is not None and cnn < CNN_FEAR)
    greed = (aaii is not None and aaii >= AAII_GREED) or (cnn is not None and cnn > CNN_GREED)
    if fear and tag == "平静":
        return "平静，但调查偏悲观"
    if fear and tag == "正常":
        return "正常，调查偏悲观"
    if greed and tag == "紧张":
        return "紧张，但调查偏贪婪"
    if greed and tag == "平静":
        return "平静偏贪婪"
    return tag


def _risk_summary(tag: str) -> str:
    if "悲观" in tag:
        return "价格波动不高，但调查偏悲观" if tag.startswith("平静") or tag.startswith("正常") else "风险偏好偏恐惧"
    if "贪婪" in tag:
        return "风险偏好偏贪婪"
    return {"平静": "风险偏好平静", "正常": "风险偏好正常", "紧张": "风险偏好紧张", "中性": "风险偏好中性",
            "调查偏悲观": "调查偏悲观", "调查偏贪婪": "调查偏贪婪"}.get(tag, f"风险偏好{tag}")


def sentiment_state(v: dict) -> dict:
    """四块并列。标签用「 · 」接起来，缺的那块不写。"""
    vix, cnn, aaii = v.get("vix"), v.get("cnn"), v.get("aaii")
    breadth, margin_yoy = v.get("breadth"), v.get("margin_yoy")
    gap, top10 = v.get("margin_gap"), v.get("top10")
    anchors = {
        "vix": f"低于 {VIX_CALM:g} 平静，高于 {VIX_STRESS:g} 紧张",
        "cnn": f"低于 {CNN_FEAR:g} 恐惧，高于 {CNN_GREED:g} 贪婪",
        "aaii": f"牛熊差 ≤ {AAII_FEAR:g} 悲观，≥ {AAII_GREED:g} 乐观",
        "breadth": f"低于 {BREADTH_NARROW:g}% 为面窄",
        "margin_yoy": f"同比高于 +{MARGIN_UP:g}% 为杠杆扩张，低于 {MARGIN_DOWN:g} 为去杠杆",
        "margin_gap": f"融资同比比标普同比高过 {MARGIN_FASTER:g} 个百分点，算快于指数",
        "top10": f"前十大权重合计达到 {TOP10:g}% 为集中",
    }
    parts, phrases = [], []
    why_items = []

    risk = _risk_tag(vix, cnn, aaii)
    if risk:
        parts.append(f"风险偏好{risk}")
        phrases.append(_risk_summary(risk))
        bits = []
        if vix is not None:
            bits.append(f"VIX {_f(vix, '{:.1f}')}，" + ("低于 15，平静" if vix < VIX_CALM else
                                                      "高于 25，紧张" if vix > VIX_STRESS else "在 15–25，正常"))
        if cnn is not None:
            bits.append(f"CNN 恐贪 {_f(cnn, '{:.0f}')}，" + ("低于 25，恐惧" if cnn < CNN_FEAR else
                                                         "高于 75，贪婪" if cnn > CNN_GREED else "在中间"))
        if aaii is not None:
            bits.append(f"AAII 牛熊差 {_f(aaii, '{:+.1f}')} 个百分点，" + ("≤ −15，悲观" if aaii <= AAII_FEAR else
                                                                        "≥ 20，乐观" if aaii >= AAII_GREED else "在中间"))
        why_items.append(("风险偏好", "；".join(bits)))

    if breadth is not None:
        narrow = breadth < BREADTH_NARROW
        parts.append("参与度面窄" if narrow else "参与度正常")
        phrases.append("上涨的参与度窄" if narrow else "上涨的参与度不窄")
        why_items.append(("参与度", f"标普成分股里 {_f(breadth, '{:.0f}')}% 站上 200 日均线"
                          + ("，低于 40%，面窄" if narrow else "，不低于 40%")))

    if margin_yoy is not None:
        if margin_yoy > MARGIN_UP:
            mtag, msum = "杠杆扩张", "融资杠杆在扩张"
        elif margin_yoy < MARGIN_DOWN:
            mtag, msum = "杠杆去化", "融资杠杆在去化"
        else:
            mtag, msum = "杠杆平稳", "融资杠杆变化不大"
        faster = gap is not None and gap > MARGIN_FASTER
        if faster and margin_yoy > MARGIN_DOWN:
            msum += "，而且快于指数"
        parts.append(mtag)
        phrases.append(msum)
        t = f"客户融资余额同比 {_pct(margin_yoy)}（高于 +{MARGIN_UP:g}% 为扩张，低于 0 为去杠杆）"
        if gap is not None:
            t += f"；比标普指数同比 {_f(gap, '{:+.0f}')} 个百分点" + (
                f"，高过 {MARGIN_FASTER:g} 个百分点，融资比指数涨得更快" if faster else "")
        why_items.append(("杠杆", t))

    if top10 is not None:
        hot = top10 >= TOP10
        parts.append("集中" if hot else "集中度未过线")
        phrases.append("指数集中在少数公司" if hot else "前十大权重还没到集中的门槛")
        why_items.append(("集中度", f"跟踪标普 500 的 ETF 前十大权重合计 {_f(top10, '{:.1f}')}%"
                          + (f"，达到 {TOP10:g}%，少数公司就能带动指数" if hot else f"，还低于 {TOP10:g}%")))

    if not parts:
        return _state("数据不足", None, [], "情绪数据不足", anchors)
    return _state(" · ".join(parts), None, _why(*why_items), "；".join(phrases), anchors)


REGIMES = {
    (1, 1): ("顺风", "盈利预期在上修，估值不贵"),
    (1, 0): ("盈利支撑", "盈利预期在上修，估值大致合理"),
    (1, -1): ("涨但偏贵", "盈利预期在上修，但估值已贵"),
    (0, 1): ("估值便宜", "盈利修正接近持平，估值不贵"),
    (0, 0): ("中性", "盈利修正接近持平，估值大致合理"),
    (0, -1): ("估值偏贵", "盈利修正接近持平，但估值已贵"),
    (-1, 1): ("下修但便宜", "盈利预期在下修，估值已经不贵"),
    (-1, 0): ("盈利转弱", "盈利预期在下修，估值大致合理"),
    (-1, -1): ("双杀风险", "盈利预期在下修，估值仍贵"),
}


def environment(earn: dict, val: dict, sent: dict) -> dict:
    """环境名由盈利 × 估值决定。情绪写在下面，不参与起名，避免和前两块互相抵消。"""
    lines = [{"k": k, "t": t} for k, t in (
        ("盈利", earn.get("summary")), ("估值", val.get("summary")), ("情绪", sent.get("summary")),
    ) if t and t not in ("盈利数据不足", "估值数据不足", "情绪数据不足")]
    el, vl = earn.get("level"), val.get("level")
    if el is None or vl is None:
        return {"name": "数据不足", "head": "数据不足", "lines": lines}
    name, meaning = REGIMES[(el, vl)]
    return {"name": name, "head": f"{name}：{meaning}", "lines": lines}
