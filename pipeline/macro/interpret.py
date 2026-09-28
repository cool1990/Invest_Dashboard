"""规则生成的解读。

每条规则拿到一个 Ctx（最新值、前值、若干期前的值、2000 年以来分位、额外字段），
返回 (一句话, 级别)。级别：alert（警示）/ watch（关注）/ ok（正常）。
阈值都是常用的经验值，写在各函数里，方便以后改。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ctx:
    v: float
    prev: float | None = None
    past: float | None = None  # trend_n 期之前
    pct: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def chg(self) -> float | None:
        return None if self.past is None else self.v - self.past


Result = tuple[str, str]


def _dir(x: float | None, up: str, down: str, flat: str = "持平", eps: float = 1e-9) -> str:
    if x is None:
        return ""
    return up if x > eps else down if x < -eps else flat


def ann(mom_pct: float) -> float:
    return ((1 + mom_pct / 100) ** 12 - 1) * 100


def vs_target(x: float, target: float = 2.0, band: float = 0.25) -> str:
    if x > target + band:
        return f"高于 {target:g}% 目标"
    if x < target - band:
        return f"低于 {target:g}% 目标"
    return f"接近 {target:g}% 目标"


# ---- 增长 ----
def gdp(c: Ctx, what: str = "增长") -> Result:
    v = c.v
    if v >= 3:
        return f"{what}明显高于约 2% 的潜在增速，经济偏热", "watch"
    if v >= 1.8:
        return f"{what}在潜在增速附近或略高，扩张稳健", "ok"
    if v >= 1:
        return f"{what}低于潜在增速，经济在放缓", "watch"
    if v >= 0:
        return f"{what}接近停滞", "watch"
    return f"{what}为负，衰退风险上升", "alert"


def gdpnow(c: Ctx) -> Result:
    return gdp(c, "本季度增长预测")


def gdp_q(c: Ctx) -> Result:
    text, lv = gdp(c, "上季度增长")
    now = c.extra.get("gdpnow")
    if now is not None:
        text += f"；GDPNow 对本季度的预测为 {now:.1f}%"
    return text, lv


def nfp3(c: Ctx) -> Result:
    """按 3 个月均值判断，单月数据噪音大。"""
    v = c.extra.get("avg3", c.v)
    if v >= 200:
        t, lv = "就业增长强劲", "ok"
    elif v >= 100:
        t, lv = "就业增长稳健", "ok"
    elif v >= 50:
        t, lv = "就业增长放缓，接近维持失业率不变所需的水平", "watch"
    elif v >= 0:
        t, lv = "就业增长疲弱", "watch"
    else:
        t, lv = "就业在收缩", "alert"
    d = _dir(c.extra.get("avg3_chg"), "比上月的 3 个月均值回升", "比上月的 3 个月均值回落", "")
    return f"3 个月平均 {v:,.0f} 千人，" + t + ("，" + d if d else ""), lv


def unrate(c: Ctx) -> Result:
    sahm = c.extra.get("sahm")
    chg12 = c.extra.get("chg12")
    parts = []
    if chg12 is not None:
        parts.append(f"较一年前{'上升' if chg12 > 0 else '下降' if chg12 < 0 else '持平'} {abs(chg12):.1f} 个百分点")
    if sahm is not None:
        parts.append(f"Sahm 指标 {sahm:.2f}（≥0.5 为衰退信号）")
    if sahm is not None and sahm >= 0.5:
        return "；".join(parts) + "，已触发衰退信号", "alert"
    if sahm is not None and sahm >= 0.3:
        return "；".join(parts) + "，接近触发", "watch"
    return "；".join(parts) + ("，劳动力市场稳定" if parts else "劳动力市场稳定"), "ok"


def retail_ctrl(c: Ctx) -> Result:
    avg3 = c.extra.get("avg3")
    tail = f"，近 3 个月平均 {avg3:+.2f}%" if avg3 is not None else ""
    if c.v >= 0.5:
        return "核心零售强，消费动能好" + tail, "ok"
    if c.v >= 0:
        return "核心零售温和增长" + tail, "ok"
    return "核心零售回落，消费降温" + tail, "watch"


# ---- 通胀 ----
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


def core_3m(c: Ctx) -> Result:
    yoy = c.extra.get("yoy")
    t = f"{vs_target(c.v)}"
    if yoy is not None:
        t += f"；比同比 {yoy:.2f}% 高 0.3 个百分点以上，短期在加速" if c.v > yoy + 0.3 else \
             f"；比同比 {yoy:.2f}% 低 0.3 个百分点以上，短期在放缓" if c.v < yoy - 0.3 else \
             f"；与同比 {yoy:.2f}% 相差不到 0.3 个百分点，短期动能持平"
    return t, "alert" if c.v >= 3 else "watch" if c.v >= 2.5 else "ok"


def core_yoy(c: Ctx) -> Result:
    return vs_target(c.v), "alert" if c.v >= 3 else "watch" if c.v >= 2.5 else "ok"


def supercore(c: Ctx) -> Result:
    m3 = c.extra.get("m3")
    tail = f"，3 个月年化 {m3:.1f}%" if m3 is not None else ""
    if c.v >= 3.5:
        return "服务通胀粘性强（与工资相关），是降息的主要障碍" + tail, "alert"
    if c.v >= 2.75:
        return "服务通胀仍偏高" + tail, "watch"
    return "服务通胀回到与 2% 目标相容的区间" + tail, "ok"


def fwd_infl(c: Ctx) -> Result:
    v = c.v
    if v > 2.8:
        return "长期通胀预期明显抬升，有脱锚风险", "alert"
    if v > 2.5:
        return "长期通胀预期略高于常态", "watch"
    if v >= 2.0:
        return "长期通胀预期稳定在目标附近", "ok"
    return "长期通胀预期偏低", "watch"


# ---- 流动性 ----
def reserves(c: Ctx) -> Result:
    ratio = c.extra.get("gdp_ratio")
    chg4 = c.extra.get("chg4")
    tail = f"；近 4 周{'增加' if chg4 > 0 else '减少'} {abs(chg4):,.0f} 十亿" if chg4 is not None else ""
    if ratio is None:
        return "准备金" + tail, "ok"
    head = f"约占 GDP {ratio:.1f}%"
    # 「充足准备金」下限的常见估计约为 GDP 的 9–11%；要结合 SOFR − IORB 一起看
    if ratio >= 11:
        return head + "，处于充裕区间" + tail, "ok"
    if ratio >= 9:
        return head + "，落在充足下限的估计区间（约 9–11%），留意回购利率是否抬升" + tail, "watch"
    return head + "，低于充足下限的常见估计，资金面可能偏紧" + tail, "alert"


def sofr_iorb(c: Ctx) -> Result:
    v = c.v
    avg = c.extra.get("avg4")
    tail = f"（近 4 周平均 {avg:+.0f}bp）" if avg is not None else ""
    if v > 5:
        return "SOFR 明显高于 IORB，回购资金紧张" + tail, "alert"
    if v > 0:
        return "SOFR 高于 IORB，准备金可能接近不再充裕" + tail, "watch"
    if v >= -5:
        return "SOFR 略低于 IORB，资金面平稳" + tail, "ok"
    return "SOFR 远低于 IORB，资金非常充裕" + tail, "ok"


def nfci(c: Ctx) -> Result:
    d = ""
    if c.v > 0:
        return "金融条件紧于历史平均" + d, "watch"
    if c.v > -0.3:
        return "金融条件接近历史平均、略偏松" + d, "ok"
    return "金融条件明显宽松，对风险资产友好" + d, "ok"


def hy(c: Ctx) -> Result:
    v = c.v
    if v < 3:
        return "信用利差很窄：风险偏好高，但对坏消息的缓冲也薄", "watch"
    if v < 5:
        return "信用利差处于正常区间", "ok"
    if v < 7:
        return "信用利差走阔，信用压力上升", "watch"
    return "信用利差处于危机水平", "alert"


# ---- 财政 ----
def deficit(c: Ctx) -> Result:
    chg = c.extra.get("chg12")
    tail = f"，比一年前{'扩大' if chg > 0 else '收窄'} {abs(chg):.1f} 个百分点" if chg is not None else ""
    if c.v >= 6:
        return "赤字在非衰退时期偏大，财政仍在托底需求" + tail, "alert"
    if c.v >= 4:
        return "赤字偏高" + tail, "watch"
    return "赤字处于常见水平" + tail, "ok"


def interest(c: Ctx) -> Result:
    if c.v >= 3:
        return "利息负担处于历史高位（1990 年代峰值约 3.2%），挤压其他支出、推高发债", "alert"
    if c.v >= 2.5:
        return "利息负担偏重", "watch"
    return "利息负担可控", "ok"


# ---- 货币政策 ----
def real_policy(c: Ctx) -> Result:
    v = c.v
    if v >= 2:
        return "实际政策利率明显高于中性（约 0.5–1%），政策紧缩", "alert"
    if v >= 1:
        return "实际政策利率高于中性，政策偏紧", "watch"
    if v >= 0:
        return "实际政策利率接近中性", "ok"
    return "实际政策利率为负，政策宽松", "watch"


def effr_path(c: Ctx) -> Result:
    cur = c.extra.get("current")
    ye, ny = c.extra.get("year_end"), c.extra.get("next_year")
    dot_ye, dot_ny = c.extra.get("dot_year"), c.extra.get("dot_next")
    parts = []

    def moves(x: float) -> str:
        n = (x - cur) / 0.25
        if abs(n) < 0.2:
            return "不变"
        return f"{'加息' if n > 0 else '降息'}约 {abs(n):.1f} 次"

    if cur is not None and ye is not None:
        parts.append(f"市场预计到年底{moves(ye)}")
    if cur is not None and ny is not None:
        parts.append(f"到明年底{moves(ny)}")
    lv = "ok"
    if dot_ny is not None and ny is not None:
        gap = ny - dot_ny
        if abs(gap) >= 0.25:
            parts.append(f"比点阵图{'更鹰' if gap > 0 else '更鸽'} {abs(gap) * 100:.0f}bp")
            lv = "watch"
        else:
            parts.append("与点阵图基本一致")
    elif dot_ye is not None and ye is not None:
        parts.append(f"点阵图年底 {dot_ye:.2f}%")
    return "，".join(parts) if parts else "暂无市场隐含路径", lv


def curve(c: Ctx) -> Result:
    """inv_days_ago：距离最近一次倒挂（< 0）过去了多少个交易日；一年内没倒挂过为 None。"""
    ago = c.extra.get("inv_days_ago")
    if c.v < 0:
        return "收益率曲线倒挂，历史上领先衰退 6–18 个月", "alert"
    if ago is not None and ago <= 63:
        return "最近 3 个月内刚由倒挂转正，历史上衰退常在这一阶段开始", "alert"
    if ago is not None:
        return f"倒挂已结束约 {ago / 21:.0f} 个月，曲线在变陡；衰退风险窗口尚未完全过去", "watch"
    if c.v < 0.5:
        return "曲线接近平坦", "watch"
    return "曲线正常向上倾斜", "ok"


# ---------------------------------------------------------------------------
# 维度状态：每个维度按经济锚点给状态，而不是和历史平均比
#
# 每个函数拿到一组数（缺的为 None），返回：
#   label   状态短语，放在卡片右上角
#   head    一句话
#   points  证据，每条是 (文字, 级别)
#   level   给总判断用的档位（增长 / 通胀：+1 / 0 / −1）
#   split   是否存在明显分歧

def _f(x: float | None, fmt: str = "{:.1f}") -> str:
    return "—" if x is None else fmt.format(x)


def growth_state(v: dict) -> dict:
    now, last = v.get("gdpnow"), v.get("gdp_q")
    nfp3, nfp3_ago = v.get("nfp3"), v.get("nfp3_ago")
    u12, sahm = v.get("unrate_chg12"), v.get("sahm")
    out = now if now is not None else last
    if out is None:
        o, o_txt = None, "数据不足"
    elif out >= 2.5:
        o, o_txt = 1, "偏强"
    elif out >= 1.5:
        o, o_txt = 0, "接近潜在"
    elif out >= 0.5:
        o, o_txt = -1, "低于潜在"
    else:
        o, o_txt = -1, "停滞或收缩"
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
    head = f"产出{o_txt}、就业{l_txt}" + ("：增长分化" if split else "")
    pts = []
    if now is not None or last is not None:
        t = f"本季 GDPNow {_f(now)}%，上季实际 {_f(last)}%（潜在增速约 2%）"
        gap = abs(now - last) if now is not None and last is not None else 0
        if gap > 2:
            t += f"；两者相差 {gap:.1f} 个百分点，本季预测要等 GDP 初值确认"
        pts.append((t, "watch" if gap > 2 else "ok"))
    if nfp3 is not None:
        d = "" if nfp3_ago is None else ("，比上月的 3 个月均值回升" if nfp3 > nfp3_ago else "，比上月的 3 个月均值回落")
        pts.append((f"非农 3 个月均值 {nfp3:,.0f} 千人（维持失业率不变约需 50–100 千人）{d}",
                    "ok" if l == 1 else "watch"))
    if u12 is not None or sahm is not None:
        pts.append((f"失业率较一年前 {_f(u12, '{:+.1f}')} 个百分点，Sahm {_f(sahm, '{:.2f}')}（0.5 触发）",
                    "alert" if sahm is not None and sahm >= 0.5 else "ok"))
    return {"label": label, "head": head, "points": pts, "level": level, "split": split,
            "output": o_txt, "labor": l_txt}


def inflation_state(v: dict) -> dict:
    yoy, m3, sc, fwd = v.get("core_yoy"), v.get("core_3m"), v.get("supercore"), v.get("fwd")
    nc = v.get("nowcast")  # [(期间文字, 同比)]，比最新官方数据更新的期间
    if yoy is None:
        return {"label": "数据不足", "head": "数据不足", "points": [], "level": None, "split": False}
    gap = yoy - 2
    lvl_txt = "明显高于目标" if gap >= 0.75 else "略高于目标" if gap >= 0.25 else         "接近目标" if gap > -0.25 else "低于目标"
    level = 1 if gap >= 0.5 else -1 if gap <= -0.25 else 0
    mom = None if m3 is None else m3 - yoy
    mom_txt = "" if mom is None else "短期动能在加速" if mom > 0.3 else "短期动能在放缓" if mom < -0.3 else "短期动能持平"
    nxt = ""
    if nc:
        last_nc = nc[-1][1]
        nxt = ("，Nowcast 预计同比回升" if last_nc > yoy + 0.1 else "，Nowcast 预计同比回落" if last_nc < yoy - 0.1
               else "，Nowcast 预计同比持平") + f"到 {last_nc:.2f}%（{nc[-1][0]}）"
    label = {1: "偏热", 0: "接近目标", -1: "偏冷"}[level]
    head = f"核心 PCE {lvl_txt}" + (f"，{mom_txt}" if mom_txt else "") + nxt
    pts = [(f"核心 PCE 同比 {yoy:.2f}%，3 个月年化 {_f(m3, '{:.2f}')}%（目标 2%）",
            "alert" if gap >= 1 else "watch" if gap >= 0.5 else "ok")]
    if nc:
        pts.append(("克利夫兰联储 Nowcast：" + "、".join(f"{p} {x:.2f}%" for p, x in nc), "watch" if nc[-1][1] > yoy + 0.1 else "ok"))
    if sc is not None:
        pts.append((f"超级核心同比 {sc:.2f}%（服务除能源、住房，与工资相关）", "alert" if sc >= 3.5 else "watch" if sc >= 2.75 else "ok"))
    if fwd is not None:
        pts.append((f"5y5y 远期通胀预期 {fwd:.2f}%", "ok" if 2 <= fwd <= 2.5 else "watch"))
    return {"label": label, "head": head, "points": pts, "level": level, "split": False,
            "rising": (nc and nc[-1][1] > yoy + 0.1) or (mom is not None and mom > 0.3)}


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
    pts = [(f"SOFR − IORB {_f(sofr, '{:+.0f}')}bp，准备金约占 GDP {_f(ratio)}%（充足下限估计约 9–11%）", fl)]
    if nfci_v is not None:
        pts.append((f"NFCI {nfci_v:.2f}（0 为历史平均，负值偏松）", "ok" if nfci_v < 0 else "watch"))
    if hy_v is not None:
        pts.append((f"高收益利差 {hy_v:.2f}%：窄利差说明风险偏好高，但对坏消息缓冲薄", "watch" if hy_v < 3 or hy_v >= 5 else "ok"))
    return {"label": label, "head": head, "points": pts, "level": None, "split": False}


def fiscal_state(v: dict) -> dict:
    d, chg, it, debt = v.get("deficit"), v.get("deficit_chg12"), v.get("interest"), v.get("debt")
    imp = None if chg is None else "收缩（赤字在收窄，拖累增长）" if chg < -0.5 else         "扩张（赤字在扩大，托底增长）" if chg > 0.5 else "中性"
    press = None if it is None else "高" if it >= 3 else "偏高" if it >= 2.5 else "可控"
    label = f"脉冲{(imp or '—').split('（')[0]} · 偿债压力{press or '—'}"
    head = f"财政脉冲{imp or '—'}；偿债压力{press or '—'}"
    pts = []
    if d is not None:
        pts.append((f"赤字率 {d:.1f}% GDP，较一年前 {_f(chg, '{:+.1f}')} 个百分点", "watch" if d >= 4 else "ok"))
    if it is not None:
        pts.append((f"利息支出占 GDP {it:.2f}%（1990 年代峰值约 3.2%）", "alert" if it >= 3 else "ok"))
    if debt is not None:
        pts.append((f"联邦债务占 GDP {debt:.0f}%", "watch" if debt >= 100 else "ok"))
    return {"label": label, "head": head, "points": pts, "level": None, "split": False}


def policy_state(v: dict) -> dict:
    real, cur, ye, ny, dot_ny = (v.get(k) for k in ("real_policy", "current", "year_end", "next_year", "dot_next"))
    stance = None if real is None else "宽松" if real < 0 else "接近中性" if real < 1 else "偏紧" if real < 2 else "紧缩"
    path, n = None, None
    if cur is not None and ny is not None:
        n = (ny - cur) / 0.25
        path = f"加息约 {n:.1f} 次" if n > 0.5 else f"降息约 {-n:.1f} 次" if n < -0.5 else "按兵不动"
    gap = None if ny is None or dot_ny is None else ny - dot_ny
    label = f"立场{stance or '—'} · 市场定价{'加息' if n and n > 0.5 else '降息' if n and n < -0.5 else '不变' if n is not None else '—'}"
    head = f"当前立场{stance or '—'}"
    if path:
        head += f"，但市场定价到明年底{path}" if stance == "接近中性" and n and abs(n) > 0.5 else f"，市场定价到明年底{path}"
    if gap is not None and abs(gap) >= 0.25:
        head += f"，比点阵图{'鹰' if gap > 0 else '鸽'} {abs(gap) * 100:.0f}bp"
    pts = []
    if real is not None:
        pts.append((f"实际政策利率 {real:.2f}%（EFFR − 核心 PCE 同比；中性约 0.5–1%）", "ok" if 0 <= real < 1 else "watch"))
    if path:
        pts.append((f"市场隐含：当前 {cur:.2f}% → 年底 {_f(ye, '{:.2f}')}% → 明年底 {ny:.2f}%", "watch" if abs(n) > 2 else "ok"))
    if gap is not None:
        pts.append((f"点阵图明年底 {dot_ny:.2f}%，市场比它{'高' if gap > 0 else '低'} {abs(gap) * 100:.0f}bp",
                    "watch" if abs(gap) >= 0.25 else "ok"))
    return {"label": label, "head": head, "points": pts, "level": None, "split": False,
            "hikes": n}


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
    g_txt = f"增长分化（{g['head'].split('：')[0]}）" if g.get("split") else f"增长{g['label']}"
    head = f"{name}：{g_txt}，通胀{i['label']}"
    if i.get("rising") and il >= 0:
        head += "且可能再抬头"
    sub = "；".join(x for x in (p.get("head"), "流动性" + liq.get("label", "")) if x)
    return {"name": name, "head": head, "sub": sub}


LEVEL_ORDER = {"alert": 0, "watch": 1, "ok": 2}
