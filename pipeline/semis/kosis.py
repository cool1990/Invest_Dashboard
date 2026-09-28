"""韩国统计局 KOSIS：半导体出货与库存指数（库存周期最好的领先指标）。

需要环境变量 KOSIS_API_KEY（在 https://kosis.kr/openapi/ 免费申请）。没有 key 时跳过，
库存周期退回美国「计算机与电子产品」的出货与库存（FRED A34SVS、A34STI）。

表：광업제조업동향조사（产业活动动向，统计厅 orgId=101）里按产业分的出货指数、库存指数，
取「반도체」一项，月度、2020=100。表号和项目号在拿到 key 后到 KOSIS 页面核对，
改下面 TABLES 即可；返回格式是 KOSIS 统一的 JSON（PRD_DE=YYYYMM，DT=数值）。
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from ..csvio import merge, read_csv, write_csv

URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
FIELDS = ["period", "item", "value"]
# item → 查询参数（待有 key 后核对）
TABLES = {
    "ship": {"orgId": "101", "tblId": "DT_1F01503", "itmId": "T1", "objL1": "C0026"},  # 出货指数：半导体
    "inv": {"orgId": "101", "tblId": "DT_1F01504", "itmId": "T1", "objL1": "C0026"},  # 库存指数：半导体
}


def parse(text: str, item: str) -> list[dict]:
    rows = []
    data = json.loads(text)
    if isinstance(data, dict):  # 出错时返回 {"err": ..., "errMsg": ...}
        raise ValueError(data.get("errMsg") or str(data)[:120])
    for r in data:
        p, v = str(r.get("PRD_DE", "")), r.get("DT")
        try:
            val = float(str(v).replace(",", ""))
        except ValueError:
            continue
        if len(p) == 6 and p.isdigit():
            rows.append({"period": f"{p[:4]}-{p[4:]}", "item": item, "value": val})
    return rows


def update(path: Path) -> tuple[list[dict], str | None]:
    """返回 (记录, 状态)。状态 None 表示成功，「未接入」表示没有 key。"""
    old = read_csv(path)
    key = os.environ.get("KOSIS_API_KEY", "").strip()
    if not key:
        return old, "未接入（没有 KOSIS_API_KEY）"
    new: list[dict] = []
    try:
        for item, q in TABLES.items():
            params = {"method": "getList", "apiKey": key, "format": "json", "jsonVD": "Y",
                      "prdSe": "M", "newEstPrdCnt": "240", "orgId": q["orgId"], "tblId": q["tblId"],
                      "itmId": q["itmId"], "objL1": q["objL1"]}
            with urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(params), timeout=40) as resp:
                new += parse(resp.read().decode("utf-8", errors="replace"), item)
    except Exception as exc:  # noqa: BLE001
        return old, str(exc)[:200]
    rows = merge(old, new, key=lambda r: (r["item"], r["period"]))
    write_csv(path, rows, FIELDS)
    return rows, None
