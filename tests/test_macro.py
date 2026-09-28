"""宏观板块的计算测试。只用标准库：python3 -m unittest discover -s tests"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import fred, series as ts  # noqa: E402
from pipeline.macro import consensus as cons, effr_expect, interpret as I  # noqa: E402
from pipeline.macro.build import MacroBuilder, build_dashboard  # noqa: E402


def months(start: date, n: int) -> list[date]:
    out, d = [], start
    for _ in range(n):
        out.append(d)
        d = ts._shift_month(d, 1)
    return out


class SeriesTest(unittest.TestCase):
    def test_pct_change_uses_calendar_not_neighbour(self):
        s = [(date(2024, 1, 1), 100.0), (date(2024, 3, 1), 110.0)]  # 缺 2 月
        self.assertEqual(ts.pct_change(s, 1, "M"), [])
        s.append((date(2024, 4, 1), 121.0))
        self.assertAlmostEqual(ts.pct_change(s, 1, "M")[0][1], 10.0)

    def test_annualized_quarterly(self):
        s = [(date(2024, 1, 1), 100.0), (date(2024, 4, 1), 101.0)]
        v = ts.pct_change(s, 1, "Q", annualize=4)[0][1]
        self.assertAlmostEqual(v, (1.01 ** 4 - 1) * 100)

    def test_rolling_sum_requires_full_window(self):
        s = [(d, 1.0) for d in months(date(2020, 1, 1), 13)]
        out = ts.rolling(s, 12, "sum")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], (date(2020, 12, 1), 12.0))

    def test_to_weekly_keeps_last_obs(self):
        s = [(date(2024, 1, 1), 1.0), (date(2024, 1, 5), 2.0), (date(2024, 1, 8), 3.0)]
        self.assertEqual(ts.to_weekly(s), [(date(2024, 1, 5), 2.0), (date(2024, 1, 8), 3.0)])

    def test_asof_gap(self):
        s = [(date(2024, 1, 1), 1.0)]
        self.assertEqual(ts.asof(s, date(2024, 1, 20)), 1.0)
        self.assertIsNone(ts.asof(s, date(2024, 6, 1), 60))

    def test_percentile(self):
        self.assertEqual(ts.percentile_rank([1, 2, 3, 4], 4), 87.5)

    def test_fred_csv_parse(self):
        text = "observation_date,UNRATE\n2024-01-01,3.7\n2024-02-01,.\n2024-03-01,3.9\n"
        self.assertEqual(fred.parse_csv(text), [(date(2024, 1, 1), 3.7), (date(2024, 3, 1), 3.9)])


class PceBreakdownTest(unittest.TestCase):
    """构造一组可加的分项，检验各项贡献之和接近总体与核心的变化。"""

    def setUp(self):
        ms = months(date(2019, 1, 1), 42)
        # 名义支出与价格月增速：食品、能源商品、能源服务、核心商品、住房、超级核心
        comp = {"food": (100.0, 0.002), "egoods": (40.0, 0.006), "eserv": (20.0, -0.003),
                "cgoods": (250.0, 0.0005), "housing": (200.0, 0.004), "super": (390.0, 0.003)}
        price = {k: [] for k in comp}
        nominal = {k: [] for k in comp}
        for i, d in enumerate(ms):
            for k, (n0, g) in comp.items():
                p = (1 + g) ** i
                price[k].append((d, p * 100))
                nominal[k].append((d, n0 * p))

        def add(*keys):
            return [(d, sum(nominal[k][i][1] for k in keys)) for i, d in enumerate(ms)]

        def price_of(*keys):
            out, level = [], 100.0
            for i, d in enumerate(ms):
                if i:
                    tot = sum(nominal[k][i - 1][1] for k in keys)
                    level *= 1 + sum(nominal[k][i - 1][1] / tot * (price[k][i][1] / price[k][i - 1][1] - 1)
                                     for k in keys)
                out.append((d, level))
            return out

        def annual(keys, freq_q=False):
            out = []
            for y in (2019, 2020, 2021):
                vals = [v for d, v in add(*keys) if d.year == y]
                out.append((date(y, 1, 1), sum(vals) / len(vals)))
            return out

        def quarterly(*keys):
            out = []
            series = add(*keys)
            for d, v in series:
                if d.month in (1, 4, 7, 10):
                    q = [x for dd, x in series if dd.year == d.year and d.month <= dd.month < d.month + 3]
                    out.append((d, sum(q) / len(q)))
            return out

        allk = tuple(comp)
        core = ("cgoods", "housing", "super")
        self.raw = {
            "PCE": add(*allk), "PCEPI": price_of(*allk),
            "DFXARG3M086SBEA": price["food"], "DFXARC1M027SBEA": nominal["food"],
            "DNRGRG3M086SBEA": price_of("egoods", "eserv"), "DNRGRC1M027SBEA": add("egoods", "eserv"),
            "PCEPILFE": price_of(*core), "DPCCRC1M027SBEA": add(*core),
            "IA001176M": price_of("cgoods", "super"), "IA001260M": price["super"],
            "DHSGRC1A027NBEA": annual(("housing",)),
            "DGDSRC1": add("food", "egoods", "cgoods"),
            "DGOERC1Q027SBEA": quarterly("egoods"), "DNRGRC1Q027SBEA": quarterly("egoods", "eserv"),
        }

    def check_sum(self, lines, headline, delta):
        maps = [dict(ln.data) for ln in lines]
        common = set.intersection(*(set(m) for m in maps))
        self.assertGreater(len(common), 12)
        for d in common:
            self.assertAlmostEqual(sum(m[d] for m in maps), headline[d], delta=delta)
        return maps

    def test_total_and_core_sum(self):
        br = MacroBuilder(self.raw, asof=date(2022, 7, 1)).pce_breakdown()
        self.assertEqual([ln.name for ln in br["total_mom"]], ["食品", "能源", "核心商品", "住房", "超级核心"])
        self.assertEqual([ln.name for ln in br["core_mom"]], ["核心商品", "住房", "超级核心"])
        # 住房比重用上一年年度值、能源商品比重用季度均值，与当月真实比重略有差别
        self.check_sum(br["total_mom"], dict(ts.pct_change(self.raw["PCEPI"], 1, "M")), 0.003)
        maps = self.check_sum(br["core_mom"], dict(ts.pct_change(self.raw["PCEPILFE"], 1, "M")), 0.003)
        # 超级核心贡献 ≈ 占核心份额 × 0.3%（份额由近似权重倒算，允许少量误差）
        d = date(2021, 6, 1)
        share = 390 * 1.003 ** 28 / dict(self.raw["DPCCRC1M027SBEA"])[date(2021, 5, 1)]
        self.assertAlmostEqual(maps[2][d], share * 0.3, delta=0.003)
        self.check_sum(br["core_yoy"], dict(ts.pct_change(self.raw["PCEPILFE"], 12, "M")), 0.05)

    def test_fallbacks(self):
        raw = dict(self.raw)
        raw.pop("DGOERC1Q027SBEA")
        br = MacroBuilder(raw).pce_breakdown()
        self.assertEqual([ln.name for ln in br["core_mom"]], ["住房", "核心除住房"])
        raw.pop("DHSGRC1A027NBEA")
        br = MacroBuilder(raw).pce_breakdown()
        self.assertEqual([ln.name for ln in br["total_mom"]], ["食品", "能源", "核心"])


class ConsensusTest(unittest.TestCase):
    def test_parse_value(self):
        self.assertEqual(cons.parse_value("150K"), 150)
        self.assertEqual(cons.parse_value("4.2%"), 4.2)
        self.assertEqual(cons.parse_value("-0.3%"), -0.3)
        self.assertEqual(cons.parse_value("7.25M"), 7250)
        self.assertIsNone(cons.parse_value(""))

    def test_ref_period(self):
        E = cons.EVENTS
        self.assertEqual(cons.ref_period(E["Non-Farm Employment Change"], date(2026, 10, 2)), date(2026, 9, 1))
        self.assertEqual(cons.ref_period(E["Core PCE Price Index m/m"], date(2026, 9, 26)), date(2026, 8, 1))
        self.assertEqual(cons.ref_period(E["JOLTS Job Openings"], date(2026, 9, 30)), date(2026, 8, 1))
        self.assertEqual(cons.ref_period(E["Final GDP q/q"], date(2026, 12, 22)), date(2026, 7, 1))
        self.assertEqual(cons.ref_period(E["Unemployment Claims"], date(2026, 10, 1)), date(2026, 9, 26))

    def test_merge_keeps_prerelease_forecast(self):
        from datetime import datetime, timezone
        ev = {"release_at": "2026-10-02T08:30:00-04:00", "title": "Non-Farm Employment Change",
              "impact": "High", "forecast": "150K", "previous": "142K"}
        before = datetime(2026, 10, 1, tzinfo=timezone.utc)
        after = datetime(2026, 10, 3, tzinfo=timezone.utc)
        rows = cons.merge_events([], [ev], before)
        rows = cons.merge_events(rows, [{**ev, "forecast": "155K"}], before)
        self.assertEqual(rows[0]["forecast"], "155K")
        rows = cons.merge_events(rows, [{**ev, "forecast": "999K"}], after)
        self.assertEqual(rows[0]["forecast"], "155K")

    def test_release_vs_actual(self):
        from datetime import datetime, timezone
        raw = {"PAYEMS": [(date(2026, 8, 1), 1000.0), (date(2026, 9, 1), 1180.0)],
               "UNRATE": [(date(2026, 9, 1), 4.3)]}
        events = [
            {"release_at": "2026-10-02T08:30:00-04:00", "title": "Non-Farm Employment Change",
             "impact": "High", "forecast": "150K", "previous": "142K"},
            {"release_at": "2026-10-02T08:30:00-04:00", "title": "Unemployment Rate",
             "impact": "High", "forecast": "4.2%", "previous": "4.1%"},
            {"release_at": "2026-10-06T10:00:00-04:00", "title": "ISM Services PMI",
             "impact": "High", "forecast": "51.0", "previous": "50.5"},
        ]
        dash = build_dashboard(raw, [], date(2026, 10, 3), events, [], datetime(2026, 10, 3, tzinfo=timezone.utc))
        rec = {r["title_en"]: r for r in dash["releases"]["recent"]}
        self.assertEqual(rec["Non-Farm Employment Change"]["actual_text"], "180")
        self.assertEqual(rec["Non-Farm Employment Change"]["verdict"], "好于预期")
        self.assertEqual(rec["Unemployment Rate"]["verdict"], "差于预期")
        self.assertEqual([r["title"] for r in dash["releases"]["upcoming"]], ["ISM 服务业 PMI"])
        sig = {s["id"]: s for s in dash["signals"]["growth"]}
        self.assertEqual(sig["nfp"]["consensus"]["forecast_text"], "150K")
        self.assertEqual(sig["nfp"]["consensus"]["surprise_text"], "+30")


class InterpretTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(I.core_mom(I.Ctx(0.4))[1], "alert")
        self.assertEqual(I.core_mom(I.Ctx(0.15))[1], "ok")
        self.assertEqual(I.unrate(I.Ctx(4.5, extra={"sahm": 0.6, "chg12": 0.8}))[1], "alert")
        self.assertEqual(I.curve(I.Ctx(-0.3))[1], "alert")
        self.assertEqual(I.curve(I.Ctx(1.2, extra={"min12": 0.4}))[1], "ok")
        text, lv = I.effr_path(I.Ctx(4.24, extra={"current": 3.88, "year_end": 4.24, "next_year": 4.76,
                                                  "dot_year": 4.1, "dot_next": 4.1}))
        self.assertIn("加息约 1.4 次", text)
        self.assertEqual(lv, "watch")

    def test_regime(self):
        self.assertTrue(I.regime({"growth": -0.8, "inflation": 0.9}).startswith("滞胀"))
        self.assertTrue(I.regime({"growth": 0.1, "inflation": 0.1}).startswith("温和"))


class ReservesTest(unittest.TestCase):
    def test_identity(self):
        wed = [date(2024, 1, 3), date(2024, 1, 10)]
        raw = {
            "WALCL": [(wed[0], 8_000_000.0), (wed[1], 8_010_000.0)],
            "WDTGAL": [(wed[0], 700_000.0), (wed[1], 650_000.0)],
            "WRBWFRBL": [(wed[0], 3_500_000.0), (wed[1], 3_600_000.0)],
            "WCURCIR": [(wed[0], 2_300_000.0), (wed[1], 2_300_000.0)],
            "RRPONTSYD": [(date(2024, 1, 2), 900.0), (wed[1], 850.0)],  # 十亿美元，第一周用前一天
        }
        fr = MacroBuilder(raw).reserves_frame()
        for i, d in enumerate(wed):
            total = sum(dict(fr[k])[d] for k in ("reserves", "rrp", "tga", "currency", "other"))
            self.assertAlmostEqual(total, dict(fr["assets"])[d])
        self.assertAlmostEqual(dict(fr["rrp"])[wed[0]], 900.0)
        self.assertAlmostEqual(dict(fr["other"])[wed[0]], 8000 - 900 - 700 - 2300 - 3500)


class ScoreAndDashboardTest(unittest.TestCase):
    def test_score_direction(self):
        ms = months(date(2000, 1, 1), 300)
        up = [(d, float(i)) for i, d in enumerate(ms)]
        from pipeline.macro.build import Component
        b = MacroBuilder({}, asof=ms[-1])
        hi = b.score([Component("a", up, +1), Component("b", up, +1)])
        lo = b.score([Component("a", up, -1), Component("b", up, -1)])
        self.assertGreater(hi["score"], 1)
        self.assertAlmostEqual(hi["score"], -lo["score"])
        self.assertEqual(hi["month"], ms[-1].isoformat())

    def test_empty_data_builds(self):
        dash = build_dashboard({}, [], date(2026, 9, 28))
        self.assertEqual(len(dash["dimensions"]), 5)
        self.assertTrue(all(d["label"] == "数据不足" for d in dash["dimensions"]))
        self.assertEqual(dash["charts"], {})

    def test_effr_path(self):
        raw = {"EFFR": [(date(2026, 9, 24), 3.88)],
               "FEDTARMD": [(date(2026, 1, 1), 3.9), (date(2027, 1, 1), 3.6)],
               "FEDTARMDLR": [(date(2026, 9, 17), 3.0)]}
        exp = [{"date": "2026-09-27", "series_id": k, "value": v, "obs_date": "", "remark": ""}
               for k, v in (("effr_next", "4.046"), ("effr_year", "4.24"), ("effr_ny", "4.762"))]
        c = build_dashboard(raw, exp, date(2026, 9, 28))["charts"]["effr_path"]
        market, dots = c["series"]
        self.assertEqual(market["data"], [3.88, 4.046, 4.24, 4.762, None])
        self.assertEqual(dots["data"], [3.88, None, 3.9, 3.6, 3.0])


class EffrExpectTest(unittest.TestCase):
    def test_parse_and_merge(self):
        text = ("date,series_id,name,value,unit,change_text,label,obs_date,carried,section,remark,hike_count,source\n"
                "2026-09-26,effr_next,下月EFFR,4.046,百分比,,中性,2026-09-25,,利率,备注,0.7,x.md\n"
                "2026-09-26,cnn_fg,CNN,33,点,,,2026-09-26,,,,,x.md\n"
                "2026-09-26,effr_year,年底EFFR,未更新,百分比,,,,,,,,x.md\n")
        rows = effr_expect.parse_source(text)
        self.assertEqual([(r["series_id"], r["value"]) for r in rows], [("effr_next", "4.046")])
        merged = effr_expect.merge([{"date": "2026-09-26", "series_id": "effr_next", "value": "4.0",
                                     "obs_date": "", "remark": ""}], rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["value"], "4.046")


if __name__ == "__main__":
    unittest.main()
