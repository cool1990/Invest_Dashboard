"""小 CSV 文件的读写与按主键合并。各板块累积的记录都用这几个函数。"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable, Iterable


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def merge(old: Iterable[dict], new: Iterable[dict], key: Callable[[dict], tuple]) -> list[dict]:
    """按主键合并，新的覆盖旧的，结果按主键排序。"""
    by_key = {key(r): r for r in old}
    for r in new:
        by_key[key(r)] = r
    return [by_key[k] for k in sorted(by_key)]
