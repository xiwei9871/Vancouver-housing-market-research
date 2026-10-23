# Listing-Level Valuation V1.4 — 报告

基准:HPI 2026-09;挂牌快照 2026-10-20;成本 v3s-2026-10(ptt+fin-1yr-dev-window)。
224 套 ACTIVE 中 224 套进入逐房源估值。

## 土地型旧房池(模块 A)

主池 age≥50 & BCA land_share≥95%: **1979** 笔;
辅助池 90-95%: **521** 笔;剔除 {"no_yb": 149, "no_tax": 31, "strata": 2, "no_lot": 2, "age_lt50": 2319}。

## Q1 Ask 低于独立土地估值

91 / 224 套 Ask < fair_land_value;≥5%: 59,≥10%: 35,≥15%: 22,≥20%: 8。

## Q3 基准 ROI 达标房源数(1年开发期,0% 未来涨跌,EXCL)

| 目标 | 达标房源数 |
|---|---|
| ≥10% | 74 |
| ≥20% | 15 |
| ≥30% | 2 |

## Q4 砍价后达标(≥10% 开发 ROI)

砍5%: 93 套;砍10%: 120 套;砍15%: 142 套。

## Q5 全门通过(土地合理 + verified 方案 + ROI≥10%;置信度展示不过滤)

n_listings=45 / n_scenarios=53:
1060 KELOWNA STREET, 1333 E 41ST AVENUE, 1349 E 14TH AVENUE, 1380 E 54TH AVENUE, 1621 E 58TH AVENUE, 2119 E 47TH AVENUE, 2284 E 45TH AVENUE, 2413 COLLINGWOOD STREET, 2461 DUNDAS STREET, 2554 ADANAC STREET, 2561 MCGILL STREET, 2566 VENABLES STREET, 2733 E 28TH AVENUE, 301 E 41ST AVENUE, 3055 ROSEMONT DRIVE

## Q6 疑似假机会(ROI≥10% 但 LOW_CONFIDENCE)

n_listings=38 / n_scenarios=40:
1621 E 58TH AVENUE, 1661 E 49TH AVENUE, 1861 E KENT AVENUE NORTH, 1972 E 51ST AVENUE, 2119 E 47TH AVENUE, 2175 WAVERLEY AVENUE, 2284 E 45TH AVENUE, 2307 W 47TH AVENUE, 2461 DUNDAS STREET, 2554 ADANAC STREET, 2561 MCGILL STREET, 2566 VENABLES STREET, 266 E 32ND AVENUE, 2812 E 18TH AVENUE, 2935 ADANAC STREET

## 口径

- fair_land_value = 匹配土地 comps 加权 P50 $/sf × lot_sqft;
  水位 MICRO_500M→COMM_LOTBAND→COMM_ADJ_BAND→COMM_ANY→NEARBY_ADJ→SIDE。
  V1.4.2:挂牌 hood→档案社区别名表(HOOD_COMM_ALIAS,按质心距离
  核定,见 land_valuation.hood_comm 列);NEARBY 回退的社区间换算
  已修正为 目标/来源 比值;`land_nearby_adj_fix_diff.csv` 留存
  修正前后差异(106 套原回退房源,现已全部命中社区级或更优匹配)。
- GST 口径:EXCL=售价为未税净价、5% 记成本行;INCL=售价为含税
  总价、成本行记实际汇缴 sale×5/105——两者只扣一次。
- est_sale = 匹配新房 comps 加权 P50 $/sf × net_saleable;DUPLEX/MPX
  comps 为代理指数归一化(PROXY 标),MPX ×0.85 产品折价。
- ROI 层级:roi_simple=ASK+建安;roi_development_1y=V3s 全成本
  (含 PTT/办证/1年融资/销售/GST),统一 1 年开发融资窗口;
  未来出售时间与 ROI 分离——市场 ±10/20% 仅为独立价格压力情景,
  不再有 3y/5y ROI;开发前长期持有成本独立记录,不叠加。
- max_bid 以 1y 开发期/EXCL 为基准求满足目标 ROI 的最高出价;
  土地白送仍不达标 = UNATTAINABLE。
- utility_flag=EXISTING_HOUSE_UTILITY(改善占比>10%)提示房源含
  居住价值,土地估值与房价需分开看。
