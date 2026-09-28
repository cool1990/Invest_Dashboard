from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import statelog  # noqa: E402
from pipeline import series as ts  # noqa: E402
from pipeline.semis import build as B  # noqa: E402
from pipeline.semis import importance, kosis, oldsite, sec, twse  # noqa: E402
from pipeline.semis import interpret as I  # noqa: E402


def _months(start: date, n: int) -> list[date]:
    out, d = [], start
    for _ in range(n):
        out.append(d)
        d = ts._shift_month(d, 1)
    return out


class SecTest(unittest.TestCase):
    def test_ytd_to_quarters(self):
        # 自然年公司：10-Q 只有年初至今累计，10-K 是全年
        units = {"USD": [
            {"start": "2025-01-01", "end": "2025-03-31", "val": 10, "form": "10-Q", "filed": "2025-04-30"},
            {"start": "2025-01-01", "end": "2025-06-30", "val": 25, "form": "10-Q", "filed": "2025-07-30"},
            {"start": "2025-01-01", "end": "2025-09-30", "val": 45, "form": "10-Q", "filed": "2025-10-30"},
            {"start": "2025-01-01", "end": "2025-12-31", "val": 70, "form": "10-K", "filed": "2026-02-01"},
            # 同一期间后来的修订覆盖先前的
            {"start": "2025-01-01", "end": "2025-03-31", "val": 11, "form": "10-Q/A", "filed": "2025-05-30"},
        ]}
        q = dict(sec.quarterly(units))
        self.assertEqual(q[date(2025, 3, 31)], 11)
        self.assertEqual(q[date(2025, 6, 30)], 14)
        self.assertEqual(q[date(2025, 9, 30)], 20)
        self.assertEqual(q[date(2025, 12, 31)], 25)

    def test_direct_quarter_preferred(self):
        units = {"USD": [
            {"start": "2025-04-01", "end": "2025-06-30", "val": 7, "form": "10-Q", "filed": "2025-07-30"},
            {"start": "2025-01-01", "end": "2025-03-31", "val": 5, "form": "10-Q", "filed": "2025-04-30"},
            {"start": "2025-01-01", "end": "2025-06-30", "val": 13, "form": "10-Q", "filed": "2025-07-30"},
        ]}
        self.assertEqual(dict(sec.quarterly(units))[date(2025, 6, 30)], 7)

    def test_extract_picks_latest_concept(self):
        facts = {"facts": {"us-gaap": {
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
                {"start": "2019-01-01", "end": "2019-03-31", "val": 1, "form": "10-Q", "filed": "2019-04-30"}]}},
            "PaymentsToAcquireProductiveAssets": {"units": {"USD": [
                {"start": "2025-01-01", "end": "2025-03-31", "val": 9, "form": "10-Q", "filed": "2025-04-30"}]}},
        }}}
        rows = sec.extract("AMZN", facts)
        self.assertEqual({r["concept"] for r in rows}, {"PaymentsToAcquireProductiveAssets"})

    def test_calendar_quarter(self):
        self.assertEqual(B.cal_quarter(date(2026, 1, 25)), date(2025, 10, 1))  # 英伟达 1 月结账 → 上年 Q4
        self.assertEqual(B.cal_quarter(date(2025, 8, 31)), date(2025, 7, 1))  # 甲骨文 8 月 → Q3
        self.assertEqual(B.cal_quarter(date(2025, 9, 30)), date(2025, 7, 1))


class TwseTest(unittest.TestCase):
    def test_openapi(self):
        text = json.dumps([
            {"出表日期": "1151005", "資料年月": "11509", "公司代號": "2330", "公司名稱": "台積電",
             "營業收入-當月營收": "330,000,000", "營業收入-去年當月營收": "250,000,000"},
            {"資料年月": "11509", "公司代號": "9999", "營業收入-當月營收": "1"},
        ], ensure_ascii=False)
        rows = twse.parse_openapi(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["period"], "2026-09")
        self.assertEqual(rows[0]["revenue"], 330000000)
        self.assertEqual(rows[0]["revenue_ly"], 250000000)

    def test_history_html(self):
        html = """<table><tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th></tr>
        <tr align=right><td align=center>2330</td><td>台積電</td><td>1,000</td><td>900</td><td>800</td><td>1</td></tr>
        <tr><td>1101</td><td>台泥</td><td>5</td><td>5</td><td>5</td></tr></table>"""
        rows = twse.parse_history(html, "2020-01")
        self.assertEqual(rows, [{"period": "2020-01", "code": "2330", "name": "台积电", "revenue": 1000.0, "revenue_ly": 800.0}])

    def test_roc(self):
        self.assertEqual(twse.roc_period("115/8"), "2026-08")
        self.assertIsNone(twse.roc_period("abc"))


class OldsiteTest(unittest.TestCase):
    def test_parse_and_filter(self):
        text = "date,ticker,company,revision_30d,next_fy_eps\n2026-09-27,NVDA,,19.46,15.68\n2026-09-27,MCD,,0.1,1\n"
        rows = oldsite.parse_table("eps", text)
        self.assertEqual([r["ticker"] for r in rows], ["NVDA"])

    def test_calendar_filter(self):
        cal = {"generated_at": "x", "events": [
            {"date": "2026-10-08", "category": "半导体", "title": "台积电9月营收"},
            {"date": "2026-10-01", "category": "财报", "title": "耐克（NKE）财报"},
            {"date": "2026-10-28", "category": "财报", "title": "微软（MSFT）财报"},
            {"date": "2026-10-14", "category": "财报", "tags": ["半导体"], "title": "ASML 业绩"},
        ]}
        titles = [e["title"] for e in oldsite.parse_calendar(json.dumps(cal))["events"]]
        self.assertEqual(titles, ["台积电9月营收", "微软（MSFT）财报", "ASML 业绩"])

    def test_importance(self):
        self.assertEqual(importance.rate("韩国10月1-20日出口（关税厅速报）")["stars"], 5)
        self.assertEqual(importance.rate("韩国9月进出口（产业通商部，全月初值）")["stars"], 4)
        self.assertEqual(importance.rate("美光（MU）财报")["stars"], 5)
        self.assertEqual(importance.rate("西部半导体展（旧金山，10月13-15日）")["stars"], 1)
        self.assertEqual(importance.rate("联电9月营收")["stars"], 3)


class KosisTest(unittest.TestCase):
    def test_parse(self):
        rows = kosis.parse(json.dumps([{"PRD_DE": "202608", "DT": "112.3"}, {"PRD_DE": "2026", "DT": "1"}]), "ship")
        self.assertEqual(rows, [{"period": "2026-08", "item": "ship", "value": 112.3}])
        with self.assertRaises(ValueError):
            kosis.parse(json.dumps({"err": "20", "errMsg": "인증키 오류"}), "ship")

    def test_no_key(self):
        old = os.environ.pop("KOSIS_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                rows, err = kosis.update(Path(d) / "k.csv")
            self.assertEqual(rows, [])
            self.assertTrue(err.startswith("未接入"))
        finally:
            if old is not None:
                os.environ["KOSIS_API_KEY"] = old


class RuleTest(unittest.TestCase):
    def test_ai_demand(self):
        st = I.ai_demand_state({"capex_yoy": 60, "capex_yoy_prev": 50, "tokens_30d": 50, "eps_ai": 5})
        self.assertEqual(st["label"], "加速")
        st = I.ai_demand_state({"capex_yoy": 25, "capex_yoy_prev": 40, "tokens_30d": 10, "eps_ai": 0})
        self.assertEqual(st["label"], "放缓")  # 票 +1、0、0 → 平均 0.33
        st = I.ai_demand_state({"capex_yoy": -5, "tokens_30d": -3})
        self.assertEqual(st["label"], "收缩")
        self.assertEqual(I.ai_demand_state({})["label"], "数据不足")

    def test_inventory_phases(self):
        self.assertEqual(I.inventory_state({"ship_yoy": 8, "inv_yoy": -4})["label"], "被动去库")
        self.assertEqual(I.inventory_state({"ship_yoy": 8, "inv_yoy": 12})["label"], "主动补库")
        self.assertEqual(I.inventory_state({"ship_yoy": -3, "inv_yoy": 5})["label"], "被动补库")
        self.assertEqual(I.inventory_state({"ship_yoy": -3, "inv_yoy": -8})["label"], "主动去库")
        self.assertEqual(I.inventory_state({"ship_yoy": 8, "inv_yoy": 12})["level"], -1)  # 库存涨得更快 → 偏松

    def test_price_window(self):
        # 7 天窗口用更窄的阈值
        self.assertEqual(I.price_state({"dram_chg": 3, "dram_window": 7})["label"], "涨价")
        self.assertEqual(I.price_state({"dram_chg": 3, "dram_window": 30})["label"], "企稳")

    def test_quadrant_and_env(self):
        up = {"label": "扩张", "level": 1}
        flat = {"label": "放缓", "level": 0}
        a = I.line_verdict("AI 算力", up, 1, "x")
        t = I.line_verdict("传统芯片", flat, -1, "y")
        self.assertEqual(a["name"], "景气上行")
        self.assertEqual(t["name"], "去库下行")
        env = I.environment(a, t, "领先指标走强，出货已确认")
        self.assertEqual(env["name"], "分化")
        self.assertIn("AI 算力景气上行", env["head"])
        self.assertEqual(I.environment(a, dict(a), "")["name"], "景气上行")

    def test_confirm(self):
        self.assertEqual(I.confirm_text(0.6, {"label": "走强"}), "领先指标走强，出货已确认")
        self.assertEqual(I.confirm_text(-0.6, {"label": "走强"}), "领先指标转弱，出货仍强，留意拐点")
        self.assertEqual(I.confirm_text(None, {"label": "走强"}), "领先指标不足")


def fixture_sources() -> B.Sources:
    """合成的一套数据：足够让每个维度都出标签。"""
    months = _months(date(2018, 1, 1), 104)  # 到 2026-08
    fred = {
        "A34SNO": [(d, 100 + i) for i, d in enumerate(months)],
        "A34SVS": [(d, 100 + i) for i, d in enumerate(months)],
        "A34STI": [(d, 100 + i * 0.5) for i, d in enumerate(months)],
        "IPG3344S": [(d, 100 + i * 0.8) for i, d in enumerate(months)],
        "CAPUTLG3344S": [(d, 78.0) for d in months],
        "PCU33443344": [(d, 100 + i * 0.1) for i, d in enumerate(months)],
    }
    twse_rows = []
    for i, d in enumerate(months):
        for code, base in (("2330", 100.0), ("2454", 40.0), ("2303", 20.0)):
            twse_rows.append({"period": d.isoformat()[:7], "code": code, "name": "", "revenue": base * (1.02 ** i), "revenue_ly": ""})
    sec_rows = []
    quarters = [date(y, m, 1) for y in range(2019, 2027) for m in (3, 6, 9, 12) if date(y, m, 1) < date(2026, 7, 1)]
    for i, q in enumerate(quarters):
        end = (ts._shift_month(q, 1) - __import__("datetime").timedelta(days=1)).isoformat()
        for t in ("MSFT", "GOOGL", "AMZN", "META", "ORCL"):
            sec_rows.append({"ticker": t, "item": "capex", "end": end, "value": 1e9 * (1.1 ** i)})
        for t in ("TXN", "MCHP", "ADI", "AMAT", "LRCX", "KLAC", "MU"):
            sec_rows.append({"ticker": t, "item": "revenue", "end": end, "value": 1e9 * (1.01 ** i)})
        for t in ("MU", "TXN", "MCHP"):
            sec_rows.append({"ticker": t, "item": "cogs", "end": end, "value": 5e8})
            sec_rows.append({"ticker": t, "item": "inventory", "end": end, "value": 4e8 * (1.005 ** i)})
    old = {
        "memory": [{"date": f"2026-09-{d:02d}", "product": p, "value": str(v + d * 0.2), "chg_7d_pct": "", "chg_30d_pct": ""}
                   for d in range(1, 28) for p, v in (("DDR5 16Gb spot", 27), ("DDR4 16Gb spot", 40), ("TLC 512Gb NAND wafer spot", 0.3))],
        "gpu": [{"date": "2026-09-27", "gpu": g, "price": "2", "chg_90d_pct": c} for g, c in (("H100 SXM", "16"), ("H200", "29"), ("B200", "68"))],
        "openrouter": [{"date": "2026-09-27", "window": "30日", "tokens": "543", "change_pct": "53"}],
        "silicon": [], "eps": [{"date": "2026-09-27", "ticker": t, "revision_30d": r} for t, r in
                               (("NVDA", "19"), ("TSM", "0.7"), ("AVGO", "-1"), ("QCOM", "-3"), ("INTC", "1"))],
        "korea": [{"period": "2026-09", "d10_yoy": "270", "d20_yoy": "259", "month_yoy": ""},
                  {"period": "2026-08", "month_usd_mn": "46829", "month_yoy": "206", "d20_yoy": "198"}],
        "calendar": {"events": [{"date": "2026-10-01", "time_bj": "", "title": "韩国9月进出口（产业通商部，全月初值）"},
                                {"date": "2026-09-30", "time_bj": "", "title": "美光（MU）财报", "consensus": "$31.24", "previous": "$2.86"},
                                {"date": "2026-12-30", "title": "太远了"}]},
    }
    return B.Sources(fred=fred, old=old, twse=twse_rows, sec=sec_rows, kosis=[],
                     manual=[{"date": "2026-09-01", "key": "dram_contract_ddr5", "value": "25"}])


class BuildTest(unittest.TestCase):
    def test_end_to_end(self):
        d = B.build_dashboard(fixture_sources(), date(2026, 9, 28), datetime(2026, 9, 28, 2, tzinfo=timezone.utc))
        keys = [x["key"] for x in d["dimensions"]]
        self.assertEqual(keys, ["ai_demand", "trad_demand", "inventory", "price", "capacity", "shipments"])
        labels = {x["key"]: x["label"] for x in d["dimensions"]}
        self.assertNotIn("数据不足", labels.values())
        self.assertEqual(labels["ai_demand"], "扩张")  # 资本开支同比 +46%、用量 +53%、EPS 平均 +6%
        self.assertEqual(labels["price"], "涨价")
        self.assertEqual(labels["shipments"], "走强")
        self.assertEqual(labels["inventory"], "主动补库")
        # 领先指标一览按时长排序，设备商不计入合计
        tiers = [x["tier"] for x in d["leading"]]
        self.assertEqual(tiers, sorted(tiers, key=["几天到几周", "1–3 个月", "1–2 个季度"].index))
        self.assertFalse(next(x for x in d["leading"] if x["name"] == "设备商营收同比")["score"])
        # 韩国出口：取最新期间里最完整的一档
        why = next(x for x in d["dimensions"] if x["key"] == "shipments")["why"]
        self.assertIn("2026-09 前 20 日", why[0]["t"])
        # 日历：只留 14 天内，按日期排序
        self.assertEqual([x["title"] for x in d["releases"]["upcoming"]],
                         ["美光（MU）财报", "韩国9月进出口（产业通商部，全月初值）"])
        self.assertEqual(d["releases"]["upcoming"][0]["forecast_text"], "EPS 预期 $31.24")
        # 没有 KOSIS 时用美国数据，图的说明写明
        self.assertIn("KOSIS_API_KEY", d["charts"]["inv_cycle"]["note"])
        # 每个指标的图都真实存在
        for dim in d["dimensions"]:
            for m in dim["metrics"]:
                if m["chart"]:
                    self.assertIn(m["chart"], d["charts"])
        self.assertIn(d["verdict"]["name"], set(I.QUAD.values()) | {"分化"})

    def test_empty_sources(self):
        d = B.build_dashboard(B.Sources(), date(2026, 9, 28))
        self.assertEqual(d["verdict"]["name"], "数据不足")
        self.assertEqual(d["leading"], [])

    def test_quarter_gap_no_accel(self):
        # 同比序列中间缺一季时，不拿隔季的数算加速
        src = fixture_sources()
        src.sec = [r for r in src.sec if not (r["item"] == "capex" and r["end"].startswith("2026-03"))]
        d = B.build_dashboard(src, date(2026, 9, 28))
        why = next(x for x in d["dimensions"] if x["key"] == "ai_demand")["why"][0]["t"]
        self.assertNotIn("比上季同比", why)


class StateLogTest(unittest.TestCase):
    def test_only_label_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "log.csv"
            statelog.update(p, "2026-09-01", [("a", "A", "扩张", "x")], lambda k: "t", write=True)
            statelog.update(p, "2026-09-02", [("a", "A", "扩张", "y")], lambda k: "t", write=True)
            rows = statelog.update(p, "2026-09-03", [("a", "A", "放缓", "z")], lambda k: "某数据", write=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["from"], "扩张")
        self.assertEqual(rows[0]["trigger"], "某数据")
        self.assertEqual(rows[1]["trigger"], "开始记录")


if __name__ == "__main__":
    unittest.main()
