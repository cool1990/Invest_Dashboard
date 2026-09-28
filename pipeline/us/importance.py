"""观察名单财报的重要程度。核心篮子进盈利和估值的中位数，星级更高。"""

from __future__ import annotations

import re

from .indicators import CORE

RULE = "核心篮子（盈利和估值中位数用到的 8 家）财报 5 星，观察名单上的其他美股财报 3 星。"

_TICKER = re.compile(r"（([A-Z][A-Z0-9.]*)）")


def tickers_in(title: str) -> list[str]:
    return _TICKER.findall(title or "")


def rate(title: str, watch: set[str]) -> dict | None:
    """不在美股观察名单上的条目返回 None，调用方不要放进即将发布。"""
    names = [t for t in tickers_in(title) if t in watch]
    if not names:
        return None
    if any(t in CORE for t in names):
        return {"stars": 5, "timing": "财报",
                "why": "这是核心篮子里的公司，远期市盈率和 30 日 EPS 修正会进入本站的盈利、估值判断。财报后分析师通常会改下财年预期。"}
    return {"stars": 3, "timing": "财报",
            "why": "在盈利跟踪的美股观察名单上，但不进核心篮子的中位数。财报会改这一家的远期市盈率和修正信号。"}
