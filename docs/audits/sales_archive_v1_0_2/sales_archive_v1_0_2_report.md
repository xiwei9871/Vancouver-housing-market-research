# Sales Archive V1.0.2 — Identity & Product Integrity Gate

运行时间:2026-10-23T14:16:41
输入指纹:见 manifest.json(全部 SHA256)

## 瀑布
raw 30798 → parcel 簇 10734 → unit 簇 24147 →
canonical 24203;确认合并 6595;疑似候选重复(人工)
2198;被拦截 union 129;簇冲突
0;schema 缺陷 0。

## 空间匹配分层
- SPATIAL_CANDIDATE_ONLY: 184452
- SPATIAL_ADDRESS_CORROBORATED: 131880
- EXACT_PID_UNIT: 4330
- EXACT_PARCEL: 2074
- ADDRESS_ONLY: 151

## 年度成交
- 2023: 7456
- 2024: 8066
- 2025: 6483
- 2026: 2198
(2026 截至 2026-07-08,非全年)

## 产品分类(VERIFIED/CANDIDATE 分离)
- CONDO: 14068
- HOUSE: 5386
- TOWNHOUSE: 1823
- DUPLEX_WHOLE: 1349
- DUPLEX_HALF: 607
- OTHER_RESIDENTIAL: 391
- UNKNOWN: 316
- MULTIPLEX_UNIT: 228
- MULTIPLEX_WHOLE: 35
- VERIFIED: 23667
- UNKNOWN: 316
- CANDIDATE: 220
MPX 复核见 multiplex_classification_reconciliation.csv
(候选 220 笔,
验证 43 笔)。

## 房龄证据分级
- AGE_ADDRESS_INFERRED: 16494
- AGE_EXACT_PID: 4811
- AGE_UNKNOWN: 2698
- AGE_CONSENSUS_ONLY: 200
严格覆盖率:4811
/ 24203;含推断:21505 / 24203。

## 数据源修复(P1 逐源可行性)
- 旧 `arc_2324_all` 由 `build_bca_sales_where` 硬编码
  `IS_STRATA='N' AND DESCRIPTION LIKE '%house%'` → 2023/24 名义
  全类型实为 house-only,这是上一轮"2023/24 无 CONDO"的根因。
- 探测:同一合法 guest-token 通道改开放 where(仅价格带+日期),
  2023-24 实拉 15,535 条(2023:7,468 / 2024:8,067),
  含 strata 公寓 9,000+、联排 1,315、duplex ~1,100 —— 源端
  数据一直在,缺的是过滤条件。已存
  `bca-arcgis-sales-2023-01-01-2024-12-31-alltypes.json`。
- 结构缺口仍存在:BCA 层仅存每物业 LAST_SALE,窗口内二次转售
  的早前记录被覆盖;comps_v2 的 2023/24 无 CONDO/TH/DPX
  (MLS 口径侧重非 strata)→ 该两年全部产品标
  PARTIAL_COVERAGE:last_sale_window_truncated。
- IS_STRATA='Y' 服务端过滤返回 0 行(字段值域不符),故用
  开放 where + 本地描述分类;`Strata Office` 等非住宅归入
  OTHER_RESIDENTIAL 待剔除评估。
- 2026 截 2026-07-08;2025 亦受 LAST_SALE 截断影响
  (2026 在售物业的 2025 记录被覆盖)。

## 跨源归并核销
确认合并 6595(可追溯 merge_evidence);同 parcel
异单元事件命中 2198 笔列为人工核对,未自动合并;
多源核实销售 6544 笔
(V1.0.1 基线:ArcGIS↔comps 3 笔 / 多源 4,178 笔)。

## 覆盖诚实性
2023/2024 的 CONDO/TOWNHOUSE/DUPLEX/MULTIPLEX 标
SOURCE_NOT_COVERED 或 PARTIAL_COVERAGE(见
source_coverage_2023_2026.csv),不为市场零。
2026 全产品均带 cutoff=2026-07-08。
