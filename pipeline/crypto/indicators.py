"""加密货币板块的指标目录。阈值在 interpret.py，这里只放来源和字段名。"""

from __future__ import annotations

# Coin Metrics 社区接口里、这次用得到的字段。
# CapRealUSD、IssContNtv 不在免费目录里：实现价格用价格 / MVRV，新供给用 IssTotNtv（含手续费）。
CM_METRICS = ("PriceUSD", "CapMrktCurUSD", "CapMVRVCur", "SplyCur", "IssTotNtv")

# BGeometrics 免费层每天大约 15 次。固定拉这 5 个，不再加。
# 持有者成本、长线 SOPR、币龄是别处没有的；主导率是因为 CoinGecko 的历史总市值要 key。
BG_ENDPOINTS = {
    "sth_price": ("sth-realized-price", "sthRealizedPrice"),
    "lth_price": ("lth-realized-price", "lthRealizedPrice"),
    "lth_sopr": ("lth-sopr", "lthSopr"),
    "dominance": ("bitcoin-dominance", "bitcoinDominance"),
}

# HODL 波段里「1 年以上」。不含 1 天、1 周、1 个月。
HODL_LTH = (
    "age_1y_2y", "age_2y_3y", "age_3y_4y", "age_4y_5y", "age_5y_7y", "age_7y_10y", "age_10y",
)

# 手工补录：data/crypto/manual.csv。Farside 改版或被拦时，按日补 ETF 净流入（百万美元）。
MANUAL_ETF = {"etf_btc_usd_mn": "比特币 ETF", "etf_eth_usd_mn": "以太坊 ETF"}
