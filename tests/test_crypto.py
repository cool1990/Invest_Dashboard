"""加密货币板块的规则和解析。不访问网络。"""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.crypto import binance, bgeometrics, build, etf  # noqa: E402
from pipeline.crypto import interpret as I  # noqa: E402
from pipeline.crypto.build import Sources, build_dashboard  # noqa: E402


def _days(n, start=date(2024, 1, 1), step=1):
    return [start + timedelta(days=i * step) for i in range(n)]


class ValuationTest(unittest.TestCase):
    def test_mvrv_bands(self):
        self.assertEqual(I.valuation_label(0.99), "低于成本")
        self.assertEqual(I.valuation_label(1.0), "成本附近")
        self.assertEqual(I.valuation_label(1.39), "成本附近")
        self.assertEqual(I.valuation_label(1.4), "盈利扩张")
        self.assertEqual(I.valuation_label(2.4), "盈利扩张")
        self.assertEqual(I.valuation_label(2.41), "过热")
        self.assertEqual(I.valuation_label(None), "数据不足")

    def test_missing_does_not_vote(self):
        st = I.valuation_state({})
        self.assertEqual(st["label"], "数据不足")
        self.assertEqual(st["why"], [])


class HolderTest(unittest.TestCase):
    def test_capitulation_before_distribution(self):
        st = I.holder_state({"price": 70, "sth": 80, "supply_30d": -1, "sopr7": 0.9})
        self.assertEqual(st["label"], "投降")

    def test_distribution(self):
        st = I.holder_state({"price": 90, "sth": 80, "supply_30d": -1, "sopr7": 1.1})
        self.assertEqual(st["label"], "派发")

    def test_accumulation(self):
        st = I.holder_state({"price": 90, "sth": 80, "supply_30d": 0.4, "sopr7": 1.1})
        self.assertEqual(st["label"], "吸筹")

    def test_hold_when_signals_disagree(self):
        # 供给在下降，但不是盈利卖出，也没到投降
        st = I.holder_state({"price": 90, "sth": 80, "supply_30d": -0.2, "sopr7": 0.98})
        self.assertEqual(st["label"], "持有")

    def test_missing(self):
        self.assertEqual(I.holder_state({})["label"], "数据不足")


class LeverageTest(unittest.TestCase):
    def test_delever_first(self):
        self.assertEqual(I.leverage_state({"funding7": 0.05, "oi_30d": -15.1})["label"], "去杠杆")

    def test_boundaries(self):
        self.assertEqual(I.leverage_state({"funding7": 0.03, "oi_30d": 0})["label"], "中性")
        self.assertEqual(I.leverage_state({"funding7": 0.0301, "oi_30d": 0})["label"], "多头拥挤")
        self.assertEqual(I.leverage_state({"funding7": -0.001, "oi_30d": 0})["label"], "空头拥挤")
        self.assertEqual(I.leverage_state({"funding7": 0.01, "oi_30d": 15})["label"], "中性")
        self.assertEqual(I.leverage_state({"funding7": 0.01, "oi_30d": 15.1})["label"], "加杠杆")
        self.assertEqual(I.leverage_state({"funding7": 0.05, "oi_30d": 20})["label"], "多头拥挤")
        self.assertEqual(I.leverage_state({})["label"], "数据不足")


class FundingWindowTest(unittest.TestCase):
    def test_seven_day_mean_is_percent(self):
        end = date(2026, 9, 21)
        rows = []
        for i, rate in enumerate((0.0004, 0.0002)):
            stamp = (end - timedelta(days=i)).isoformat() + "T08:00:00Z"
            rows.append({"time": stamp, "rate": f"{rate:.8f}"})
        self.assertAlmostEqual(binance.mean_recent(rows, 7), 0.03)
        self.assertAlmostEqual(build.funding_7d(rows)[-1][1], 0.03)


class SpotTest(unittest.TestCase):
    def test_etf_versus_issuance(self):
        self.assertEqual(I.etf_vote(100, 80), 1)
        self.assertEqual(I.etf_vote(80, 100), 0)
        self.assertEqual(I.etf_vote(100, 100), 0)
        self.assertEqual(I.etf_vote(-100, 80), -1)
        self.assertEqual(I.etf_vote(-50, 80), 0)
        self.assertIsNone(I.etf_vote(None, 80))

    def test_average_with_stables(self):
        # 只有稳定币一票，高于 2% 就是流入
        st = I.spot_state({"stable_30d": 2.1})
        self.assertEqual(st["label"], "流入")
        # ETF 没超过新产出，稳定币收缩，平均为负
        st = I.spot_state({"etf_usd": 10, "issuance_usd": 20, "stable_30d": -1.1})
        self.assertEqual(st["label"], "流出")
        self.assertEqual(st["etf_vote"], 0)
        # 两票对冲
        st = I.spot_state({"etf_usd": 100, "issuance_usd": 80, "stable_30d": -2})
        self.assertEqual(st["label"], "平淡")

    def test_missing(self):
        self.assertEqual(I.spot_state({})["label"], "数据不足")


class CycleTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(I.cycle_name("低于成本", "投降"), "出清")
        self.assertEqual(I.cycle_name("成本附近", "持有"), "磨底")
        self.assertEqual(I.cycle_name("盈利扩张", "吸筹"), "景气上行")
        self.assertEqual(I.cycle_name("盈利扩张", "持有"), "景气上行")
        self.assertEqual(I.cycle_name("过热", "持有"), "过热")
        self.assertEqual(I.cycle_name("过热", "吸筹"), "过热")
        self.assertEqual(I.cycle_name("过热", "派发"), "见顶风险")
        self.assertEqual(I.cycle_name("低于成本", "派发"), "分化")
        self.assertEqual(I.cycle_name("盈利扩张", "派发"), "分化")
        self.assertEqual(I.cycle_name("数据不足", "持有"), "数据不足")

    def test_funds(self):
        self.assertEqual(I.funds_name("流入", "中性", 1), "资金配合")
        self.assertEqual(I.funds_name("流入", "加杠杆", 1), "资金配合")
        self.assertEqual(I.funds_name("流入", "多头拥挤", 1), "资金拥挤")
        self.assertEqual(I.funds_name("平淡", "多头拥挤", 0), "资金拥挤")
        self.assertEqual(I.funds_name("流出", "中性", -1), "资金撤退")
        self.assertEqual(I.funds_name("流出", "多头拥挤", -1), "资金撤退")
        self.assertEqual(I.funds_name("流入", "去杠杆", 1), "资金出清")
        self.assertEqual(I.funds_name("平淡", "中性", 0), "资金平淡")
        # 稳定币单独把现货打成流出，但 ETF 没有超过新产出的流出
        self.assertEqual(I.funds_name("流出", "中性", 0), "资金平淡")
        self.assertEqual(I.funds_name("数据不足", "数据不足", None), "数据不足")

    def test_price_confirms_only(self):
        self.assertIn("价格已确认", I.confirm_text("景气上行", "上升趋势"))
        self.assertIn("价格尚未确认", I.confirm_text("景气上行", "下降趋势"))
        self.assertIn("价格已确认", I.confirm_text("出清", "下降趋势"))
        env = I.environment("盈利扩张", "持有", "流入", "中性", 1, "上升趋势", "比特币独强", "走弱")
        self.assertEqual(env["name"], "景气上行，资金配合")
        for line in env["lines"]:
            if line["k"] == "价格":
                self.assertIn("价格已确认", line["t"])
                continue
            self.assertFalse(any(ch.isdigit() for ch in line["t"]))


class BreadthSentimentTest(unittest.TestCase):
    def test_breadth_conflict(self):
        st = I.breadth_state({"dom_30d": 2.0, "eth_30d": 4})
        self.assertEqual(st["label"], "分化")
        st = I.breadth_state({"dom_30d": 2.0, "eth_30d": -1})
        self.assertEqual(st["label"], "比特币独强")
        st = I.breadth_state({"dom_30d": -2.0, "eth_30d": -4})
        self.assertEqual(st["label"], "分化")
        st = I.breadth_state({"dom_30d": 0.2, "eth_30d": 10})
        self.assertEqual(st["label"], "结构稳定")

    def test_fear_greed_bands(self):
        self.assertEqual(I.fear_greed_label(24), "极度恐惧")
        self.assertEqual(I.fear_greed_label(25), "恐惧")
        self.assertEqual(I.fear_greed_label(45), "中性")
        self.assertEqual(I.fear_greed_label(56), "贪婪")
        self.assertEqual(I.fear_greed_label(76), "极度贪婪")

    def test_trend_band(self):
        self.assertEqual(I.trend_state({"ma_dist": 5})["label"], "趋势中性")
        self.assertEqual(I.trend_state({"ma_dist": 5.1})["label"], "上升趋势")
        self.assertEqual(I.trend_state({"ma_dist": -5.1})["label"], "下降趋势")


class EtfParseTest(unittest.TestCase):
    HTML = """
    <table class="etf"><thead><tr><th> </th><th>Total</th></tr></thead><tbody>
      <tr><td><span class="tabletext">09 Sep 2026</span></td>
          <td><span class="redFont">(120.2)</span></td></tr>
      <tr><td><span class="tabletext">10 Sep 2026</span></td>
          <td><span class="tabletext">15.5</span></td></tr>
      <tr><td><span class="tabletext">11 Sep 2026</span></td>
          <td><span class="tabletext">-</span></td></tr>
      <tr><td>Total</td><td>100</td></tr>
    </tbody></table>
    """

    def test_parse(self):
        rows = etf.parse_html(self.HTML)
        self.assertEqual([(r["date"], float(r["total_usd_mn"])) for r in rows],
                         [("2026-09-09", -120.2), ("2026-09-10", 15.5)])


class BgFundingTest(unittest.TestCase):
    def test_parse(self):
        rows = bgeometrics.parse_funding([
            {"d": "2026-09-20 08:00:00", "fundingRate": "0.00010000"},
            {"d": "2026-09-21", "fundingRate": 0.00005},
        ])
        self.assertEqual(rows[0]["time"], "2026-09-20T08:00:00Z")
        self.assertEqual(rows[1]["time"], "2026-09-21T00:00:00Z")
        self.assertAlmostEqual(float(rows[0]["rate"]), 0.0001)


class HodlTest(unittest.TestCase):
    def test_one_year_plus(self):
        row = {"age_1d_1w": "10", "age_1y_2y": "3", "age_10y": "7", "age_6m_1y": "5"}
        self.assertEqual(bgeometrics.lth_coins(row), 10)
        coins, share = bgeometrics.hodl_share(row)
        self.assertEqual(coins, 10)
        self.assertAlmostEqual(share, 10 / 25 * 100)


class MetricsParseTest(unittest.TestCase):
    def test_last_row(self):
        text = (
            "create_time,symbol,sum_open_interest,sum_open_interest_value,"
            "count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,"
            "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
            "2026-09-26 00:00:00,BTCUSDT,1,100,1,1,1.1,1\n"
            "2026-09-26 12:00:00,BTCUSDT,2,250,1,1,1.4,1\n"
        )
        row = binance.parse_metrics_csv(text)
        self.assertEqual(row["date"], "2026-09-26")
        self.assertEqual(row["oi_usd"], 250)
        self.assertEqual(row["ls_ratio"], 1.4)


class BuildShapeTest(unittest.TestCase):
    def test_dashboard_uses_same_rules(self):
        days = _days(220)
        price = [(d, 100 + i) for i, d in enumerate(days)]
        mvrv = [(d, 1.6) for d in days]
        mcap = [(d, 1_000_000) for d in days]
        issuance = [(d, 450) for d in days]
        # 20 个交易日的流入盖过新产出：450*100 美元量级，这里用百万美元的日流入
        etf = [(d, 50) for d in days if d.weekday() < 5][-30:]
        hodl = [{"date": d.isoformat(), "age_1y_2y": str(1000 + i), "age_0d_1d": "10"} for i, d in enumerate(days)]
        src = Sources(
            price=price, mcap=mcap, mvrv=mvrv, issuance=issuance, etf_btc=etf,
            stable=[(d, 100 * (1.001 ** i)) for i, d in enumerate(days)],
            sth=[(d, 50) for d in days], lth_sopr=[(d, 1.05) for d in days], hodl=hodl,
            funding=[{"time": days[-1].isoformat() + "T08:00:00Z", "rate": "0.00010000"}],
        )
        dash = build_dashboard(src, days[-1])
        labels = {d["key"]: d["label"] for d in dash["dimensions"]}
        self.assertEqual(labels["valuation"], "盈利扩张")
        self.assertEqual(labels["holders"], "吸筹")
        self.assertEqual(labels["spot"], "流入")
        self.assertEqual(labels["leverage"], "中性")
        self.assertEqual(dash["verdict"]["name"], "景气上行，资金配合")
        self.assertIn("mvrv", dash["charts"])
        for line in dash["verdict"]["lines"]:
            if line["k"] == "价格":
                continue
            self.assertFalse(any(ch.isdigit() for ch in line["t"]))


if __name__ == "__main__":
    unittest.main()
