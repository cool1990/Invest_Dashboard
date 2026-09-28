"""用标准库读 xlsx 的第一张表。只处理数字、共享字符串和单元格内文字。"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + ord(ch.upper()) - 64
        else:
            break
    return n - 1


def read_sheet(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
        sheet = next(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        root = ET.fromstring(z.read(sheet))
    rows: list[list[str]] = []
    for row in root.findall(f"{NS}sheetData/{NS}row"):
        vals: dict[int, str] = {}
        for c in row.findall(f"{NS}c"):
            ref = c.get("r") or ""
            idx = _col(ref) if ref else len(vals)
            kind = c.get("t")
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{NS}t"))
            else:
                v = c.find(f"{NS}v")
                raw = v.text if v is not None and v.text else ""
                text = shared[int(raw)] if kind == "s" and raw else raw
            vals[idx] = text
        if vals:
            rows.append([vals.get(i, "") for i in range(max(vals) + 1)])
    return rows
