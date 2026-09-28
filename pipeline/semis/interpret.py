"""规则：半导体供需五个维度的状态、整体景气位置、分环节状态、观察清单。

需求端：AI 算力、传统终端；供给端：产能、库存、价格。

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
IP_UP, IP_DOWN = 5.0, 0.0  # 美国半导体工业产出同比 %（产能下参考）


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
    return _state(label, level, why, summary, anchors, score=score, yoy=capex, accel=accel)


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
    return _state(label, level, why, summary, anchors, score=score, yoy=analog, accel=accel)


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
    return _state(label, level, why, summary, anchors, ppi=ppi, dram=dram)


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
    return _state(label, level, why, summary, anchors, equip=equip)


# ---------------------------------------------------------------------------
# 景气位置：方向（上行 / 下行 / 震荡）× 阶段（早期 / 中期 / 后期）
#
# 方向看这条线的需求档位，出货确认作校验。阶段按经典半导体周期，由需求、库存、价格、产能
# 四个维度各投一票，票最多的阶段胜出，平票取中期。库存、价格、产能两条线共用，只有需求不同。

HIGH_GROWTH = {"ai": 40.0, "trad": 15.0}  # 需求同比到这个水平算「高位」
LOW_GROWTH = {"ai": 30.0, "trad": 5.0}  # 低于这个水平还在加速算「低位起步」
PPI_LOW, PPI_HIGH = 5.0, 10.0  # 半导体 PPI 同比：低于 5% 价格还没涨起来，高于 10% 已在高位
STAGES = ("早期", "中期", "后期")
CYCLE = ("上行早期", "上行中期", "上行后期", "下行早期", "下行中期", "下行后期")


def overall_phase(ai: dict, trad: dict) -> tuple[str | None, str]:
    """方向：AI 算力与传统终端两个需求档位取平均。返回 (方向, 需求分化的说明)。"""
    levels = [d["level"] for d in (ai, trad) if d["label"] != "数据不足"]
    if not levels:
        return None, ""
    avg = sum(levels) / len(levels)
    split = ""
    if len(levels) == 2 and ai["level"] != trad["level"]:
        split = f"需求内部分化：AI 算力{ai['label']}，传统终端{trad['label']}"
    if avg >= 0.5:
        return "上行", split
    if avg <= -0.5:
        return "下行", split
    return "震荡", split


def _demand_vote(phase: str, d: dict, line: str) -> tuple[str, str] | None:
    yoy, accel, lab = d.get("yoy"), d.get("accel"), d["label"]
    what = "云厂商资本开支" if line == "ai" else "模拟与 MCU 营收"
    num = f"{what}同比 {yoy:+.1f}%" + (f"，比上季{'抬升' if accel >= 0 else '回落'} {abs(accel):.1f} 个百分点" if accel is not None else "") if yoy is not None else f"需求{lab}"
    if phase == "上行":
        if lab == "放缓" or (yoy is not None and accel is not None and accel < 0 and yoy > HIGH_GROWTH[line]):
            return "后期", f"{num}：增速从高位回落"
        if yoy is not None and yoy < LOW_GROWTH[line] and (accel or 0) > 0:
            return "早期", f"{num}：从低位加速"
        return "中期", f"{num}：需求{lab}"
    if phase == "下行":
        if lab == "放缓":
            return "早期", f"{num}：需求刚开始降温"
        if accel is not None and accel > 0:
            return "后期", f"{num}：跌幅在收窄"
        return "中期", f"{num}：需求仍在收缩"
    return None


def _supply_votes(phase: str, inv: dict, price: dict, cap: dict) -> list[tuple[str, str, str]]:
    out = []
    il, gap = inv["label"], inv.get("gap")
    gap_t = f"，出货比库存{'快' if gap >= 0 else '慢'} {abs(gap):.1f} 个百分点" if gap is not None else ""
    if il != "数据不足":
        if phase == "上行":
            st = {"被动去库": "早期", "主动去库": "早期", "主动补库": "中期", "被动补库": "后期"}[il]
        else:
            st = {"主动补库": "早期", "被动补库": "早期", "被动去库": "后期"}.get(il) or ("后期" if (gap or 0) >= 0 else "中期")
        out.append(("库存", st, f"{il}{gap_t}"))
    pl, ppi = price["label"], price.get("ppi")
    if pl != "数据不足":
        ppi_t = f"（PPI 同比 {ppi:+.1f}%）" if ppi is not None else ""
        if phase == "上行":
            if pl == "涨价":
                st = "早期" if ppi is not None and ppi < PPI_LOW else "中期"
            elif pl == "企稳":
                st = "后期" if ppi is not None and ppi > PPI_HIGH else "早期"
            else:
                st = "早期"
            why = {"后期": "价格已在高位但涨不动了", "中期": "价格在涨", "早期": "价格刚开始抬头"}[st]
        else:
            if pl == "跌价":
                st, why = "中期", "价格在跌"
            elif pl == "企稳":
                st, why = ("早期", "价格还在高位，刚停涨") if ppi is not None and ppi > PPI_HIGH else ("后期", "跌价收窄到企稳")
            else:
                st, why = "早期", "价格还在涨"
        out.append(("价格", st, f"存储{pl}{ppi_t}：{why}"))
    cl, eq = cap["label"], cap.get("equip")
    if cl != "数据不足":
        eq_t = f"（设备商营收同比 {eq:+.1f}%）" if eq is not None else ""
        if phase == "上行":
            st = {"收缩": "早期", "平稳": "中期", "扩张": "后期"}[cl]
        else:
            st = {"扩张": "早期", "平稳": "中期", "收缩": "后期"}[cl]
        why = {"扩张": "扩产在加码，1–2 个季度后供给增加", "平稳": "扩产节奏平稳", "收缩": "扩产在收缩"}[cl]
        out.append(("产能", st, f"{cl}{eq_t}：{why}"))
    return out


STAGE_MEANING = {
    "上行早期": "需求刚回升、库存还在消化、产能没扩，通常是周期里弹性最大的一段",
    "上行中期": "需求扩张、厂商补库存、价格在涨，量价齐升",
    "上行后期": "需求仍在扩张但增速见顶、价格涨不动、产能加码，要防见顶",
    "下行早期": "需求降温、库存被动堆积、产能还在扩，价格开始承压",
    "下行中期": "需求收缩、厂商砍库存、价格在跌",
    "下行后期": "去库接近尾声、跌价收窄、产能收缩，底部在形成",
}


DIM_NAMES = {"ai": "AI 算力", "trad": "传统终端"}


def position(ai: dict, trad: dict, inv: dict, price: dict, cap: dict) -> dict:
    """半导体整体的位置：方向看需求，阶段由 AI 算力、传统终端、产能、库存、价格五个维度各投一票。

    返回 {phase, stage, name, head, meaning, split, votes[{dim, stage, why}], counts}。
    """
    phase, split = overall_phase(ai, trad)
    base = {"phase": phase, "stage": None, "split": split, "votes": [], "counts": {}}
    if phase is None:
        return {**base, "name": "数据不足", "head": "需求数据不足，定不了位置", "meaning": ""}
    if phase == "震荡":
        return {**base, "name": "震荡", "head": "震荡：需求没有明确方向",
                "meaning": "需求不扩张也不收缩，等待方向选择；看观察清单里哪边先出信号"}
    votes = []
    for line, d in (("ai", ai), ("trad", trad)):
        if d["label"] == "数据不足":
            continue
        dv = _demand_vote(phase, d, line)
        if dv:
            votes.append({"dim": DIM_NAMES[line], "stage": dv[0], "why": dv[1]})
    votes += [{"dim": dim, "stage": st, "why": why} for dim, st, why in _supply_votes(phase, inv, price, cap)]
    counts = {s: sum(1 for v in votes if v["stage"] == s) for s in STAGES}
    top = max(counts.values())
    winners = [s for s in STAGES if counts[s] == top]
    stage = winners[0] if len(winners) == 1 else "中期"
    name = phase + stage
    tally = "、".join(f"{k} {counts[k]} 票" for k in STAGES if counts[k])
    return {**base, "stage": stage, "name": name, "votes": votes, "counts": counts,
            "head": f"半导体整体处于{name}", "meaning": STAGE_MEANING[name], "tally": tally}


# ---------------------------------------------------------------------------
# 分环节：每个环节用一个主指标的同比及其变化定状态

SEG_ACCEL = 0.0  # 同比比上期抬升多少算「在抬升」（个百分点）


def segment_state(yoy: float | None, prev: float | None) -> str:
    if yoy is None:
        return "数据不足"
    rising = prev is not None and yoy - prev > SEG_ACCEL
    if yoy >= 0:
        if prev is None:
            return "上行"
        return "上行加速" if rising else "上行放缓"
    if prev is None:
        return "下行"
    return "触底回升" if rising else "下行"


# 观察清单：最能改变位置判断的几个数。up = 上行时出现什么说明见顶或转下行；down = 下行时出现什么说明见底
WATCH = [
    ("capex_yoy", "云厂商资本开支同比",
     f"连续两季回落，或跌破 {CAPEX_UP:g}%：AI 需求降档，是见顶的第一信号",
     f"止跌回升并重回 {CAPEX_UP:g}% 以上：AI 需求重启"),
    ("inv_gap", "出货 − 库存",
     "转负：库存开始堆积（被动补库），通常领先见顶 1–3 个月",
     "由负转正：去库接近尾声，通常领先价格见底 1–3 个月"),
    ("dram_chg", "DRAM 现货变化",
     f"30 天跌超 {DRAM_30:g}%：存储价格转跌",
     f"30 天涨超 {DRAM_30:g}%：价格见底回升"),
    ("equip_yoy", "设备商营收同比",
     "继续抬升到 30% 以上：扩产加码，1–2 个季度后供给压力更大",
     "由负转正：扩产重启，说明厂商看好后续需求"),
    ("korea_yoy", "韩国芯片出口同比",
     "连续两个月回落，或前 10/20 日明显放缓：出货确认转弱",
     "转正并加速：出货回暖得到确认"),
    ("analog_yoy", "模拟与 MCU 营收同比",
     f"比上季回落，或跌破 {ANALOG_UP:g}%：传统芯片需求降温",
     "由负转正：传统芯片周期见底"),
]


def watch_list(phase: str | None, now: dict[str, str]) -> list[dict]:
    """phase 为上行时写转弱信号，下行时写见底信号，震荡两边都写。now：{id: 当前值文字}。"""
    out = []
    for key, name, up, down in WATCH:
        if key not in now:
            continue
        if phase == "上行":
            sig = up
        elif phase == "下行":
            sig = down
        else:
            sig = f"向上：{down}；向下：{up}"
        out.append({"id": key, "name": name, "now": now[key], "signal": sig})
    return out
