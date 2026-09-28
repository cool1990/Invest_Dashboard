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
    d = _dir(c.extra.get("avg3_chg"), "比 3 个月前加快", "比 3 个月前放缓", "")
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
    a = ann(c.v)
    t = f"折年率约 {a:.1f}%，{vs_target(a, target)}"
    return t, "alert" if a >= hi else "watch" if a >= mid else "ok"


def core_cpi_mom(c: Ctx) -> Result:
    # CPI 长期比 PCE 高约 0.3–0.5 个百分点，阈值相应放宽
    return core_mom(c, hi=3.5, mid=2.8, target=2.0)


def core_3m(c: Ctx) -> Result:
    yoy = c.extra.get("yoy")
    t = f"{vs_target(c.v)}"
    if yoy is not None:
        t += f"；高于同比 {yoy:.2f}% 说明通胀在加速" if c.v > yoy + 0.1 else \
             f"；低于同比 {yoy:.2f}% 说明通胀在放缓" if c.v < yoy - 0.1 else f"；与同比 {yoy:.2f}% 相近"
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
    d = _dir(c.chg, "，比 3 个月前收紧", "，比 3 个月前放松", "")
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
    min12 = c.extra.get("min12")
    if c.v < 0:
        return "收益率曲线倒挂，历史上领先衰退 6–18 个月", "alert"
    if min12 is not None and min12 < 0:
        return "刚从倒挂转正，历史上衰退常在这一阶段开始", "alert"
    if c.v < 0.5:
        return "曲线接近平坦", "watch"
    return "曲线正常向上倾斜", "ok"


# ---------------------------------------------------------------------------
# 总判断

def regime(scores: dict[str, float | None]) -> str:
    g, i = scores.get("growth"), scores.get("inflation")
    if g is None or i is None:
        return "数据不足"
    hi, lo = 0.5, -0.5
    if g > hi and i > hi:
        return "过热：增长和通胀都高于常态"
    if g > hi:
        return "扩张：增长偏强、通胀可控"
    if g < lo and i > hi:
        return "滞胀风险：增长偏弱、通胀偏热"
    if g < lo and i < lo:
        return "衰退风险：增长和通胀同时走弱"
    if g < lo:
        return "放缓：增长偏弱，通胀不构成约束"
    if i > hi:
        return "增长平稳、通胀偏热：降息空间受限"
    if i < lo:
        return "增长平稳、通胀偏冷：有降息空间"
    return "温和：增长与通胀都接近常态"


LEVEL_ORDER = {"alert": 0, "watch": 1, "ok": 2}
