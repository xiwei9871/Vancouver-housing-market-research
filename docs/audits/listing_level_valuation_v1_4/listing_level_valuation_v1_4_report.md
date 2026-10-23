# Listing-Level Valuation V1.4 — 报告

基准:HPI 2026-09;挂牌快照 2026-10-20;成本 v3s-2026-10(ptt+fin-1yr-dev-window)。
224 套 ACTIVE 中 224 套进入逐房源估值。

## 土地型旧房池(模块 A)

主池 age≥50 & BCA land_share≥95%: **1979** 笔;
辅助池 90-95%: **521** 笔;剔除 {"no_yb": 149, "no_tax": 31, "strata": 2, "no_lot": 2, "age_lt50": 2319}。

## Q1 Ask 低于独立土地估值

47 / 224 套 Ask < fair_land_value;≥5%: 33,≥10%: 16,≥15%: 10,≥20%: 3。

## Q3 基准 ROI 达标房源数(1年开发期,0% 未来涨跌,EXCL)

| 目标 | 达标房源数 |
|---|---|
| ≥10% | 73 |
| ≥20% | 10 |
| ≥30% | 1 |

## Q4 砍价后达标(≥10% 开发 ROI)

砍5%: 95 套;砍10%: 125 套;砍15%: 151 套。

## Q5 全门通过(土地合理 + verified 方案 + ROI≥10%;置信度展示不过滤)

33 套: 7455 WEST BOULEVARD, 2413 COLLINGWOOD STREET, 3368 PARKER STREET, 3148 E 1ST AVENUE, 3368 PARKER STREET, 3148 E 1ST AVENUE, 7455 WEST BOULEVARD, 2119 E 47TH AVENUE, 2566 VENABLES STREET, 1380 E 54TH AVENUE, 550 CASSIAR STREET, 2284 E 45TH AVENUE, 2461 DUNDAS STREET, 5884 ORMIDALE STREET, 3617 ADANAC STREET

## Q6 疑似假机会(ROI≥10% 但 LOW_CONFIDENCE)

53 套: 48 E 22ND AVENUE, 301 E 41ST AVENUE, 3141 E BROADWAY, 3148 E 1ST AVENUE, 3579 W 42ND AVENUE, 659 E 22ND AVENUE, 4940 KILLARNEY STREET, 817 W 64TH AVENUE, 3149 W KING EDWARD AVENUE, 522 E CORDOVA STREET, 5555 KNIGHT STREET, 4153 JOHN STREET, 2566 VENABLES STREET, 3524 W 16TH AVENUE, 2307 W 47TH AVENUE

## 口径

- fair_land_value = 匹配土地 comps 加权 P50 $/sf × lot_sqft;
  水位 MICRO_500M→COMM_LOTBAND→COMM_ADJ_BAND→COMM_ANY→NEARBY_ADJ→SIDE。
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
