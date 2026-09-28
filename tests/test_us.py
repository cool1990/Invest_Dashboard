from __future__ import annotations

import io
import sys
import unittest
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.us import build as B  # noqa: E402
from pipeline.us import finra, holdings, importance, oldsite  # noqa: E402
from pipeline.us import interpret as I  # noqa: E402
from pipeline.us.indicators import CORE  # noqa: E402
from pipeline.us.xlsxio import read_sheet  # noqa: E402


def _row(ticker, rev, pe, signal="中性", status="OK", flags="", market="US"):
    return {"date": "2026-09-27", "ticker": ticker, "market": market, "revision_30d": str(rev),
            "forward_pe": str(pe), "revision_signal": signal, "status": status, "flags": flags,
            "rsi": "50", "company": ticker}


def _earn_rows(revs, pes, signals):
    rows = []
    for t, rev, pe, sig in zip(CORE, revs, pes, signals):
        rows.append(_row(t, rev, pe, sig))
    return rows


class InterpretTest(unittest.TestCase):
    def test_revision_bands_and_spread(self):
        up = I.earnings_state({"rev": 2.01, "n": 8, "n_up": 5, "n_down": 1})
        self.assertEqual(up["label"], "上修")
        self.assertEqual(up["level"], 1)
        self.assertIn("扩散", up["summary"])

        flat = I.earnings_state({"rev": 2.0, "n": 8, "n_up": 4, "n_down": 1})
        self.assertEqual(flat["label"], "平稳")  # 高于 +2% 才算上修
        self.assertIn("强上修", flat["summary"])

        down = I.earnings_state({"rev": -2.01, "n": 8, "n_up": 0, "n_down": 4})
        self.assertEqual(down["label"], "下修")
        self.assertEqual(down["level"], -1)

        self.assertEqual(I.earnings_state({"rev": None})["label"], "数据不足")

    def test_erp_bands(self):
        # 25 倍 → 盈利收益率 4%；实际利率 2.5% → 溢价 1.5%，贵
        rich = I.valuation_state({"pe": 25, "erp": 1.5, "real": 2.5, "manual": False})
        self.assertEqual(rich["label"], "贵")
        self.assertEqual(rich["level"], -1)
        self.assertIn("25.0", rich["head"])

        fair = I.valuation_state({"pe": 20, "erp": 3, "real": 2, "manual": False})
        self.assertEqual(fair["label"], "大致合理")
        self.assertEqual(fair["level"], 0)

        # 刚好 2% 和 4% 落在「大致合理」
        self.assertEqual(I.valuation_state({"pe": 20, "erp": 2, "real": 3, "manual": False})["label"], "大致合理")
        self.assertEqual(I.valuation_state({"pe": 16, "erp": 4, "real": 2.25, "manual": False})["label"], "大致合理")

        cheap = I.valuation_state({"pe": 15, "erp": 4.01, "real": 2, "manual": True})
        self.assertEqual(cheap["label"], "便宜")
        self.assertIn("手工", cheap["head"])

        missing_rate = I.valuation_state({"pe": 20, "erp": None, "real": None, "manual": False})
        self.assertEqual(missing_rate["label"], "数据不足")
        self.assertTrue(missing_rate["why"])

    def test_sentiment_parts_do_not_cancel(self):
        st = I.sentiment_state({
            "vix": 14, "cnn": 20, "aaii": -18, "breadth": 30,
            "margin_yoy": 25, "margin_gap": 12, "top10": 35,
        })
        self.assertIn("平静，但调查偏悲观", st["label"])
        self.assertIn("参与度面窄", st["label"])
        self.assertIn("杠杆扩张", st["label"])
        self.assertIn("集中", st["label"])
        self.assertIn("快于指数", st["summary"])
        self.assertEqual(len(st["why"]), 4)

        calm = I.sentiment_state({"vix": 18, "breadth": 55, "margin_yoy": -5, "top10": 22})
        self.assertIn("风险偏好正常", calm["label"])
        self.assertIn("杠杆去化", calm["label"])
        self.assertIn("集中度未过线", calm["label"])
        self.assertNotIn("面窄", calm["label"])

    def test_environment_ignores_sentiment(self):
        earn = I.earnings_state({"rev": 3, "n": 8, "n_up": 1, "n_down": 1})
        val = I.valuation_state({"pe": 25, "erp": 1, "real": 3, "manual": False})
        sent = I.sentiment_state({"vix": 40, "breadth": 20, "margin_yoy": -30, "top10": 15})
        env = I.environment(earn, val, sent)
        self.assertEqual(env["name"], "涨但偏贵")
        self.assertEqual([x["k"] for x in env["lines"]], ["盈利", "估值", "情绪"])


class ParseTest(unittest.TestCase):
    def test_us_filter_and_sentiment(self):
        rows = oldsite.parse_earnings(
            "date,ticker,company,market,close,rsi,forward_pe,target_pe,triggered,next_fy_eps,"
            "revision_30d,up30,down30,revision_signal,status,flags,is_trading_day\n"
            "2026-09-27,AAPL,Apple,US,1,50,20,,0,,1.2,1,0,强上修,OK,,true\n"
            "2026-09-27,0700.HK,Tencent,HK,1,50,12,,0,,-1,0,1,强下修,OK,,true\n")
        self.assertEqual([r["ticker"] for r in rows], ["AAPL"])
        sent = oldsite.parse_sentiment("date,series_id,name,value,unit,change_text,label,obs_date\n"
                                       "2026-09-27,aaii,AAII,-15.4,百分点,,,2026-09-23\n"
                                       "2026-09-27,btc,btc,1,美元,,,2026-09-27\n")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["series_id"], "aaii")

    def test_margin_yoy_table(self):
        header = ["Year-Month", "Debit Balances in Customers' Securities Margin Accounts",
                  "Free Credit Balances in Customers' Cash Accounts",
                  "Free Credit Balances in Customers' Securities Margin Accounts"]
        rows = [header, ["2025-08", "1000", "1", "2"], ["2026-08", "1,300", "1", "2"], ["Aug-26", "999", "1", "2"]]
        parsed = finra.parse_table(rows)
        # 2026-08 出现两次时由后面的调用方合并；解析本身两行都留，Aug-26 也是 2026-08
        self.assertEqual(parsed[0]["date"], "2025-08-01")
        self.assertEqual(parsed[1]["debit"], "1300.0")
        self.assertEqual(finra.parse_month("Aug-26"), date(2026, 8, 1))
        self.assertIn("margin-statistics.xlsx", finra.xlsx_url('<a href="/sites/default/files/2021-03/margin-statistics.xlsx">'))

    def test_top10(self):
        text = 'Fund Holdings as of,"Sep 25, 2026"\nTicker,Name,Asset Class,Weight (%)\n'
        body = "\n".join(f"T{i},Name {i},Equity,{10 - i * 0.1}" for i in range(12))
        text += body + "\nCASH,Cash,Cash,1\n"
        row = holdings.parse_ivv(text)
        self.assertEqual(row["date"], "2026-09-25")
        self.assertEqual(row["source"], "IVV")
        self.assertAlmostEqual(float(row["top10"]), sum(10 - i * 0.1 for i in range(10)), places=3)
        self.assertIsNone(holdings.parse_ivv("<!DOCTYPE html><html></html>"))
        # 小数权重会乘回百分数
        small = 'as of 2026-09-25\nTicker,Name,Weight (%)\n' + "\n".join(
            f"T{i},N,{0.10 - i * 0.005}" for i in range(10))
        scaled = holdings.parse_ivv(small)
        self.assertAlmostEqual(float(scaled["top10"]), sum((0.10 - i * 0.005) * 100 for i in range(10)), places=3)

    def test_xlsx_roundtrip_and_spy(self):
        sheet = _xlsx([
            ["Holdings:", "As of 25-Sep-2026"],
            ["Name", "Ticker", "Weight"],
            ["NVIDIA CORP", "NVDA", "8.5"],
            ["APPLE INC", "AAPL", "7.5"],
            *[[f"N{i}", f"T{i}", str(5 - i * 0.1)] for i in range(8)],
            ["CASH", "-", ""],
        ])
        self.assertEqual(read_sheet(sheet)[1][1], "Ticker")
        row = holdings.parse_spy(sheet)
        self.assertEqual(row["date"], "2026-09-25")
        self.assertEqual(row["source"], "SPY")
        self.assertEqual(row["names"].split(",")[0], "NVDA")
        self.assertGreater(float(row["top10"]), 30)


def _xlsx(rows: list[list[str]]) -> bytes:
    body = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, val in enumerate(row):
            col = chr(ord("A") + c)
            cells.append(f'<c r="{col}{r}" t="inlineStr"><is><t>{escape(val)}</t></is></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   "</Relationships>")
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


class BuildTest(unittest.TestCase):
    def test_dashboard_uses_basket_not_outliers(self):
        rows = _earn_rows(
            [0.5, 0.4, 19, 0.2, 0.3, 0.6, -0.5, 0.1],
            [36, 27, 19, 21, 27, 24, 17, 16],
            ["强下修", "强上修", "强上修", "强上修", "强上修", "强下修", "温和下修", "温和上修"],
        )
        rows.append(_row("MSTR", 150, 5, "温和上修"))
        rows.append(_row("IREN", "", "", "样本不足", status="NEGATIVE_EPS", flags="LOW_SAMPLE"))
        # 再放一天，确认中位数按天算
        for r in list(rows):
            if r["ticker"] in CORE:
                rows.append({**r, "date": "2026-09-26", "revision_30d": "3"})
        src = B.Sources(
            fred={
                "DFII10": [(date(2026, 9, 26), 2.8), (date(2026, 9, 27), 2.85)],
                "SP500": [(date(2025, 9, 30), 5000), (date(2026, 9, 27), 6000)],
                "VIXCLS": [(date(2026, 9, 27), 14.5)],
                "CPATAX": [], "NCBEILQ027S": [], "GDP": [],
            },
            earnings=rows,
            sentiment=[{"date": "2026-09-27", "series_id": "spx_breadth_200", "value": "30", "obs_date": "2026-09-25"},
                       {"date": "2026-09-27", "series_id": "aaii", "value": "-16", "obs_date": "2026-09-23"}],
            calendar={"events": [
                {"date": "2026-10-02", "time_bj": "", "title": "耐克（NKE）财报", "consensus": "1", "previous": ""},
                {"date": "2026-10-02", "time_bj": "", "title": "甲骨文（ORCL）财报", "consensus": "", "previous": ""},
                {"date": "2026-10-13", "time_bj": "", "title": "摩根大通（JPM）财报", "consensus": "", "previous": ""},
            ]},
            margin=[{"date": "2025-09-01", "debit": "1000"}, {"date": "2026-09-01", "debit": "1300"}],
            concentration=[{"date": "2026-09-25", "top10": "38.2", "names": "NVDA,AAPL", "source": "SPY"}],
        )
        dash = B.build_dashboard(src, date(2026, 9, 28), datetime(2026, 9, 28, 3, tzinfo=timezone.utc))
        labels = {d["key"]: d["label"] for d in dash["dimensions"]}
        self.assertEqual(labels["earnings"], "平稳")  # 中位数约 0.35，不是被 NVDA 的 19% 拉成上修
        self.assertEqual(labels["valuation"], "贵")
        self.assertIn("杠杆扩张", labels["sentiment"])
        self.assertIn("集中", labels["sentiment"])
        self.assertEqual(dash["verdict"]["name"], "估值偏贵")
        tickers = [n["ticker"] for n in dash["names"]]
        self.assertEqual(tickers[0], "AAPL")
        self.assertIn("IREN", tickers)
        titles = [e["title"] for e in dash["releases"]["upcoming"]]
        self.assertEqual(titles, ["甲骨文（ORCL）财报"])  # NKE 不在观察名单，JPM 也不在；ORCL 在 14 天内
        self.assertEqual(dash["releases"]["upcoming"][0]["importance"]["stars"], 5)
        self.assertIn("rev", dash["charts"])
        self.assertIn("margin", dash["charts"])

    def test_manual_pe_overrides_basket(self):
        rows = _earn_rows([1] * 8, [20] * 8, ["中性"] * 8)
        src = B.Sources(
            fred={"DFII10": [(date(2026, 9, 27), 1.0)], "SP500": [], "VIXCLS": [], "CPATAX": [], "NCBEILQ027S": [], "GDP": []},
            earnings=rows,
            manual=[{"date": "2026-09-27", "key": "spx_fwd_pe", "value": "15"}],
        )
        dash = B.build_dashboard(src, date(2026, 9, 28))
        val = next(d for d in dash["dimensions"] if d["key"] == "valuation")
        self.assertEqual(val["label"], "便宜")  # 100/15 - 1 = 5.67 > 4
        ids = [m["id"] for m in val["metrics"]]
        self.assertIn("basket_pe", ids)


class ImportanceTest(unittest.TestCase):
    def test_only_watchlist(self):
        watch = set(CORE) | {"NKE"}
        self.assertIsNone(importance.rate("摩根大通（JPM）财报", watch))
        self.assertEqual(importance.rate("耐克（NKE）财报", watch)["stars"], 3)
        self.assertEqual(importance.rate("微软（MSFT）财报", watch)["stars"], 5)


if __name__ == "__main__":
    unittest.main()
