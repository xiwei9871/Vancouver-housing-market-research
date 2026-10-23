# Free Source Audit — BCA 采集链与免费 SOLD 平台核查

日期 2026-10-23。**用途:个人购房/重建决策,非商业。**
按用户指令:商业数据采购任务取消,免费证据优先。

## 1. 现有 BCA/ArcGIS 采集链审计

`bca_arcgis_sales.py` 链路:Mapsearch 公开页 → 提取访客 gistoken →
查询 `DCX/AFP_*/MapServer/0`。该层**字段全集**(outFields=* 已全取):
`ADDRESS/STREET_*/UNIT_NUMBER/ROLL/AFP_OID/OID_EVBC/DESCRIPTION/
TOTAL_ASSESSED/TOTAL_LAND/TOTAL_BUILDING/IS_STRATA/LAST_SALE_DATE/
SALE_PRICE/EXTRACT_DATE/SALES_REFRESH_DATE/geometry`

**关键结论:该层无 year_built、无 floor area。**
→ 829 条 yb 缺失不是"匹配失败",是**批量免费层本身不含该字段**。
同服务目录还确认:`DCX/Sales_*` 点层(仅 oid+date)、
`DCX/PublicServices`(医院/警局等,无关)。`ROLL`、`OID_EVBC`、
`UNIT_NUMBER`、`IS_STRATA` 是可用的**身份关联键**(MPX 分层链)。

## 2. 829 真相 = 字段缺口 → 用免费逐户详页回补

`official_enrichment.py` 已有公开门户链路:
`GetByAddress → OID → PropertyInformation` 解析
`year_built / first_floor_area / second_floor_area /
basement_finish_area / pid / sales_history`。

**12 条抽样实测(真实请求)**:

| 结果 | n |
|---|---|
| 地址命中 folio | 12/12 |
| 返回 yb+面积+pid | 9/12(75%) |
| BCA 有 folio 但字段真为空(过渡期) | 3/12 |

已回填样例:2938 DISCOVERY yb1925+1439sf、3079 CROWN yb1942、
4554 4TH W yb1937…**829 中多数是旧房,确认后将核销为
CONFIRMED_OLD 而非"新建缺口"** — 这正是核销需要的。
3 条空值(如 2561 2ND W,lblYearBuilt 标签存在但为空)多为
拆除/重建过渡期 folio,单列 `BCA_BLANK` 诚实标注。

**全量回填**(829 条 × 2 请求,限速 ~1.7s,增量可续跑 —
`scripts/bca_yb_backfill.py`)**已完成,恒等式闭合**:

| 结果 | n | 处理 |
|---|---|---|
| CONFIRMED_OLD(yb<2018) | **786** | 核销为旧房,非新建缺口 |
| 新建有效证据(售≥yb) | **12** | 候选新增 HOUSE 样本(含 4 条带单元前缀,需核半户/strata) |
| TEMPORAL_CONFLICT(售<yb) | 24 | 拆地销售候选,隔离出价格池 |
| BCA_BLANK(folio 在但字段空) | 7 | 过渡期 folio,诚实标注 |
| NO_MATCH / ERR | 0 | — |

786+12+24+7 = **829 闭合**。附带回填 821 条 floor_area 与全部 pid。
**此前"829 无源"结论作废**:不是源端缺失,是批量层不含字段、
而免费逐户详页含全部字段。命中率 99.2%(822/829)。

## 3. 免费 SOLD 平台实测

| 平台 | 结果 | 判定 |
|---|---|---|
| Zolo /sold | **403**(bot 防护) | 个人手动核查可用,管线不可 |
| REW /properties | **403** | 同上 |
| HouseSigma | **302**(app/登录墙) | 同上 |
| Zealty | 301 重定向 | 同生态,人工可查 |
| REALTOR api2 | 挂牌端点,**无 sold 字段** | 已在用,仅要价 |

**结论:免费 SOLD 平台适合做 P0 方案的人工抽样核验,
不构成可自动化批量源** — ToS 均限制自动化提取。
真正合法的免费批量成交源仍是 **BCA arcgis 销售层(已采)**
+ **CoV/BC 官方开放集**(已穷尽)。

## 3.5 MPX 单元在 arcgis 层的存在性实证

对 340 个 MPX 项目地址在已采 arcgis 销售层做基址匹配:

- 命中:**16 条,全部 IS_STRATA=N**(土地/旧房成交)
- strata 单元成交:**0**

→ 即便免费 BCA 层,MPX 单元成交也不存在(与 V1.2 的 comps-v2
口径一致)。剩余免费路径:PropertyInformation 详页的
`sales_history` + 分层 folio;但单元未注册/未成交则真无数据——
这与"渠道缺失"一致,非市场为零。

## 4. 修订后的证据策略(免费优先)

| 缺口 | 免费路径 | 判定 |
|---|---|---|
| 829 yb | BCA 逐户详页(进行中) | ✅ 合法可解 75%± |
| MPX 单元成交 | BCA strata 单元 ROLL/OID + 人工平台核查 | 部分可解 |
| DUPLEX 半户口径 | BCA 详页(UNIT_NUMBER/strata)+ 人工 | 可抽样 |
| 2026-07 后成交 | arcgis 层按月刷新,重跑采集即可 | ✅ 免费 |
| 全市 sold 全量 | 无免费合法批量源 | 人工/授权二选一,不急 |

**采购建议修订**:MLS/GVR 授权从"首选"降为"可选补充"——
若 BCA 逐户+arcgis 层覆盖足够,则不必购买。
