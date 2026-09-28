"""半导体板块的指标目录：序列、公司、领先指标一览。

两条线分开判断：ai（AI 算力）与 trad（传统芯片：手机、PC、成熟制程、模拟、通用存储）。
阈值不在这里，在 interpret.py。
"""

from __future__ import annotations

# FRED 序列：id → (名称, 单位, 频率)。与宏观共用 data/raw/fred/。
# 行业口径：3344 = 半导体及其他电子元件；34 = 计算机与电子产品（M3 调查不单独公布半导体的订单）。
FRED = {
    "IPG3344S": ("美国工业产出：半导体及其他电子元件", "指数 2017=100", "M"),
    "CAPUTLG3344S": ("美国产能利用率：半导体及其他电子元件", "%", "M"),
    "PCU33443344": ("PPI：半导体及其他电子元件", "指数", "M"),
    "A34SNO": ("美国新订单：计算机与电子产品", "百万美元", "M"),
    "A34SVS": ("美国出货：计算机与电子产品", "百万美元", "M"),
    "A34STI": ("美国库存：计算机与电子产品", "百万美元", "M"),
}

# 台湾上市公司月营收：代号 → (简称, 所属线, 说明)
TWSE = {
    "2330": ("台积电", "ai", "晶圆代工，先进制程与 AI 加速器的主要产能"),
    "2382": ("广达", "ai", "AI 服务器组装"),
    "6669": ("纬颖", "ai", "云厂商自研服务器组装"),
    "3037": ("欣兴", "ai", "ABF 载板，AI 芯片封装"),
    "2303": ("联电", "trad", "成熟制程代工"),
    "2454": ("联发科", "trad", "手机与消费电子芯片"),
    "3711": ("日月光投控", "trad", "封装测试"),
    "2408": ("南亚科", "trad", "通用 DRAM"),
    "2344": ("华邦电", "trad", "利基型 DRAM 与 NOR Flash"),
}
TW_AI_CONFIRM = ("2330",)  # 出货确认用台积电
TW_TRAD_DEMAND = ("2454", "2303")  # 传统需求用联发科 + 联电

# SEC 季报：代号 → (CIK, 名称, 分组)
#   cloud  云厂商，看资本开支（AI 需求的源头）
#   ai     AI 芯片
#   memory 存储
#   analog 模拟与 MCU（传统需求，周期性最典型）
#   equip  半导体设备（供给侧的扩产）
SEC = {
    "MSFT": (789019, "微软", "cloud"),
    "GOOGL": (1652044, "Alphabet", "cloud"),
    "AMZN": (1018724, "亚马逊", "cloud"),
    "META": (1326801, "Meta", "cloud"),
    "ORCL": (1341439, "甲骨文", "cloud"),
    "NVDA": (1045810, "英伟达", "ai"),
    "AMD": (2488, "AMD", "ai"),
    "AVGO": (1730168, "博通", "ai"),
    "MU": (723125, "美光", "memory"),
    "TXN": (97476, "德州仪器", "analog"),
    "MCHP": (827054, "微芯", "analog"),
    "ADI": (6281, "亚德诺", "analog"),
    "AMAT": (6951, "应用材料", "equip"),
    "LRCX": (707549, "泛林", "equip"),
    "KLAC": (319201, "科磊", "equip"),
}

# 每日笔记里的 EPS 修正（下财年 EPS 30 天变化 %）
EPS_AI = ("NVDA", "TSM", "AVGO")
EPS_TRAD = ("QCOM", "INTC")
EPS_KEEP = EPS_AI + EPS_TRAD + ("MU", "SNDK", "AMD", "MSFT", "GOOGL", "AMZN", "META", "ORCL")

# 存储现货：旧站 memory.csv 的 product
DRAM = ("DDR5 16Gb spot", "DDR4 16Gb spot")
NAND = "TLC 512Gb NAND wafer spot"
SSD = "零售 SSD 2TB"
GPUS = ("H100 SXM", "H200", "B200")

# 手工 / 笔记补录（data/semis/manual.csv 的 key）
MANUAL = {
    "dram_contract_ddr5": ("DDR5 16Gb 合约价", "USD/GB"),
    "dram_contract_ddr4": ("DDR4 16Gb 合约价", "USD/GB"),
    "nand_contract": ("NAND 合约价", "USD/GB"),
    "asml_bookings": ("ASML 季度新订单", "亿欧元"),
    "asml_sales": ("ASML 季度销售", "亿欧元"),
    "tsmc_capex_guide": ("台积电全年资本开支指引（中值）", "亿美元"),
    "tsmc_util": ("台积电产能利用率", "%"),
    "lead_time": ("芯片交期", "周"),
}

# 领先多久（领先指标一览按这个分组）
TIERS = ("几天到几周", "1–3 个月", "1–2 个季度")
LINES = {"ai": "AI 算力", "trad": "传统芯片", "both": "两条线"}
