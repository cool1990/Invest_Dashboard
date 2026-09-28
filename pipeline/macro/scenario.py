"""新数据对判断的影响：发布前的情景门槛、发布后的前后对比。

做法：把「新一期的值」加到对应序列末尾，用和页面完全相同的规则（interpret.py）
重新算一遍维度状态和整体环境。
- 发布前：在一组候选值上逐个试，把结果相同的相邻候选值并成一段，得到
  「新值落在哪个范围 → 判断变成什么」。
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
LEVEL_TEXT = {"alert": "警示", "watch": "关注", "ok": "正常"}


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
        idx, m1, avg3, yoy = r
        lvl = I.core_mom(I.Ctx(m1, extra={"avg3": avg3}))[1]
        m3 = ts.pct_change(idx, 3, "M", annualize=12)[-1][1]
        i = I.inflation_state({**self.b.inputs["inflation"], "core_yoy": yoy, "core_3m": m3,
                               "nowcast": self.b.nowcast_after("核心 PCE", idx[-1][0])})
        return {"fields": [("核心 PCE 环比", LEVEL_TEXT[lvl]), ("通胀", i["label"]),
                           ("整体环境", self._env(i=i)["name"])],
                "detail": {"核心 PCE 同比": yoy, "3 个月均值折年": I.ann(avg3)}}

    def _o_core_cpi_mom(self, ref: date, x: float | None) -> dict | None:
        r = self._price("ccpi_idx", ref, x)
        if not r:
            return None
        _, m1, avg3, yoy = r
        lvl = I.core_cpi_mom(I.Ctx(m1, extra={"avg3": avg3}))[1]
        return {"fields": [("核心 CPI 环比", LEVEL_TEXT[lvl])],
                "detail": {"核心 CPI 同比": yoy, "3 个月均值折年": I.ann(avg3)}}

    # -- 发布前：情景门槛 -----------------------------------------------------
    def scenarios(self, key: str, ref: date, forecast: float | None) -> dict | None:
        if key not in GRID:
            return None
        base = self.outcome(key, ref, None)
        if base is None:
            return None
        lo, hi, step, fmt = GRID[key]
        n = int(round((hi - lo) / step))
        segs: list[dict] = []
        for k in range(n + 1):
            x = round(lo + k * step, 6)
            o = self.outcome(key, ref, x)
            if o is None:
                return None
            sig = tuple(o["fields"])
            if segs and segs[-1]["sig"] == sig:
                segs[-1]["end"] = x
                segs[-1]["detail_end"] = o["detail"]
            else:
                segs.append({"sig": sig, "start": x, "end": x, "detail_start": o["detail"], "detail_end": o["detail"]})
        base_f = dict(base["fields"])
        out = []
        for j, sg in enumerate(segs):
            if len(segs) == 1:
                rng = "在常见范围内"
            elif j == 0:
                rng = f"≤ {fmt.format(sg['end'])}"
            elif j == len(segs) - 1:
                rng = f"≥ {fmt.format(sg['start'])}"
            else:
                rng = f"{fmt.format(sg['start'])} ~ {fmt.format(sg['end'])}"
            changes = [f"{k}变为「{v}」" for k, v in sg["sig"] if base_f.get(k) != v]
            detail = []
            for name in sg["detail_start"]:
                a, b = sg["detail_start"][name], sg["detail_end"][name]
                if a is None or b is None:
                    continue
                f, unit = ("{:,.0f}", " 千人") if "非农" in name else ("{:.2f}", "") if name == "Sahm" else ("{:.2f}", "%")
                lo_, hi_ = f.format(min(a, b)), f.format(max(a, b))
                detail.append(f"{name} {lo_}{unit}" if lo_ == hi_ else f"{name} {lo_}–{hi_}{unit}")
            has_fc = forecast is not None and sg["start"] - step / 2 <= forecast <= sg["end"] + step / 2
            out.append({"range": rng, "result": "；".join(changes) or "判断不变", "changed": bool(changes),
                        "detail": "，".join(detail), "forecast": has_fc})
        # 哪些标签在某一段里会变：页面上折叠行的标题
        affects = [k for k, v in base["fields"] if any(dict(sg["sig"]).get(k) != v for sg in segs)]
        return {"current": "；".join(f"{k}「{v}」" for k, v in base["fields"]), "segments": out,
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
