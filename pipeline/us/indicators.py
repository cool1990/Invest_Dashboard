"""美股板块的名单、FRED 序列和旧站情绪序列。

核心篮子是盈利跟踪笔记里、能代表美股大盘的 8 家。观察名单上还有中概和加密相关公司，
那些只列在表里，不进中位数。
"""

from __future__ import annotations

# 顺序即页面上核心篮子的顺序
CORE = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "ORCL")

# 键是 FRED 序列 ID；(中文名, 原始单位, 频率)
# DFII10、GDP 宏观板块已经在下，这里只读不重复下载。
FRED: dict[str, tuple[str, str, str]] = {
    "SP500": ("标普 500", "指数", "日"),
    "VIXCLS": ("VIX", "指数", "日"),
    "CPATAX": ("税后企业利润", "十亿美元（年化）", "季"),
    "NCBEILQ027S": ("非金融企业股权市值", "百万美元", "季"),
}

FRED_READ = ("DFII10", "GDP")

# 旧站 data/sentiment/series.csv 里要累积的序列。20 日、50 日和纳指广度只作参考图。
SENTIMENT = (
    "vix", "cnn_fg", "aaii", "spx_rsi", "nasdaq_rsi",
    "spx_breadth_20", "spx_breadth_50", "spx_breadth_200",
    "ndx_breadth_20", "ndx_breadth_50", "ndx_breadth_200",
)

# 手工表 data/us/manual.csv 的 key。有 spx_fwd_pe 时，估值改用它。
MANUAL_PE = "spx_fwd_pe"
