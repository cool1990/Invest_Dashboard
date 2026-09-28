"""规则：半导体六个维度的状态、两条线的景气象限、领先指标的方向。

和宏观页一样按经济锚点判断，不和历史平均比。阈值都写在这里。
每个维度函数拿到一组数（缺的为 None），返回：
  label    状态标签
  why      理由，每条 {"k": 小结论, "t": 用哪几个数、对照什么锚点得出}
  head     理由连成一句，写进判断变化日志
  summary  不带数字的一句白话
  anchors  {指标 id: 对照的锚点}，第二层每个数旁边那一列
  level    档位：需求 +1 扩张 / 0 放缓 / −1 收缩；供给侧为「偏紧」方向 +1 / 0 / −1

每个进判断的数先按锚点投一票（+1 / 0 / −1），维度取平均。缺数的不投票。
"""

from __future__ import annotations

from ..macro.interpret import _f, _head, _why

# ---------------------------------------------------------------------------
# 锚点（阈值）

CAPEX_UP, CAPEX_DOWN = 20.0, 0.0  # 云厂商资本开支同比 %
CAPEX_ACCEL = 5.0  # 同比较上季抬升超过这么多个百分点算加速
TOKENS_UP, TOKENS_DOWN = 20.0, 0.0  # OpenRouter 最近 30 天用量较前 30 天 %
EPS_UP, EPS_DOWN = 2.0, -2.0  # 下财年 EPS 30 天修正 %
GPU_UP, GPU_DOWN = 10.0, -10.0  # GPU 租金 90 天变化 %

ORDERS_UP, ORDERS_DOWN = 5.0, 0.0  # 美国电子产品新订单 3 个月同比 %
TW_TRAD_UP, TW_TRAD_DOWN = 10.0, 0.0  # 联发科 + 联电 3 个月营收同比 %
ANALOG_UP, ANALOG_DOWN = 5.0, 0.0  # 模拟 / MCU 季度营收同比 %

DRAM_30 = 5.0  # DRAM 现货 30 天变化 ±%
DRAM_7 = 2.0  # 历史不足 30 天时用 7 天变化 ±%
PREMIUM = 5.0  # 现货高于合约 %

EQUIP_UP, EQUIP_DOWN = 10.0, 0.0  # 设备商营收同比 %
BTB_UP, BTB_DOWN = 1.1, 0.9  # ASML 订单出货比
UTIL_TIGHT, UTIL_LOOSE = 80.0, 70.0  # 美国半导体产能利用率 %

KOREA_UP, KOREA_DOWN = 10.0, 0.0  # 韩国芯片出口同比 %
TSMC_UP, TSMC_DOWN = 15.0, 0.0  # 台积电 3 个月营收同比 %
IP_UP, IP_DOWN = 5.0, 0.0  # 美国半导体工业产出同比 %


def vote(x: float | None, up: float, down: float) -> int | None:
    if x is None:
        return None
    return 1 if x > up else -1 if x < down else 0


def _avg(votes: list[int | None]) -> float | None:
    vs = [v for v in votes if v is not None]
    return sum(vs) / len(vs) if vs else None


def _pct(x: float | None, digits: int = 1) -> str:
    return _f(x, "{:+." + str(digits) + "f}%")


def _band(up: float, down: float, unit: str = "%") -> str:
    return f"高于 {up:g}{unit} 偏强，低于 {down:g}{unit} 偏弱"


def _demand_label(score: float | None, accel: float | None) -> tuple[str, int]:
    """平均票 ≥ 0.5 扩张（主锚点同比再抬升 ≥ 5 个百分点为加速）；≤ −0.5 收缩；中间为放缓。"""
    if score is None:
        return "数据不足", 0
    if score >= 0.5:
        return ("加速" if accel is not None and accel >= CAPEX_ACCEL else "扩张"), 1
    if score <= -0.5:
        return "收缩", -1
    return "放缓", 0


def _state(label: str, level: int, why: list[dict], summary: str, anchors: dict, **extra) -> dict:
    return {"label": label, "level": level, "why": why, "head": _head(why), "summary": summary,
            "anchors": anchors, **extra}


# ---------------------------------------------------------------------------
# 需求


def ai_demand_state(v: dict) -> dict:
    """AI 需求：云厂商资本开支是源头（领先 1–2 季），用量与分析师预期是高频验证。"""
    capex, capex_prev = v.get("capex_yoy"), v.get("capex_yoy_prev")
    accel = None if capex is None or capex_prev is None else capex - capex_prev
    tokens, eps = v.get("tokens_30d"), v.get("eps_ai")
    score = _avg([vote(capex, CAPEX_UP, CAPEX_DOWN), vote(tokens, TOKENS_UP, TOKENS_DOWN),
                  vote(eps, EPS_UP, EPS_DOWN)])
    label, level = _demand_label(score, accel)
    why = _why(
        ("资本开支", capex is not None and (
            f"5 家云厂商 {v.get('capex_q', '')} 合计 {_f(v.get('capex_sum'), '{:,.0f}')} 亿美元，同比 {_pct(capex)}"
            + (f"，比上季同比{'抬升' if accel >= 0 else '回落'} {abs(accel):.1f} 个百分点" if accel is not None else "")
            + f"（{_band(CAPEX_UP, CAPEX_DOWN)}）")),
        ("用量", tokens is not None and f"OpenRouter 最近 30 天 token {_f(v.get('tokens_30d_level'), '{:,.0f}')}T，较前 30 天 {_pct(tokens)}（{_band(TOKENS_UP, TOKENS_DOWN)}）"),
        ("预期", eps is not None and f"{v.get('eps_ai_names', '')} 下财年 EPS 30 天平均修正 {_pct(eps, 2)}（±{EPS_UP:g}% 为明显上修/下修）"),
    )
    summary = {"加速": "云厂商加码投入，AI 需求在加速", "扩张": "AI 需求仍在扩张", "放缓": "AI 需求增速放缓",
               "收缩": "AI 需求在收缩"}.get(label, "AI 需求数据不足")
    anchors = {"capex_yoy": f"同比 > {CAPEX_UP:g}% 扩张，< {CAPEX_DOWN:g}% 收缩；比上季抬升 ≥ {CAPEX_ACCEL:g} 个百分点为加速",
               "tokens_30d": f"> {TOKENS_UP:g}% 偏强，< {TOKENS_DOWN:g}% 偏弱",
               "eps_ai": f"> {EPS_UP:+g}% 上修，< {EPS_DOWN:+g}% 下修"}
    return _state(label, level, why, summary, anchors, score=score)


def trad_demand_state(v: dict) -> dict:
    """传统需求：美国电子产品订单领先出货；联发科、联电看手机与成熟制程；模拟芯片周期性最典型。"""
    orders, tw, analog, analog_prev = v.get("orders_yoy"), v.get("tw_trad_yoy"), v.get("analog_yoy"), v.get("analog_yoy_prev")
    eps = v.get("eps_trad")
    accel = None if analog is None or analog_prev is None else analog - analog_prev
    score = _avg([vote(orders, ORDERS_UP, ORDERS_DOWN), vote(tw, TW_TRAD_UP, TW_TRAD_DOWN),
                  vote(analog, ANALOG_UP, ANALOG_DOWN), vote(eps, EPS_UP, EPS_DOWN)])
    label, level = _demand_label(score, accel)
    why = _why(
        ("订单", orders is not None and f"美国计算机与电子产品新订单 3 个月同比 {_pct(orders)}（{_band(ORDERS_UP, ORDERS_DOWN)}）"),
        ("手机与成熟制程", tw is not None and f"联发科 + 联电近 3 个月营收同比 {_pct(tw)}（{_band(TW_TRAD_UP, TW_TRAD_DOWN)}）"),
        ("模拟与 MCU", analog is not None and (
            f"德州仪器、微芯、亚德诺 {v.get('analog_q', '')} 营收合计同比 {_pct(analog)}"
            + (f"，比上季{'抬升' if accel >= 0 else '回落'} {abs(accel):.1f} 个百分点" if accel is not None else "")
            + f"（{_band(ANALOG_UP, ANALOG_DOWN)}）")),
        ("预期", eps is not None and f"{v.get('eps_trad_names', '')} 下财年 EPS 30 天平均修正 {_pct(eps, 2)}"),
    )
    summary = {"加速": "传统芯片需求回升加快", "扩张": "传统芯片需求在恢复", "放缓": "传统芯片需求平淡",
               "收缩": "传统芯片需求在收缩"}.get(label, "传统芯片需求数据不足")
    anchors = {"orders_yoy": f"> {ORDERS_UP:g}% 偏强，< {ORDERS_DOWN:g}% 偏弱",
               "tw_trad_yoy": f"> {TW_TRAD_UP:g}% 偏强，< {TW_TRAD_DOWN:g}% 偏弱",
               "analog_yoy": f"> {ANALOG_UP:g}% 偏强，< {ANALOG_DOWN:g}% 偏弱；比上季抬升 ≥ {CAPEX_ACCEL:g} 个百分点为加速",
               "eps_trad": f"> {EPS_UP:+g}% 上修，< {EPS_DOWN:+g}% 下修"}
    return _state(label, level, why, summary, anchors, score=score)


# ---------------------------------------------------------------------------
# 供给


def inventory_state(v: dict) -> dict:
    """库存周期：出货同比 × 库存同比定四个阶段；出货 − 库存 > 0 说明库存压力在减轻（领先信号）。"""
    ship, inv = v.get("ship_yoy"), v.get("inv_yoy")
    src = v.get("inv_source", "")
    if ship is None or inv is None:
        return _state("数据不足", 0, [], "库存数据不足", {})
    gap = ship - inv
    if ship >= 0:
        label = "被动去库" if inv < 0 else "主动补库"
    else:
        label = "被动补库" if inv >= 0 else "主动去库"
    level = 1 if gap > 0 else -1 if gap < 0 else 0
    phase = {"被动去库": "需求回升、库存还在降（复苏）", "主动补库": "需求好、厂商在补库存（繁荣）",
             "被动补库": "需求转弱、库存被动堆积（见顶）", "主动去库": "需求弱、厂商在砍库存（下行）"}[label]
    dio = v.get("dio_yoy")
    why = _why(
        ("阶段", f"{src}出货 3 个月同比 {_pct(ship)}、库存 {_pct(inv)}：{phase}"),
        ("领先信号", f"出货比库存{'快' if gap >= 0 else '慢'} {abs(gap):.1f} 个百分点，库存压力在{'减轻' if gap >= 0 else '加重'}（> 0 偏紧，< 0 偏松）"),
        ("公司库存", dio is not None and f"{v.get('dio_names', '')} 库存天数较一年前 {_pct(dio)}（参考，偏滞后）"),
    )
    summary = {"被动去库": "库存在消化，供给偏紧", "主动补库": "厂商在补库存", "被动补库": "库存开始堆积，留意见顶",
               "主动去库": "行业在去库存"}[label]
    anchors = {"ship_yoy": "出货同比 ≥ 0 为需求向上", "inv_yoy": "库存同比 ≥ 0 为库存在增加",
               "inv_gap": "出货 − 库存 > 0 库存压力减轻（偏紧），< 0 加重（偏松）",
               "dio_yoy": "参考：库存天数上升说明货卖得慢"}
    return _state(label, level, why, summary, anchors, gap=gap)


def price_state(v: dict) -> dict:
    """价格：DRAM 现货是存储周期最快的信号；现货高于合约，下季合约价大概率跟涨。"""
    dram, window = v.get("dram_chg"), v.get("dram_window", 30)
    th = DRAM_30 if window >= 30 else DRAM_7
    nand, prem, ppi = v.get("nand_chg"), v.get("premium"), v.get("ppi_yoy")
    votes = [vote(dram, th, -th), vote(prem, PREMIUM, -PREMIUM)]
    score = _avg(votes)
    if score is None:
        return _state("数据不足", 0, [], "价格数据不足", {})
    label, level = ("涨价", 1) if score >= 0.5 else ("跌价", -1) if score <= -0.5 else ("企稳", 0)
    why = _why(
        ("DRAM 现货", dram is not None and f"{v.get('dram_names', 'DDR4/DDR5')} 平均 {window} 天 {_pct(dram)}（±{th:g}% 为涨跌）"
         + ("；历史不足 30 天，暂用 7 天变化" if window < 30 else "")),
        ("现货 vs 合约", prem is not None and f"现货比合约{'高' if prem >= 0 else '低'} {abs(prem):.1f}%，下季合约价{'大概率跟涨' if prem > PREMIUM else '大概率走弱' if prem < -PREMIUM else '变化不大'}"),
        ("NAND", nand is not None and f"TLC wafer 现货 {v.get('nand_window', 7)} 天 {_pct(nand)}（参考）"),
        ("PPI", ppi is not None and f"美国半导体 PPI 同比 {_pct(ppi)}（参考）"),
    )
    summary = {"涨价": "存储在涨价，供给偏紧", "企稳": "存储价格走平", "跌价": "存储在跌价，供给偏松"}[label]
    anchors = {"dram_chg": f"30 天 ±{DRAM_30:g}%（历史不足时 7 天 ±{DRAM_7:g}%）",
               "premium": f"现货比合约高 > {PREMIUM:g}% 合约价跟涨", "nand_chg": "参考", "ppi_yoy": "参考",
               "gpu_90d": f"> {GPU_UP:+g}% 算力偏紧，< {GPU_DOWN:+g}% 偏松（进 AI 算力的供给松紧）"}
    return _state(label, level, why, summary, anchors)


def capacity_state(v: dict) -> dict:
    """产能与资本开支：设备商营收与 ASML 订单领先产能 1–2 季；产能扩得太猛是下一轮下行的种子。
    level 按「偏紧」方向：扩张 −1（未来供给增加），收缩 +1。"""
    equip, btb, util, capex_g = v.get("equip_yoy"), v.get("asml_btb"), v.get("util"), v.get("tsmc_capex")
    score = _avg([vote(equip, EQUIP_UP, EQUIP_DOWN), vote(btb, BTB_UP, BTB_DOWN)])
    if score is None:
        return _state("数据不足", 0, [], "产能数据不足", {})
    label, level = ("扩张", -1) if score >= 0.5 else ("收缩", 1) if score <= -0.5 else ("平稳", 0)
    why = _why(
        ("设备", equip is not None and f"应用材料、泛林、科磊 {v.get('equip_q', '')} 营收合计同比 {_pct(equip)}（{_band(EQUIP_UP, EQUIP_DOWN)}）"),
        ("光刻订单", btb is not None and f"ASML 订单出货比 {btb:.2f}（> {BTB_UP:g} 扩产，< {BTB_DOWN:g} 收缩）"),
        ("利用率", util is not None and f"美国半导体产能利用率 {util:.1f}%（≥ {UTIL_TIGHT:g}% 偏紧，< {UTIL_LOOSE:g}% 偏松；参考）"),
        ("台积电", capex_g is not None and f"全年资本开支指引中值 {capex_g:,.0f} 亿美元（参考）"),
    )
    summary = {"扩张": "设备投资在扩张，1–2 个季度后供给增加", "平稳": "扩产节奏平稳", "收缩": "扩产在放缓，未来供给偏紧"}[label]
    anchors = {"equip_yoy": f"> {EQUIP_UP:g}% 扩张，< {EQUIP_DOWN:g}% 收缩",
               "asml_btb": f"> {BTB_UP:g} 扩产，< {BTB_DOWN:g} 收缩",
               "util": f"≥ {UTIL_TIGHT:g}% 偏紧，< {UTIL_LOOSE:g}% 偏松（参考）", "tsmc_capex": "参考"}
    return _state(label, level, why, summary, anchors)


# ---------------------------------------------------------------------------
# 同步确认


def shipments_state(v: dict) -> dict:
    """出货确认：韩国出口最早公布，台积电月营收看 AI 与先进制程，美国工业产出看本土。"""
    korea, tsmc, ip = v.get("korea_yoy"), v.get("tsmc_yoy"), v.get("ip_yoy")
    score = _avg([vote(korea, KOREA_UP, KOREA_DOWN), vote(tsmc, TSMC_UP, TSMC_DOWN), vote(ip, IP_UP, IP_DOWN)])
    if score is None:
        return _state("数据不足", 0, [], "出货数据不足", {})
    label, level = ("走强", 1) if score >= 0.5 else ("走弱", -1) if score <= -0.5 else ("持平", 0)
    why = _why(
        ("韩国出口", korea is not None and f"{v.get('korea_period', '')} 芯片出口同比 {_pct(korea)}（{_band(KOREA_UP, KOREA_DOWN)}）"
         + (f"；{v['korea_mix']}" if v.get("korea_mix") else "")),
        ("台积电", tsmc is not None and f"近 3 个月营收同比 {_pct(tsmc)}（{_band(TSMC_UP, TSMC_DOWN)}）"),
        ("美国产出", ip is not None and f"半导体工业产出 3 个月同比 {_pct(ip)}（{_band(IP_UP, IP_DOWN)}）"),
    )
    summary = {"走强": "实际出货在走强", "持平": "实际出货平稳", "走弱": "实际出货在走弱"}[label]
    anchors = {"korea_yoy": f"> {KOREA_UP:g}% 偏强，< {KOREA_DOWN:g}% 偏弱",
               "tsmc_yoy": f"> {TSMC_UP:g}% 偏强，< {TSMC_DOWN:g}% 偏弱",
               "ip_yoy": f"> {IP_UP:g}% 偏强，< {IP_DOWN:g}% 偏弱"}
    return _state(label, level, why, summary, anchors)


# ---------------------------------------------------------------------------
# 领先指标方向（给「领先指标一览」）


def direction(x: float | None, up: float, down: float) -> str | None:
    """偏多 / 偏空 / 中性（对景气而言）。"""
    v = vote(x, up, down)
    return None if v is None else {1: "偏多", 0: "中性", -1: "偏空"}[v]


# ---------------------------------------------------------------------------
# 象限与整体


QUAD = {
    (1, 1): "景气上行", (1, 0): "温和扩张", (1, -1): "量增价平",
    (0, 1): "见顶风险", (0, 0): "景气放缓", (0, -1): "去库下行",
    (-1, 1): "见顶风险", (-1, 0): "景气下行", (-1, -1): "去库下行",
}
QUAD_MEANING = {
    "景气上行": "量价齐升",
    "温和扩张": "量在增，价格平稳",
    "量增价平": "量在增，但供给跟得上，价格涨不动",
    "见顶风险": "需求不再扩张，价格还在高位",
    "景气放缓": "需求放缓，供需大致平衡",
    "景气下行": "需求在收缩",
    "去库下行": "需求不强、供给偏松，价格承压",
}


def tightness(levels: list[int | None]) -> int | None:
    a = _avg([x for x in levels])
    if a is None:
        return None
    return 1 if a >= 1 / 3 else -1 if a <= -1 / 3 else 0


def line_verdict(name: str, demand: dict, tight: int | None, tight_why: str) -> dict:
    if demand["label"] == "数据不足" or tight is None:
        return {"name": "数据不足", "t": f"{name}的数据还不够下判断", "quad": None}
    q = QUAD[(demand["level"], tight)]
    t_word = {1: "偏紧", 0: "平衡", -1: "偏松"}[tight]
    return {"name": q, "quad": q, "t": f"{q}，{QUAD_MEANING[q]}。需求{demand['label']}；供给{t_word}（{tight_why}）"}


def confirm_text(lead_score: float | None, ship: dict) -> str:
    if lead_score is None:
        return "领先指标不足"
    lead = "走强" if lead_score >= 0.25 else "转弱" if lead_score <= -0.25 else "方向不明"
    s = ship["label"]
    if s == "数据不足":
        return f"领先指标{lead}，出货数据不足"
    if lead == "走强":
        return "领先指标走强，出货已确认" if s == "走强" else "领先指标走强，出货尚未确认"
    if lead == "转弱":
        return "领先指标转弱，出货仍强，留意拐点" if s == "走强" else "领先指标转弱，出货也在走弱"
    return f"领先指标方向不明，出货{s}"


def environment(ai: dict, trad: dict, confirm: str) -> dict:
    if ai["quad"] and ai["quad"] == trad["quad"]:
        name = ai["quad"]
        head = f"{name}：AI 算力与传统芯片方向一致"
    elif ai["quad"] or trad["quad"]:
        name = "分化"
        head = f"分化：AI 算力{ai['name']}，传统芯片{trad['name']}"
    else:
        name, head = "数据不足", "数据还不够下判断"
    lines = [{"k": "AI 算力", "t": ai["t"]}, {"k": "传统芯片", "t": trad["t"]}, {"k": "领先 vs 同步", "t": confirm}]
    return {"name": name, "head": head, "lines": lines}
