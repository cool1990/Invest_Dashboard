"""新数据对判断的影响：发布前的情景门槛、发布后的前后对比。

做法：把「新一期的值」加到对应序列末尾，用和页面完全相同的规则（interpret.py）
重新算一遍维度状态和整体环境。
- 发布前：在一组候选值上逐个试，把结果相同的相邻候选值并成一段，再压成一行：
  预期落在哪一段、页面上哪个标签会变；只留预期两侧最近的门槛。
  已经公布过的期间（例如 GDP 终值）只是修订，不写情景。
- 发布后：去掉这一期（发布前）和加上这一期（发布后）各算一次，比较差别。

只覆盖进了规则的指标：非农、失业率、核心 PCE 环比、核心 CPI 环比、实际 GDP。
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from .. import series as ts
from . import interpret as I

if TYPE_CHECKING:
    from .build import MacroBuilder

# 候选值：(下限, 上限, 步长, 显示格式)
GRID = {
    "nfp": (-300.0, 600.0, 1.0, "{:,.0f}K"),
    "unrate": (3.0, 6.5, 0.1, "{:.1f}%"),
    "core_pce_mom": (-0.20, 0.80, 0.01, "{:.2f}%"),
    "core_cpi_mom": (-0.20, 0.80, 0.01, "{:.2f}%"),
    "gdp_qoq": (-3.0, 7.0, 0.1, "{:.1f}%"),
}
# 情景试算的序列：新一期的期间已经在里面，说明这次只是修订
CTX = {"nfp": "nfp", "unrate": "unrate", "gdp_qoq": "gdp_qoq", "core_pce_mom": "core_idx", "core_cpi_mom": "ccpi_idx"}


def _num(text: str) -> str:
    """负号用 −，和页面其他地方一致。"""
    return text.replace("-", "−")


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


class Scenario:
    def __init__(self, b: "MacroBuilder"):
        self.b = b

    # -- 一次试算 -------------------------------------------------------------
    def outcome(self, key: str, ref: date, x: float | None) -> dict | None:
        """返回 {"fields": [(名称, 结果)], "detail": {...}}。x 为 None 表示不加新一期（发布前）。"""
        fn = getattr(self, f"_o_{key}", None)
        return fn(ref, x) if fn else None

    def _env(self, g: dict | None = None, i: dict | None = None) -> dict:
        st = self.b.states
        return I.environment(g or st["growth"], i or st["inflation"], st["policy"], st["liquidity"])

    def _growth(self, **over) -> list[tuple[str, str]]:
        g = I.growth_state({**self.b.inputs["growth"], **over})
        return [("产出", g["output"]), ("就业", g["labor"]), ("增长", g["label"]),
                ("整体环境", self._env(g=g)["name"])]

    @staticmethod
    def _before(s: list, ref: date) -> list:
        return [(d, v) for d, v in s if d < ref]

    def _o_nfp(self, ref: date, x: float | None) -> dict | None:
        vals = [v for _, v in self._before(self.b.ctx.get("nfp", []), ref)]
        if len(vals) < 4:
            return None
        if x is None:
            avg, ago = _mean(vals[-3:]), _mean(vals[-4:-1])
        else:
            avg, ago = _mean(vals[-2:] + [x]), _mean(vals[-3:])
        return {"fields": self._growth(nfp3=avg, nfp3_ago=ago), "detail": {"非农 3 个月均值": avg}}

    def _o_unrate(self, ref: date, x: float | None) -> dict | None:
        u = self._before(self.b.ctx.get("unrate", []), ref)
        if len(u) < 16:
            return None
        if x is not None:
            u = u + [(ref, x)]
        last_d, last_v = u[-1]
        year_ago = dict(u).get(ts._shift_month(last_d, -12))
        avg3 = [_mean([v for _, v in u[i - 2:i + 1]]) for i in range(2, len(u))]
        sahm = avg3[-1] - min(avg3[-13:-1])  # Sahm：3 个月均值减去前 12 个月里 3 个月均值的最低点
        chg12 = None if year_ago is None else last_v - year_ago
        return {"fields": self._growth(unrate_chg12=chg12, sahm=sahm), "detail": {"Sahm": sahm}}

    def _o_gdp_qoq(self, ref: date, x: float | None) -> dict | None:
        q = self._before(self.b.ctx.get("gdp_qoq", []), ref)
        cur = x if x is not None else (q[-1][1] if q else None)
        if cur is None:
            return None
        return {"fields": self._growth(gdp_q=cur), "detail": {}}

    def _price(self, idx_key: str, ref: date, x: float | None) -> tuple[list, float, float, float | None] | None:
        """加上新一期的环比后，返回 (序列, 单月环比, 3 个月均值, 同比)。"""
        idx = self._before(self.b.ctx.get(idx_key, []), ref)
        if len(idx) < 16:
            return None
        if x is not None:
            if ts._shift_month(idx[-1][0], 1) != ref:
                return None  # 中间缺月，无法接上
            idx = idx + [(ref, idx[-1][1] * (1 + x / 100))]
        mom = ts.pct_change(idx, 1, "M")
        yoy = ts.pct_change(idx, 12, "M")
        return idx, mom[-1][1], _mean([v for _, v in mom[-3:]]), yoy[-1][1] if yoy else None

    def _o_core_pce_mom(self, ref: date, x: float | None) -> dict | None:
        r = self._price("core_idx", ref, x)
        if not r:
            return None
        idx, _, avg3, yoy = r
        m3 = ts.pct_change(idx, 3, "M", annualize=12)[-1][1]
        i = I.inflation_state({**self.b.inputs["inflation"], "core_yoy": yoy, "core_3m": m3,
                               "nowcast": self.b.nowcast_after("核心 PCE", idx[-1][0])})
        # 只列页面上真有的标签：环比本身没有标签，只看它会不会改通胀和整体环境
        return {"fields": [("通胀", i["label"]), ("整体环境", self._env(i=i)["name"])],
                "detail": {"核心 PCE 同比": yoy, "3 个月均值折年": I.ann(avg3)}}

    # 核心 CPI 不进任何标签（通胀按核心 PCE 判断），不写情景

    # -- 发布前：情景门槛 -----------------------------------------------------
    def segments(self, key: str, ref: date) -> tuple[dict, list[dict]] | None:
        """在候选值上逐个试算，把结果相同的相邻候选值并成一段。返回 (现在的结果, 各段)。"""
        base = self.outcome(key, ref, None)
        if base is None:
            return None
        lo, hi, step, _ = GRID[key]
        segs: list[dict] = []
        for k in range(int(round((hi - lo) / step)) + 1):
            x = round(lo + k * step, 6)
            o = self.outcome(key, ref, x)
            if o is None:
                return None
            sig = tuple(o["fields"])
            if segs and segs[-1]["sig"] == sig:
                segs[-1]["end"] = x
            else:
                segs.append({"sig": sig, "start": x, "end": x})
        return base, segs

    def scenarios(self, key: str, ref: date, forecast: float | None, forecast_text: str = "") -> dict | None:
        """一行：预期落在哪、哪个标签会变。例如
        「预期 98K，就业仍为「降温」；≥ 267K 变为「强」，增长变为「扩张偏强」；≤ −34K 变为「疲弱」」。"""
        if key not in GRID or ref in dict(self.b.ctx.get(CTX[key], [])):
            return None  # 已经公布过的期间只是修订
        r = self.segments(key, ref)
        if r is None:
            return None
        base, segs = r
        base_f = dict(base["fields"])
        affects = [k for k in base_f if any(dict(sg["sig"]).get(k) != base_f[k] for sg in segs)]
        # 主标签：会变的第一个；都变不了就用第一个
        main = affects[0] if affects else next(iter(base_f))
        _, _, step, fmt = GRID[key]

        def changes(sg: dict, name_main: bool = False) -> str:
            # 门槛那几段紧跟在「主标签仍为…」后面，主标签的名字省掉
            return "，".join(f"{'' if k == main and not name_main else k}变为「{v}」"
                            for k, v in sg["sig"] if base_f.get(k) != v)

        fc = None
        if forecast is not None:
            fc = next((j for j, sg in enumerate(segs) if sg["start"] - step / 2 <= forecast <= sg["end"] + step / 2), None)
        fc_txt = forecast_text or (fmt.format(forecast) if forecast is not None else "")
        if fc is None:
            head = f"{main}现为「{base_f[main]}」"
        elif changes(segs[fc]):
            head = f"预期 {fc_txt}：{changes(segs[fc], name_main=True)}"
        else:
            head = f"预期 {fc_txt}，{main}仍为「{base_f[main]}」" + ("" if affects else "，整体环境不变")
        # 只留预期两侧最近的门槛；没有预期时以现在的结果所在段为准
        at = fc if fc is not None else next(
            (j for j, sg in enumerate(segs) if dict(sg["sig"]) == base_f), None)
        parts = [head]
        if at is not None:
            up = next((sg for sg in segs[at + 1:] if changes(sg)), None)
            down = next((sg for sg in reversed(segs[:at]) if changes(sg)), None)
            if up:
                parts.append(f"≥ {_num(fmt.format(up['start']))} {changes(up)}")
            if down:
                parts.append(f"≤ {_num(fmt.format(down['end']))} {changes(down)}")
        return {"text": "；".join(parts), "changed": fc is not None and bool(changes(segs[fc])),
                "affects": affects}

    # -- 发布后：前后对比 -----------------------------------------------------
    def impact(self, key: str, ref: date, actual: float) -> dict | None:
        before = self.outcome(key, ref, None)
        after = self.outcome(key, ref, actual)
        if not before or not after:
            return None
        bf = dict(before["fields"])
        changes = [f"{k}：「{bf.get(k)}」→「{v}」" for k, v in after["fields"] if bf.get(k) != v]
        return {"changed": bool(changes), "text": "；".join(changes) if changes else "没有改变判断",
                "after": "；".join(f"{k}「{v}」" for k, v in after["fields"])}
