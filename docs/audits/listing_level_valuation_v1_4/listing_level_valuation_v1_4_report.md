# Listing-Level Valuation V1.4 — 报告

基准:HPI 2026-09;挂牌快照 2026-10-20;成本 v3s-2026-10(ptt+fin-1yr-dev-window)。
224 套 ACTIVE 中 224 套进入逐房源估值。

## 土地型旧房池(模块 A)

主池 age≥50 & BCA land_share≥95%: **1979** 笔;
辅助池 90-95%: **521** 笔;剔除 {"no_yb": 149, "no_tax": 31, "strata": 2, "no_lot": 2, "age_lt50": 2319}。

## Q1 Ask 低于独立土地估值

103 / 224 套 Ask < fair_land_value;≥5%: 69,≥10%: 42,≥15%: 21,≥20%: 9。

## Q3 基准 ROI 达标房源数(1年开发期,0% 未来涨跌,EXCL)

| 目标 | 达标房源数 |
|---|---|
| ≥10% | 74 |
| ≥20% | 15 |
| ≥30% | 2 |

## Q4 砍价后达标(≥10% 开发 ROI)

砍5%: 93 套;砍10%: 120 套;砍15%: 142 套。

## Q5 全门通过(土地合理 + verified 方案 + ROI≥10%;置信度展示不过滤)

n_listings=45 / n_scenarios=54:
1060 KELOWNA STREET, 1349 E 14TH AVENUE, 1380 E 54TH AVENUE, 1621 E 58TH AVENUE, 1861 E KENT AVENUE NORTH, 2119 E 47TH AVENUE, 2175 WAVERLEY AVENUE, 2413 COLLINGWOOD STREET, 2461 DUNDAS STREET, 2554 ADANAC STREET, 2561 MCGILL STREET, 2566 VENABLES STREET, 301 E 41ST AVENUE, 3055 ROSEMONT DRIVE, 309 E WOODSTOCK AVENUE

## Q6 疑似假机会(ROI≥10% 但 LOW_CONFIDENCE)

n_listings=40 / n_scenarios=42:
1621 E 58TH AVENUE, 1661 E 49TH AVENUE, 1861 E KENT AVENUE NORTH, 1972 E 51ST AVENUE, 2119 E 47TH AVENUE, 2175 WAVERLEY AVENUE, 2284 E 45TH AVENUE, 2307 W 47TH AVENUE, 2413 COLLINGWOOD STREET, 2461 DUNDAS STREET, 2554 ADANAC STREET, 2561 MCGILL STREET, 2566 VENABLES STREET, 266 E 32ND AVENUE, 2733 E 28TH AVENUE

## V1.4.4 数据核查与修正(2026-10 复审)

**P0 lot 字段血缘修正(根因)**:主池 lot 原取 ArcGIS parcel
SHAPE_Area——该字段可含共有通道/坡地/附属地块,实测
`bca-arcgis-sales-…-enriched-spatial.json` 的
BCA_PROPERTY_INFORMATION_DIMENSIONS(法定面宽×进深)显示同一
parcel 面积普遍虚高 ~2.3×(例:1785 CASSIAR GIS 10,000sf vs
BCA dims 4,254sf)。修正:主池 lot 优先采用 BCA dims(按
pid+sale_date 连接,覆盖 1269/1979 笔,其中 707 笔纠偏>1.5×
记 `LOT_CORRECTED_2X`);无 dims 覆盖且 lot≥6000 的记
`LOT_GIS_UNVERIFIED_BIG`(649 笔),占比过半的匹配结果降级为
LOW 并标 `UNVERIFIED_BIG_COMPS`。逐笔血缘见
`land_pool_lot_audit.csv`。修正后主池 ≥6000sf 成交由 1448 笔
(73.2%)降为 852 笔(43.0%)——原"大地块"多数是面积虚增。

**匹配约束**:LOT_BANDS 细分(6000-8000/8000-12000/12000+),
MICRO_500M/COMM_LOTBAND/COMM_ADJ_BAND/COMM_ANY/NEARBY 各层统一加
lot 比 0.6–1.67× 实际面积约束;p75/p25>1.4 的双峰证据不得给
HIGH_CONFIDENCE。guard 修正前后差异见
`land_lot_compat_fix_diff.csv`(3 套),逐笔 comp
见 `problem_listing_comp_audit.csv`。

| 房源 | 前层级 | 前$/sf | 前估值 | 后层级 | 后$/sf | 后估值 |
|---|---|---|---|---|---|---|
| 3151 HEATHER STREET | COMM_ANY | $445.8/sf | $936,259 | NEARBY_COMM_1.5KM_ADJ | $682.7/sf | $1,433,706 |

**P1 zoning 核验**:production verified 标志对空 zoning、C-*/CD/
DEOD 也给出 VERIFIED——共 13 套 ['2284 E 45TH AVENUE', '2316 NANAIMO STREET', '2474 E 24TH AVENUE', '266 E 49TH AVENUE', '2733 E 28TH AVENUE', '3025 E 45TH AVENUE', '3135-3137 HEATHER STREET', '3151 HEATHER STREET', '4011 KAMLOOPS STREET', '4265 SLOCAN STREET', '522 E CORDOVA STREET', '5771 SHERBROOKE STREET', '912 E 57TH AVENUE']。
修正:residential zoning 门(R*/F*),不通过者状态
`ASSUMPTIVE_ZONING` + flag `ZONING_UNVERIFIED_ASSUMED_RESI`;
假设测算保留,但不再显示已核实合法,ROI 行带 zoning_caveat。
V3s 生产代码零修改。

**P2 MULTIPLEX 售价**:228 条 MULTIPLEX_UNIT 代理成交单位价有、
floor area 全缺(fa=''),原被整体过滤导致 105 条 MPX 全 BLOCKED。
修正:MPX 池保留 unit_price;$/sf 匹配失败时回退
`500M/COMM/SIDE_UNITPX` 单位价×户数,confidence 一律
`LOW_CONFIDENCE(PROXY_UNIT_PRICE,FA_MISSING)`,与 $/sf 证据分列。
现 105/105 条 MPX 有代理估值,无依据的售价仍不制造。

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
