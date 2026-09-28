"""规则：维度状态与整体环境。

每个维度按经济锚点判断，而不是和历史平均比。阈值都是常用的经验值，写在各函数里，方便以后改。
只用已经公布的数据定状态；模型预测（GDPNow、克利夫兰联储 Nowcast）只作参考，
在句子里按它和官方数据差多少来措辞，不改写状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ctx:
    v: float
    extra: dict = field(default_factory=dict)


Result = tuple[str, str]


def ann(mom_pct: float) -> float:
    return ((1 + mom_pct / 100) ** 12 - 1) * 100


def vs_target(x: float, target: float = 2.0, band: float = 0.25) -> str:
    if x > target + band:
        return f"高于 {target:g}% 目标"
    if x < target - band:
        return f"低于 {target:g}% 目标"
    return f"接近 {target:g}% 目标"


# ---- 环比通胀：情景试算里给「核心 PCE 环比」「核心 CPI 环比」定级 ----
def core_mom(c: Ctx, hi: float = 3.0, mid: float = 2.5, target: float = 2.0) -> Result:
    """单月噪音大：级别按 3 个月均值（extra avg3）的折年率定，单月只作补充。"""
    avg3 = c.extra.get("avg3")
    if avg3 is None:
        a = ann(c.v)
        return f"单月折年约 {a:.1f}%，{vs_target(a, target)}", "alert" if a >= hi else "watch" if a >= mid else "ok"
    a3, a1 = ann(avg3), ann(c.v)
    t = f"3 个月均值 {avg3:.2f}% 折年约 {a3:.1f}%，{vs_target(a3, target)}；单月 {c.v:.2f}% 折年约 {a1:.1f}%"
    return t, "alert" if a3 >= hi else "watch" if a3 >= mid else "ok"


def core_cpi_mom(c: Ctx) -> Result:
    # CPI 长期比 PCE 高约 0.3–0.5 个百分点，阈值相应放宽
    return core_mom(c, hi=3.5, mid=2.8, target=2.0)




# ---------------------------------------------------------------------------
# 维度状态
#
# 每个函数拿到一组数（缺的为 None），返回：
#   label    状态标签，放在卡片右上角
#   why      理由，每条 {"k": 小结论, "t": 用哪几个数、对照什么锚点得出的}；标签不在理由里重复
#   head     理由连成一句，写进判断变化日志
#   summary  给顶部总结用的一句白话，不带数字（数字都在理由和依据里）
#   anchors  {指标 id: 对照的锚点}，第二层每个数旁边的那一列
#   level    给整体环境用的档位（增长 / 通胀：+1 / 0 / −1）
#   split    是否存在明显分歧

def _f(x: float | None, fmt: str = "{:.1f}") -> str:
    return "—" if x is None else fmt.format(x)


def _hi_lo(x: float, digits: int = 2, unit: str = " 个百分点") -> str:
    return f"{'高' if x >= 0 else '低'} {abs(x):.{digits}f}{unit}"


def _why(*items: tuple[str, str] | None) -> list[dict]:
    return [{"k": k, "t": t} for k, t in (x for x in items if x) if t]


def _head(why: list[dict]) -> str:
    return "；".join(f"{w['k']}：{w['t']}" for w in why) or "数据不足"


def _wan(k: float) -> str:
    """千人 → 万人。"""
    return f"{k / 10:.1f} 万"


def _output_band(x: float | None) -> tuple[int | None, str]:
    """季环比年化增速对约 2% 的潜在增速分档。按公布精度（一位小数）比：页面显示 1.5 就按 1.5 算。"""
    if x is None:
        return None, "数据不足"
    x = round(x, 1)
    if x >= 2.5:
        return 1, "偏强"
    if x >= 1.5:
        return 0, "接近潜在"
    if x >= 0.5:
        return -1, "低于潜在"
    return -1, "停滞或收缩"


# 各档对 2% 潜在增速的说法
VS_POTENTIAL = {"偏强": "高于", "接近潜在": "接近", "低于潜在": "低于", "停滞或收缩": "远低于"}

# GDP 里和内需冷热关系不大、季度波动又大的几块：GDP 与核心 GDP 分档不同时，用它们解释差在哪
NOISE = (("nx", "净出口"), ("inv", "库存"), ("gov", "政府"))


def gap_driver(gdp: float, core: float, contrib: dict) -> str:
    """GDP 总量比核心 GDP 高（低）时，在净出口、库存、政府里找往同一方向贡献最大的那项。"""
    up = gdp > core
    parts = [(name, contrib[k]) for k, name in NOISE if contrib.get(k) is not None and (contrib[k] > 0) == up]
    if not parts:
        return ""
    name, c = max(parts, key=lambda p: abs(p[1]))
    return f"{name}{'拉高' if c > 0 else '拖累'} {abs(c):.1f} 个百分点"


def growth_state(v: dict) -> dict:
    now, last, core = v.get("gdpnow"), v.get("gdp_q"), v.get("core_gdp")
    contrib = v.get("contrib") or {}
    nfp3 = v.get("nfp3")
    ur, u12, sahm = v.get("unrate"), v.get("unrate_chg12"), v.get("sahm")
    # 产出只按已公布的数据判断，优先看核心 GDP（对私人国内购买者的最终销售 = 消费 + 固定投资）：
    # GDP 总量里的净出口、库存、政府季度波动大，和内需冷热关系不大，抢进口一个季度就能把总量压低或抬高。
    # 核心 GDP 与 GDP 分档不同时，以核心 GDP 定档，理由里写明总量被哪一块拉低或拉高。
    # 两个都没有时才用 GDPNow（模型预测）顶上。
    by_model = last is None and core is None and now is not None
    o, o_txt = _output_band(core if core is not None else last if last is not None else now)
    driver, differs = "", core is not None and last is not None and _output_band(last)[1] != o_txt
    if o is None:
        o_why = ""
    elif core is not None:
        o_why = f"核心 GDP（消费 + 固定投资）{round(core, 1):.1f}%，{VS_POTENTIAL[o_txt]}约 2% 的潜在增速"
        if differs:
            driver = gap_driver(last, core, contrib)
            lo = last < core
            o_why += (f"；GDP 总量{'只有' if lo else '达到'} {round(last, 1):.1f}%，"
                      + (f"主要是{driver}" if driver else "差在净出口、库存或政府")
                      + f"，不代表内需{'弱' if lo else '强'}")
    elif by_model:
        o_why = f"还没有上季 GDP，暂按 GDPNow 模型预测 {round(now, 1):.1f}%，{VS_POTENTIAL[o_txt]}约 2% 的潜在增速"
    else:
        o_why = f"上季实际 GDP {round(last, 1):.1f}%，{VS_POTENTIAL[o_txt]}约 2% 的潜在增速"

    if nfp3 is None:
        l, l_txt = None, "数据不足"
    elif (sahm is not None and sahm >= 0.5) or (u12 is not None and u12 >= 0.5) or nfp3 < 0:
        l, l_txt = -1, "恶化"
    elif nfp3 >= 150 and (u12 is None or u12 <= 0.2):
        l, l_txt = 1, "强"
    elif nfp3 >= 50:
        l, l_txt = 0, "降温"
    else:
        l, l_txt = -1, "疲弱"
    u_txt = ""
    if ur is not None and u12 is not None:
        u_txt = f"失业率 {ur:.1f}%，" + ("与一年前持平" if abs(u12) < 0.05 else f"比一年前{_hi_lo(u12, 1)}")
    elif u12 is not None:
        u_txt = "失业率与一年前持平" if abs(u12) < 0.05 else f"失业率比一年前{_hi_lo(u12, 1)}"
    if sahm is not None:
        u_txt += ("，" if u_txt else "") + (f"Sahm 规则 {sahm:.2f} 已触发（≥ 0.5）" if sahm >= 0.5 else "Sahm 规则未触发")
    if nfp3 is None:
        l_why = ""
    else:
        pace = {"强": "高于 15 万", "降温": "只够维持失业率不变（约 5–10 万）",
                "疲弱": "低于维持失业率不变所需的约 5 万"}.get(l_txt, "为负" if nfp3 < 0 else "")
        l_why = f"非农近 3 个月月均新增 {_wan(nfp3)}人" + (f"，{pace}" if pace else "") + (f"；{u_txt}" if u_txt else "")

    if o is None or l is None:
        level = o if l is None else l
        split = False
    else:
        level, split = min(o, l) if o != l else o, o != l
    if split:
        label = "分化"
    else:
        label = {1: "扩张偏强", 0: "接近潜在", -1: "放缓"}.get(level, "数据不足")
        if o_txt == "停滞或收缩" or l_txt == "恶化":
            label = "收缩风险"
    why = _why((f"产出{o_txt}", o_why), (f"就业{l_txt}", l_why))
    summary = "" if o is None and l is None else \
        f"产出{o_txt}，但就业{l_txt}" if split else f"产出{o_txt}、就业{l_txt}"
    bands = "潜在增速约 2%：≥ 2.5 偏强，1.5–2.5 接近潜在，< 1.5 低于潜在"
    if core is None:
        gdp_anchor = bands
    elif differs:
        gdp_anchor = "与核心 GDP 分档不同，以核心为准" + (f"：{driver}" if driver else "")
    else:
        gdp_anchor = "与核心 GDP 分档相同"
    anchors = {
        "core_gdp": bands,
        "gdp_q": gdp_anchor,
        "real_pce": "月度消费，两次 GDP 之间的更新；不进判断",
        "core_capex": "设备投资的先行指标；不进判断",
        "nfp3": "维持失业率不变约需 50–100 千人；≥ 150 且失业率没升为强",
        "unrate": f"较一年前 {_f(u12, '{:+.1f}')} 个百分点，Sahm {_f(sahm, '{:.2f}')}（0.5 触发）",
    }
    if now is not None:
        t = "模型预测，不进判断" if not by_model else "模型预测；还没有上季实际值，暂用它判断产出"
        if last is not None:
            t += f"；比上季实际{_hi_lo(now - last, 1)}"
            if abs(now - last) > 2:
                t += "，要等 GDP 初值确认"
        anchors["gdpnow"] = t
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": level, "split": split, "output": o_txt, "labor": l_txt}


def _nowcast_word(gap: float) -> str:
    """Nowcast 与最新官方同比的差，按大小措辞：±0.1 以内持平，0.1–0.3 略高/略低，再大才说明显。"""
    if gap > 0.3:
        return "明显偏高"
    if gap > 0.1:
        return "略高"
    if gap < -0.3:
        return "明显偏低"
    if gap < -0.1:
        return "略低"
    return "持平"


def inflation_state(v: dict) -> dict:
    # 按页面显示的精度（两位小数）比较，理由里的差值和两个数对得上
    r2 = lambda x: None if x is None else round(x, 2)  # noqa: E731
    yoy, m3, sc, fwd = (r2(v.get(k)) for k in ("core_yoy", "core_3m", "supercore", "fwd"))
    nc = [(p, r2(x)) for p, x in v.get("nowcast") or []]  # 比最新官方数据更新的期间
    period = v.get("core_yoy_period")  # 最新官方同比的期间文字，例如「7 月」
    if yoy is None:
        return {"label": "数据不足", "why": [], "head": "数据不足", "summary": "", "anchors": {},
                "level": None, "split": False}
    gap = yoy - 2
    lvl_txt = "明显高于目标" if gap >= 0.75 else "略高于目标" if gap >= 0.25 else \
        "接近目标" if gap > -0.25 else "低于目标"
    level = 1 if gap >= 0.5 else -1 if gap <= -0.25 else 0
    mom = None if m3 is None else m3 - yoy
    mom_k = None if mom is None else "短期在加速" if mom > 0.3 else "短期在放缓" if mom < -0.3 else "短期动能持平"
    label = {1: "偏热", 0: "接近目标", -1: "偏冷"}[level]
    lvl_why = f"核心 PCE 同比 {yoy:.2f}%{f'（{period}）' if period else ''}，比 2% 目标{_hi_lo(gap)}"
    mom_why = None if mom is None else \
        f"3 个月年化 {m3:.2f}%，比同比{_hi_lo(mom)}，{'在 ±0.3 以内' if abs(mom) <= 0.3 else '超出 ±0.3'}"
    sc_why = None if sc is None else (
        "服务通胀已降温" if sc <= 2.75 else "服务通胀偏高" if sc < 3.5 else "服务通胀粘",
        f"超级核心 PCE（服务除能源、住房，跟工资走）{sc:.2f}%，≤ 2.75% 才与 2% 目标相容")
    fwd_why = None if fwd is None else (
        "长期预期锚定" if 2 <= fwd <= 2.5 else "长期预期上移" if fwd > 2.5 else "长期预期偏低",
        f"5y5y 远期通胀预期 {fwd:.2f}%，2–2.5% 为锚定")
    anchors = {
        "core_yoy": f"目标 2%：{_hi_lo(gap)}",
        "supercore": "≤ 2.75% 与目标相容，≥ 3.5% 粘性强",
        "fwd": "2–2.5% 为锚定",
    }
    if mom is not None:
        anchors["core_3m"] = f"对同比 {yoy:.2f}%：{_hi_lo(mom)}，±0.3 以内为动能持平"
    # Nowcast 是模型预测，不改标签；只有和官方差超过 0.3 个百分点才写进理由
    nc_gap = nc_word = nc_why = None
    if nc:
        nc_gap = nc[-1][1] - yoy
        nc_word = _nowcast_word(nc_gap)
        anchors["nowcast"] = (f"最新官方 {yoy:.2f}%{f'（{period}）' if period else ''}：{_hi_lo(nc_gap)}，"
                              f"算{nc_word}（±0.1 以内算持平，超过 0.3 算明显）")
        if nc_word in ("明显偏高", "明显偏低"):
            nc_why = (f"模型预计{'回升' if nc_gap > 0 else '回落'}",
                      f"克利夫兰联储 Nowcast 预计 {nc[-1][0]} {nc[-1][1]:.2f}%，比最新官方{_hi_lo(nc_gap)}；"
                      "模型预测，不改标签")
    why = _why((lvl_txt, lvl_why), (mom_k, mom_why) if mom_k else None, sc_why, fwd_why, nc_why)
    flat = {1: "，短期没有回落", -1: "，短期没有回升"}.get(level, "，短期平稳")
    summary = f"核心通胀{lvl_txt}" + {"短期在加速": "，且短期在加速", "短期在放缓": "，但短期在放缓",
                                     "短期动能持平": flat}.get(mom_k or "", "")
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": level, "split": False, "mom": mom, "nowcast_gap": nc_gap, "nowcast_word": nc_word}


def liquidity_state(v: dict) -> dict:
    sofr, ratio, nfci_v, hy_v = v.get("sofr_iorb"), v.get("reserves_ratio"), v.get("nfci"), v.get("hy")
    if sofr is not None and sofr > 0 or ratio is not None and ratio < 9:
        fund, fl = "偏紧", "watch"
    elif ratio is not None and ratio < 11:
        fund, fl = "平稳，但准备金接近下限", "watch"
    else:
        fund, fl = "充裕", "ok"
    fin = None if nfci_v is None else "宽松" if nfci_v < -0.3 else "略松" if nfci_v < 0 else "偏紧"
    cred = None if hy_v is None else "很窄" if hy_v < 3 else "正常" if hy_v < 5 else "走阔"
    loose = fin in ("宽松", "略松") and fund != "偏紧"
    label = "宽松，资金面需留意" if loose and fl != "ok" else "宽松" if loose else \
        "资金面偏紧" if fund == "偏紧" else "中性"
    # 标签已经写了结论，理由的小标题只写看的是哪一块，结论放在句尾
    fund_t = "；".join(x for x in (
        None if sofr is None else f"SOFR 比 IORB {_hi_lo(sofr, 0, ' 基点')}，"
                                  + ("回购资金偏紧" if sofr > 0 else "回购资金不紧（高于 0 才算紧）"),
        None if ratio is None else f"准备金占 GDP {ratio:.1f}%，" + (
            "已低于 9–11% 的充足下限" if ratio < 9 else "已在 9–11% 的充足下限区间" if ratio < 11
            else "高于 9–11% 的充足下限")) if x)
    why = _why(
        ("资金面", fund_t) if sofr is not None or ratio is not None else None,
        ("金融条件", f"芝加哥联储 NFCI {nfci_v:.2f}，" + {"宽松": "低于 −0.3，宽松", "略松": "在 −0.3 到 0 之间，略松",
                                                     "偏紧": "高于 0（历史平均），偏紧"}[fin]) if fin else None,
        ("信用", f"高收益债利差 {hy_v:.2f}%，" + {"很窄": "低于 3%，很窄；风险偏好高，但出事时缓冲薄",
                                              "正常": "在 3–5% 的正常区间", "走阔": "高于 5%，已走阔"}[cred]) if cred else None,
    )
    summary = "，".join(x for x in (fin and f"金融条件{fin}", cred and f"信用利差{cred}") if x)
    if sofr is not None or ratio is not None:
        summary += ("；" if summary else "") + f"资金面{fund}"
    anchors = {
        "sofr_iorb": "高于 0 说明回购资金偏紧",
        "reserves_ratio": "充足下限估计约 9–11%",
        "nfci": "0 为历史平均；< 0 略松，< −0.3 宽松",
        "hy": "3–5% 正常；< 3% 很窄，风险偏好高但缓冲薄",
    }
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": None, "split": False}


def fiscal_state(v: dict) -> dict:
    d, chg, it, debt = v.get("deficit"), v.get("deficit_chg12"), v.get("interest"), v.get("debt")
    imp = None if chg is None else "收缩" if chg < -0.5 else "扩张" if chg > 0.5 else "中性"
    press = None if it is None else "高" if it >= 3 else "偏高" if it >= 2.5 else "可控"
    label = f"脉冲{imp or '—'} · 偿债压力{press or '—'}"
    imp_t = None
    if imp:
        imp_t = (f"赤字率 {d:.1f}% GDP（滚动 12 个月），" if d is not None else "赤字率") + \
            ("与一年前差不多" if imp == "中性" else f"比一年前{'收窄' if chg < 0 else '扩大'} {abs(chg):.1f} 个百分点") + \
            {"收缩": "，政府少花钱，对增长是拖累", "扩张": "，政府多花钱，托底增长", "中性": "，对增长影响不大"}[imp]
    press_t = None
    if press:
        press_t = f"利息支出占 GDP {it:.2f}%，" + ("已超过 1990 年代峰值约 3.2%" if it >= 3.2 else
                                                   f"1990 年代峰值约 3.2%，{press}") + \
            (f"；联邦债务占 GDP {debt:.0f}%" if debt is not None else "")
    why = _why(("财政脉冲", imp_t) if imp else None, ("偿债压力", press_t) if press else None)
    summary = "；".join(x for x in (
        {"收缩": "赤字在收窄，对增长是拖累", "扩张": "赤字在扩大，托底增长", "中性": "赤字变化不大，对增长影响中性"}.get(imp or ""),
        press and ("利息负担已超过 1990 年代峰值" if it >= 3.2 else f"偿债压力{press}")) if x)
    anchors = {
        "deficit": f"脉冲看 12 个月变化：较一年前 {_f(chg, '{:+.1f}')} 个百分点，超过 ±0.5 算收缩或扩张",
        "interest": "1990 年代峰值约 3.2%；≥ 2.5% 偏高，≥ 3% 高",
        "debt": "≥ 100% 偏高",
    }
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": None, "split": False}


def policy_state(v: dict) -> dict:
    real, cur, ye, ny, dot_ny = (v.get(k) for k in ("real_policy", "current", "year_end", "next_year", "dot_next"))
    stance = None if real is None else "宽松" if real < 0 else "接近中性" if real < 1 else "偏紧" if real < 2 else "紧缩"

    def moves(x: float) -> str:
        n = (x - cur) / 0.25
        return "不变" if abs(n) <= 0.5 else f"{'加息' if n > 0 else '降息'}约 {abs(n):.1f} 次"

    path, n = None, None
    if cur is not None and ny is not None:
        n = (ny - cur) / 0.25
        path = "按兵不动" if abs(n) <= 0.5 else moves(ny)
    gap = None if ny is None or dot_ny is None else ny - dot_ny
    priced = "加息" if n and n > 0.5 else "降息" if n and n < -0.5 else "不变" if n is not None else "—"
    label = f"立场{stance or '—'} · 市场定价{priced}"
    stance_t = None
    if real is not None:
        stance_t = f"实际政策利率（EFFR − 核心 PCE 同比）{real:.2f}%，" + (
            "低于 0，政策在刺激" if stance == "宽松" else
            "略低于约 0.5–1% 的中性估计，谈不上紧" if real < 0.5 else
            "在约 0.5–1% 的中性估计之内" if stance == "接近中性" else
            "高于约 0.5–1% 的中性估计，在压需求" if stance == "偏紧" else "比中性估计高 1 个百分点以上")
    path_t = None
    if path:
        path_t = f"当前 EFFR {cur:.2f}%，市场隐含" + (f"年底 {ye:.2f}%、" if ye is not None else "") + \
            f"明年底 {ny:.2f}%，相当于到明年底{path}"
        if gap is not None and abs(gap) >= 0.25:
            path_t += f"；比点阵图 {dot_ny:.2f}% {'鹰' if gap > 0 else '鸽'} {abs(gap) * 100:.0f}bp"
    why = _why(("当前立场", stance_t) if stance else None, ("市场路径", path_t) if path else None)
    summary = "；".join(x for x in (
        stance and f"政策立场{stance}",
        path and (f"市场定价到明年底{path}"
                  + (f"，比美联储点阵图更{'鹰' if gap > 0 else '鸽'}" if gap is not None and abs(gap) >= 0.25 else ""))) if x)
    anchors = {"real_policy": "中性约 0.5–1%：< 0 宽松，< 1 接近中性，1–2 偏紧，≥ 2 紧缩"}
    if cur is not None and ye is not None:
        anchors["year_end"] = f"当前 EFFR {cur:.2f}%：{moves(ye)}"
    if cur is not None and ny is not None:
        t = f"当前 EFFR {cur:.2f}%：{moves(ny)}"
        if gap is not None:
            t = f"点阵图 {dot_ny:.2f}%：{_hi_lo(gap * 100, 0, 'bp')}；" + t
        anchors["next_year"] = t
    return {"label": label, "why": why, "head": _head(why), "summary": summary, "anchors": anchors,
            "level": None, "split": False, "hikes": n}


# 增长 × 通胀九宫格：每格一句白话，说明这个名字是什么意思
REGIMES = {
    (1, 1): ("过热", "增长强于潜在，通胀高于目标"),
    (0, 1): ("通胀粘性", "增长大致在潜在水平，通胀仍高于目标"),
    (-1, 1): ("滞胀风险", "增长放缓，通胀仍高于目标"),
    (1, 0): ("稳健扩张", "增长强于潜在，通胀接近目标"),
    (0, 0): ("金发姑娘", "增长大致在潜在水平，通胀接近目标"),
    (-1, 0): ("放缓", "增长低于潜在，通胀接近目标"),
    (1, -1): ("复苏", "增长强于潜在，通胀仍低于目标"),
    (0, -1): ("低通胀", "增长大致在潜在水平，通胀低于目标"),
    (-1, -1): ("衰退风险", "增长放缓，通胀低于目标"),
}


def environment(g: dict, i: dict, p: dict, liq: dict, f: dict | None = None) -> dict:
    """增长 × 通胀定环境名；下面四行分别写经济、流动性、财政、货币。"""
    gl, il = g.get("level"), i.get("level")
    if gl is None or il is None:
        return {"name": "数据不足", "head": "数据不足", "lines": []}
    name, meaning = REGIMES[(gl, il)]
    econ = "；".join(x for x in (g.get("summary"), i.get("summary")) if x)
    lines = [{"k": k, "t": t} for k, t in (
        ("经济", econ), ("流动性", liq.get("summary")), ("财政", (f or {}).get("summary")), ("货币", p.get("summary")),
    ) if t]
    return {"name": name, "head": f"{name}：{meaning}", "lines": lines}
