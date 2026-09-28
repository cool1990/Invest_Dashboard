# Invest_Dashboard

更全、更详细的投资看板。静态网页，简体中文，按板块分页：

| 板块 | 页面 | 状态 |
|---|---|---|
| 美国宏观 | `macro.html` | 第一期 |
| 半导体与 AI | — | 规划中 |
| 加密货币 | — | 规划中 |
| 个股 | — | 规划中 |

网址：https://cool1990.github.io/Invest_Dashboard/

旧站 [cool1990/macro-dashboard](https://github.com/cool1990/macro-dashboard) 照常运行，两边互不影响。本仓库只读旧站公开的一份文件（市场隐含 EFFR，见下）。

## 宏观板块

页面自上而下四层：

1. **总判断**：一句话结论（由增长、通胀两维评分组合决定），加上所有「警示」「关注」级别的核心指标。
2. **五维评分**：每维一个分数、历史走势和一句小结（哪几项在抬高、哪几项在压低）。
3. **核心指标**：每维 2–6 项，列出最新值、预期与意外、前值、趋势和「这意味着什么」。
4. **数据发布**：最近 30 天的发布（实际 vs 市场一致预期）、未来 10 天的日程与预期、克利夫兰联储通胀 Nowcast。
5. **明细图表**：每维默认只展开核心图，其余收在「更多图表」里。

**解读**由 `pipeline/macro/interpret.py` 里的固定规则生成：按阈值（如 2% 通胀目标、Sahm 0.5、SOFR − IORB 0bp、曲线倒挂）、分位和 3 个月趋势套预写的句子，不用模型生成，结果可复现。阈值写在各函数里。

**预期值**：
- 市场一致预期：每天读 ForexFactory 公开的本周、下周日历 JSON（非官方接口，免费），只留美国高、中影响条目，累积到 `data/macro/consensus.csv`。发布前多次看到同一条目时取最后一次的预期。实际值取 FRED 当前值（可能已修订），按参考期（例如 10 月初公布的非农对应 9 月）对齐。需要手工补录时写 `data/macro/consensus_manual.csv`，列相同。
- 模型预测：GDPNow（对比实际 GDP）、克利夫兰联储通胀 Nowcast（CPI、核心 CPI、PCE、核心 PCE 同比），存 `data/macro/nowcast.csv`。
- 这两类来源从本站上线后才开始累积，历史意外要等数据发布几次后才有。

| 维度 | 看什么 |
|---|---|
| 增长 | 实际 GDP（季环比年化、同比、年度）、GDPNow、非农、失业率、U-6、Sahm 规则、职位空缺与空缺/失业比、离职率、时薪、初请与续请、零售销售与控制组、实际 PCE、储蓄率、消费者信贷、信用卡拖欠率、耐用品与核心资本品订单、地区联储制造业调查、新屋开工/许可/销售/库存、房价、房贷利率 |
| 通胀 | PCE 与核心 PCE（同比、环比、3/6 个月年化）、PCE 按贡献拆分（食品、能源、核心商品、住房、核心服务除住房）、CPI 与核心 CPI、5y5y、10 年盈亏平衡、密歇根一年期预期 |
| 流动性 | 美联储负债结构与准备金拆分（总资产 − ON RRP − TGA − 流通中货币 − 其他）、准备金 4 周变化的来源、SOFR − IORB、EFFR − IORB、NFCI、高收益利差、美元指数 |
| 财政 | 滚动 12 个月赤字、赤字率、财年赤字率、利息支出占 GDP、债务占 GDP |
| 货币政策 | EFFR 路径（市场隐含 vs 点阵图）、EFFR 与目标区间、2 年期国债、实际政策利率、2Y − EFFR、10Y−2Y 与 10Y−3M、10 年名义与实际利率 |

**评分**：每个维度选 4–8 项，按 2000 年以来的均值和标准差算 z 分数（截断在 ±3），方向统一后等权平均。高于 +0.5 或低于 −0.5 视为明显偏离常态。各项的方向写在 `pipeline/macro/build.py` 各维度函数末尾的 `comps` 里。

**PCE 与核心 PCE 按贡献拆分**：某项贡献 ≈ 基期（环比用上月、同比用 12 个月前）的名义支出份额 × 该项价格变化。整体 PCE 拆成食品 / 能源 / 核心商品 / 住房 / 超级核心；核心 PCE 拆成核心商品 / 住房 / 超级核心（服务除能源、住房）。FRED 没有月度住房支出、月度汽油支出和月度核心商品价格，所以：住房名义 = 核心 × 上一年住房占核心的比重（年度）；核心商品名义 = 商品 − 食品 − 能源 × 能源商品占能源的比重（最近一季）；超级核心名义 = 核心 − 核心商品 − 住房。价格用核心 PCE、核心除住房（IA001176M）、超级核心（IA001260M）三条月度指数，住房和核心商品的贡献由差额得到。链式加总不严格可加，误差一般在 0.01 个百分点量级。

**ISM PMI** 不在 FRED，暂时用费城联储和纽约联储的制造业调查代替。

## 数据

- 来源：圣路易斯联储 [FRED](https://fred.stlouisfed.org/) 的公开 CSV，不需要 API key。序列清单在 `pipeline/macro/indicators.py`。
- 全历史下载，原样存在 `data/raw/fred/<ID>.csv`（列 `date,value`，数值和单位与 FRED 一致）。图表默认显示近 5 年，可切 2 年 / 10 年 / 全部（图表数据从 1990 年起，日频压成周频）。
- 市场隐含 EFFR（下月 / 年底 / 明年底）来自每天早晨的笔记。旧站已把它整理进 `data/sentiment/series.csv`，本仓库从那份公开文件里只挑这三行，累积到 `data/macro/effr_expectations.csv`。
- `data/macro/dashboard.json`：页面读的唯一文件。
- `data/macro/status.json`：每条序列这次有没有下载成功、最新观测日期。下载失败的序列沿用上次的文件，页面底部「数据状态」会列出来。

## 文件夹

```
pipeline/series.py            时间序列小工具（变化率、滚动、按日期对齐）
pipeline/fred.py              FRED 下载与读写
pipeline/macro/indicators.py  宏观序列清单
pipeline/macro/build.py       派生计算、图表、关键读数、评分
pipeline/macro/effr_expect.py 市场隐含 EFFR
pipeline/macro/consensus.py   市场一致预期（经济日历）与克利夫兰联储 Nowcast
pipeline/macro/interpret.py   规则解读与总判断
scripts/update_macro.py       更新宏观数据（下载 + 生成）
scripts/build_site.py         构建网页到 dist/
tests/                        计算测试
index.html  macro.html        页面
assets/                       样式、脚本、Chart.js（本地打包，不依赖 CDN）
```

以后加板块时，按同样的结构新增 `pipeline/<板块>/`、`data/<板块>/`、`<板块>.html`，互不干扰。

## 自动更新

- `.github/workflows/update-macro.yml`：每天 22:40 UTC（北京时间 06:40）跑测试、下载、生成；数据有变化就提交并发布网页。也可以在 Actions 页面手动运行。
- `.github/workflows/pages.yml`：改网页或合并到 `main` 时发布。

首次使用需要在仓库 **Settings → Pages → Build and deployment → Source** 选 **GitHub Actions**。

## 本地运行

只用 Python 3.12 标准库。

```bash
python3 scripts/update_macro.py            # 下载并生成（需要能访问 FRED）
python3 scripts/update_macro.py --offline  # 只用已下载的 CSV 重新生成
python3 -m unittest discover -s tests
python3 scripts/build_site.py && python3 -m http.server 8000 -d dist
```

浏览器打开 http://localhost:8000/ 。页面用 `fetch` 读数据，不能直接双击 html 打开。
