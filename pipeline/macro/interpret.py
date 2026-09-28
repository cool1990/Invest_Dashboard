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
#   label    状态短语，放在卡片右上角
#   head     一句话
#   anchors  {指标 id: 对照的锚点}，第二层每个数旁边的那一列
#   level    给整体环境用的档位（增长 / 通胀：+1 / 0 / −1）
#   split    是否存在明显分歧

def _f(x: float | None, fmt: str = "{:.1f}") -> str:
    return "—" if x is None else fmt.format(x)


def _hi_lo(x: float, digits: int = 2, unit: str = " 个百分点") -> str:
    return f"{'高' if x >= 0 else '低'} {abs(x):.{digits}f}{unit}"


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
    u12, sahm = v.get("unrate_chg12"), v.get("sahm")
    # 产出只按已公布的数据判断，优先看核心 GDP（对私人国内购买者的最终销售 = 消费 + 固定投资）：
    # GDP 总量里的净出口、库存、政府季度波动大，和内需冷热关系不大，抢进口一个季度就能把总量压低或抬高。
    # 核心 GDP 与 GDP 分档不同时，以核心 GDP 定档，句子里写明总量被哪一块拉低或拉高。
    # 两个都没有时才用 GDPNow（模型预测）顶上。
    by_model = last is None and core is None and now is not None
    o, o_txt = _output_band(core if core is not None else last if last is not None else now)
    note, driver = "", ""
    if core is not None and last is not None and _output_band(last)[1] != o_txt:
        driver = gap_driver(last, core, contrib)
        note = f"GDP 总量 {round(last, 1):.1f}%" + (f"，{driver}" if driver else "")
    elif by_model:
        note = "按 GDPNow 预测"
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
    head = f"产出{o_txt}{f'（{note}）' if note else ''}、就业{l_txt}" + ("：增长分化" if split else "")
    bands = "潜在增速约 2%：≥ 2.5 偏强，1.5–2.5 接近潜在，< 1.5 低于潜在"
    if core is None:
        gdp_anchor = bands
    elif note:
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
    return {"label": label, "head": head, "anchors": anchors, "level": level, "split": split,
            "output": o_txt, "labor": l_txt}


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
    # 按页面显示的精度（两位小数）比较，句子里的差值和两个数对得上
    r2 = lambda x: None if x is None else round(x, 2)  # noqa: E731
    yoy, m3, sc, fwd = (r2(v.get(k)) for k in ("core_yoy", "core_3m", "supercore", "fwd"))
    nc = [(p, r2(x)) for p, x in v.get("nowcast") or []]  # 比最新官方数据更新的期间
    period = v.get("core_yoy_period")  # 最新官方同比的期间文字，例如「7 月」
    if yoy is None:
        return {"label": "数据不足", "head": "数据不足", "anchors": {}, "level": None, "split": False}
    gap = yoy - 2
    lvl_txt = "明显高于目标" if gap >= 0.75 else "略高于目标" if gap >= 0.25 else \
        "接近目标" if gap > -0.25 else "低于目标"
    level = 1 if gap >= 0.5 else -1 if gap <= -0.25 else 0
    mom = None if m3 is None else m3 - yoy
    mom_txt = "" if mom is None else "短期动能在加速" if mom > 0.3 else "短期动能在放缓" if mom < -0.3 else "短期动能持平"
    label = {1: "偏热", 0: "接近目标", -1: "偏冷"}[level]
    head = f"核心 PCE {lvl_txt}" + (f"，{mom_txt}" if mom_txt else "")
    anchors = {
        "core_yoy": f"目标 2%：{_hi_lo(gap)}",
        "supercore": "≤ 2.75% 与目标相容，≥ 3.5% 粘性强",
        "fwd": "2–2.5% 为锚定",
    }
    if mom is not None:
        anchors["core_3m"] = f"对同比 {yoy:.2f}%：{_hi_lo(mom)}，±0.3 以内为动能持平"
    nc_gap = nc_word = None
    if nc:
        nc_gap = nc[-1][1] - yoy
        nc_word = _nowcast_word(nc_gap)
        anchors["nowcast"] = (f"最新官方 {yoy:.2f}%{f'（{period}）' if period else ''}：{_hi_lo(nc_gap)}，"
                              f"算{nc_word}（±0.1 以内算持平，超过 0.3 算明显）")
    return {"label": label, "head": head, "anchors": anchors, "level": level, "split": False,
            "mom": mom, "nowcast_gap": nc_gap, "nowcast_word": nc_word}


def liquidity_state(v: dict) -> dict:
    sofr, ratio, nfci_v, hy_v = v.get("sofr_iorb"), v.get("reserves_ratio"), v.get("nfci"), v.get("hy")
    if sofr is not None and sofr > 0 or ratio is not None and ratio < 9:
        fund, fl = "偏紧", "watch"
    elif ratio is not None and ratio < 11:
        fund, fl = "平稳，但准备金在充足下限区间", "watch"
    else:
        fund, fl = "充裕", "ok"
    fin = None if nfci_v is None else "宽松" if nfci_v < -0.3 else "略松" if nfci_v < 0 else "偏紧"
    cred = None if hy_v is None else "利差很窄" if hy_v < 3 else "正常" if hy_v < 5 else "走阔"
    loose = fin in ("宽松", "略松") and fund != "偏紧"
    label = "宽松" if loose and fl == "ok" else "宽松，资金面需留意" if loose else "资金面偏紧" if fund == "偏紧" else "中性"
    head = f"资金面{fund}；金融条件{fin or '—'}；信用{cred or '—'}"
    anchors = {
        "sofr_iorb": "高于 0 说明回购资金偏紧",
        "reserves_ratio": "充足下限估计约 9–11%",
        "nfci": "0 为历史平均；< 0 略松，< −0.3 宽松",
        "hy": "3–5% 正常；< 3% 很窄，风险偏好高但缓冲薄",
    }
    return {"label": label, "head": head, "anchors": anchors, "level": None, "split": False}


def fiscal_state(v: dict) -> dict:
    d, chg, it = v.get("deficit"), v.get("deficit_chg12"), v.get("interest")
    imp = None if chg is None else "收缩（赤字在收窄，拖累增长）" if chg < -0.5 else \
        "扩张（赤字在扩大，托底增长）" if chg > 0.5 else "中性"
    press = None if it is None else "高" if it >= 3 else "偏高" if it >= 2.5 else "可控"
    label = f"脉冲{(imp or '—').split('（')[0]} · 偿债压力{press or '—'}"
    head = f"财政脉冲{imp or '—'}；偿债压力{press or '—'}"
    anchors = {
        "deficit": f"脉冲看 12 个月变化：较一年前 {_f(chg, '{:+.1f}')} 个百分点，超过 ±0.5 算收缩或扩张",
        "interest": "1990 年代峰值约 3.2%；≥ 2.5% 偏高，≥ 3% 高",
        "debt": "≥ 100% 偏高",
    }
    return {"label": label, "head": head, "anchors": anchors, "level": None, "split": False}


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
    label = f"立场{stance or '—'} · 市场定价{'加息' if n and n > 0.5 else '降息' if n and n < -0.5 else '不变' if n is not None else '—'}"
    head = f"当前立场{stance or '—'}"
    if path:
        head += f"，但市场定价到明年底{path}" if stance == "接近中性" and n and abs(n) > 0.5 else f"，市场定价到明年底{path}"
    if gap is not None and abs(gap) >= 0.25:
        head += f"，比点阵图{'鹰' if gap > 0 else '鸽'} {abs(gap) * 100:.0f}bp"
    anchors = {"real_policy": "中性约 0.5–1%：< 0 宽松，< 1 接近中性，1–2 偏紧，≥ 2 紧缩"}
    if cur is not None and ye is not None:
        anchors["year_end"] = f"当前 EFFR {cur:.2f}%：{moves(ye)}"
    if cur is not None and ny is not None:
        t = f"当前 EFFR {cur:.2f}%：{moves(ny)}"
        if gap is not None:
            t = f"点阵图 {dot_ny:.2f}%：{_hi_lo(gap * 100, 0, 'bp')}；" + t
        anchors["next_year"] = t
    return {"label": label, "head": head, "anchors": anchors, "level": None, "split": False, "hikes": n}


def environment(g: dict, i: dict, p: dict, liq: dict) -> dict:
    """增长 × 通胀定环境；货币路径与流动性作为条件。"""
    gl, il = g.get("level"), i.get("level")
    if gl is None or il is None:
        return {"name": "数据不足", "head": "数据不足", "sub": ""}
    names = {
        (1, 1): "过热", (0, 1): "通胀粘性", (-1, 1): "滞胀风险",
        (1, 0): "稳健扩张", (0, 0): "金发姑娘", (-1, 0): "放缓",
        (1, -1): "复苏", (0, -1): "低通胀", (-1, -1): "衰退风险",
    }
    name = names[(gl, il)]
    g_txt = g.get("head", "增长" + g.get("label", "")).split("：")[0]
    if g.get("split"):
        g_txt = f"增长分化（{g_txt}）"
    head = f"{name}：{g_txt}，通胀{i['label']}"
    mom = i.get("mom")
    if mom is not None and mom > 0.3:
        head += "且短期在加速"
    elif mom is not None and mom < -0.3:
        head += "但短期在放缓"
    # Nowcast 只是模型预测：差得小就只说「略高 / 略低」，超过 0.3 个百分点才说回升或回落
    word = i.get("nowcast_word")
    if word in ("略高", "略低"):
        head += f"（Nowcast {word}）"
    elif word == "明显偏高":
        head += "，Nowcast 预计明显回升"
    elif word == "明显偏低":
        head += "，Nowcast 预计明显回落"
    sub = "；".join(x for x in (p.get("head"), "流动性" + liq.get("label", "")) if x)
    return {"name": name, "head": head, "sub": sub}
