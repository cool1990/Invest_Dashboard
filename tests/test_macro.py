"""宏观板块的计算测试。只用标准库：python3 -m unittest discover -s tests"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import fred, series as ts  # noqa: E402
from pipeline.macro import effr_expect  # noqa: E402
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


class PceContributionTest(unittest.TestCase):
    """构造一组可加的分项，检验各项贡献之和等于总体环比。"""

    def setUp(self):
        ms = months(date(2020, 1, 1), 30)
        # 分项名义支出与价格：食品、能源商品、能源服务、核心商品、住房、其他服务
        comp = {
            "food": (100.0, 0.002), "egoods": (40.0, 0.01), "eserv": (30.0, -0.004),
            "cgoods": (300.0, 0.001), "housing": (200.0, 0.004), "oserv": (330.0, 0.003),
        }
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
            # 用份额加权的链式指数近似合成价格
            out, level = [], 100.0
            for i, d in enumerate(ms):
                if i:
                    tot = sum(nominal[k][i - 1][1] for k in keys)
                    level *= 1 + sum(nominal[k][i - 1][1] / tot * (price[k][i][1] / price[k][i - 1][1] - 1) for k in keys)
                out.append((d, level))
            return out

        goods = ("food", "egoods", "cgoods")
        services = ("eserv", "housing", "oserv")
        allk = goods + services
        self.raw = {
            "PCE": add(*allk), "PCEPI": price_of(*allk),
            "DGDSRG3M086SBEA": price_of(*goods), "DGDSRC1": add(*goods),
            "DSERRG3M086SBEA": price_of(*services), "PCES": add(*services),
            "DFXARG3M086SBEA": price["food"], "DFXARC1": nominal["food"],
            "DNRGRG3M086SBEA": price_of("egoods", "eserv"), "DNRGRC1": add("egoods", "eserv"),
            "DGOERG3M086SBEA": price["egoods"], "DGOERC1": nominal["egoods"],
            "DHSGRG3M086SBEA": price["housing"], "DHSGRC1": nominal["housing"],
        }

    def test_contributions_sum_to_headline(self):
        b = MacroBuilder(self.raw, asof=date(2022, 7, 1))
        mom, yoy = b.pce_contributions()
        headline = dict(ts.pct_change(self.raw["PCEPI"], 1, "M"))
        maps = [dict(ln.data) for ln in mom]
        self.assertEqual([ln.name for ln in mom], ["食品", "能源", "核心商品", "住房", "核心服务除住房"])
        for d, h in headline.items():
            self.assertAlmostEqual(sum(m[d] for m in maps), h, places=6)
        # 超级核心 = 其他服务单独的贡献
        d = date(2021, 6, 1)
        oserv = maps[4][d]
        self.assertGreater(oserv, 0)
        # 同比口径也应接近总体同比（链式误差很小）
        yoy_head = dict(ts.pct_change(self.raw["PCEPI"], 12, "M"))
        ymaps = [dict(ln.data) for ln in yoy]
        for d, h in yoy_head.items():
            self.assertAlmostEqual(sum(m[d] for m in ymaps), h, delta=0.05)


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
