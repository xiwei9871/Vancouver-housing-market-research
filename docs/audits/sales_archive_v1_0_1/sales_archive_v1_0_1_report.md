# Sales Archive V1.0.1 — 数据源补齐 + 空间身份去重验收报告

基线:commit `66e5030`(Sales Archive V1)。本轮按任务书只做
源补齐、身份关联、跨源去重;**不改 V3s 估值/ROI/排名**。

## 一、执行摘要

| 指标 | V1 | V1.0.1 |
|---|---|---|
| 原始行 | 15,950 | 18,409(并入 2023-24 全类型 + 2023-26 enriched) |
| 交易实体/canonical | 14,901 | **14,192** |
| ArcGIS↔comps 跨源合并 | **3** | **4,178** |
| 源内重复标记(待核销) | 3,246 | 已核销:合并 4,217 条(raw→canon) |
| AGE_UNKNOWN | 9,868 | **662** |
| pid 已知 | ~5,700 | 13,787 |
| yb 已知 | 5,033 | 13,388 |

## 二、数据源补齐(P0-A)

新增/启用本地已存资产:
- `bca-arcgis-sales-2023-01-01-2026-09-06.json`(+enriched-high
  同键回连 pid/yb/法定描述)
- `bca-arcgis-sales-2025-present-enriched.json`、`…-enriched-spatial`
  (pid/楼层/面宽/进深)
- **新采集**:`bca-arcgis-sales-2023-01-01-2024-12-31-all.json`
  3,113 行(BCA 公共层 guest token,同历史口径合法采集)
- `property-tax-{2023..2026}-full.csv` 全年度税务
  216,524 / 224,743 / 226,360 / 228,935 行(CoV 开放数据)

### 年度×产品 canonical 覆盖

| 年 | HOUSE | CONDO | TOWNHOUSE | DUPLEX_HALF | DUPLEX_WHOLE | MPX_UNIT | MPX_WHOLE | OTHER | 合计 |
|---|---|---|---|---|---|---|---|---|---|
| 2023 | 2,534 | 0 | 3 | 0 | 0 | 0 | 0 | 94 | 2,631 |
| 2024 | 2,465 | 0 | 3 | 0 | 0 | 0 | 0 | 68 | 2,536 |
| 2025 | 1,700 | 3,744 | 405 | 474 | 9 | 180 | 24 | 147 | 6,683 |
| 2026 | 774 | 1,203 | 118 | 133 | 6 | 48 | 11 | 49 | 2,342 |

### 仍然未覆盖(SOURCE_NOT_COVERED,不虚报)

1. **2023/2024 CONDO/TOWNHOUSE/DUPLEX/MPX 近零**:BCA ArcGIS
   层仅存每物业**最近一次成交**(LAST_SALE);早期非独立屋的
   成交多已被后期转售覆盖。全类型重拉仅 +149 条新记录(印证
   早期 last-sale 主要是未再售物业)。回补路径:MLS/GVR 历史
   库或 BCA 批量数据授权。
2. **2026-07-09 以后**:BCA 层刷新截至 2026-07-08;
   已实测 07-09→10-23 查询返回 0 行(源端滞后)。
3. 税务记录是属性快照,**不当作成交**;缺成交源的年份/产品
   只标缺口,不填零。

## 三、空间身份关联(P0-B)

- `address_norm.py`:组件级解析,保留 unit/number/name/type/
  direction/qualifier/原始地址。处理 AVENUE↔AVE、方向前后置
  ("E 19TH AVENUE" ≡ "19TH AVE E")、街名内方向词
  (KENT AVENUE SOUTH E)、序数 ST(1ST 是街名非 STREET)、
  `#204`/`204-`/`UNIT 204` 单元写法。
- `spatial_identity.py`:七级分层 + 地块尺寸自适应半径
  (25m 基底→0.8×√lot_m² 上限 80m),坐标只发现候选。
- 判定结果:EXACT_PID_UNIT 4,454 对、EXACT_PARCEL 5,572 对、
  SPATIAL_CANDIDATE_ONLY 51,510 对(不自动合并)、
  IDENTITY_CONFLICT 0。
- 全库 11,508 个身份簇;SPATIAL_CANDIDATE_ONLY 只进审计表。

## 四、跨源去重(P0-C)

Raw → Identity → Unit → Transaction → Canonical:
- 18,409 raw → 11,508 身份簇 → **14,192 canonical sales**,
  合并 4,217 条(其中跨源合并 4,178,源内重复 12,其余为
  同单元多行)。
- `raw_to_canonical_mapping.csv` 全量可逆映射;
  `merge_evidence.csv`(本地)逐条记录 date/price/ident_tier。
- 2025 年疑似重复核销:raw 9,137 → canon 6,683(净并 2,454)。
- 规则:同一单元身份 + 合同/登记日 ≤60d + 价差 ≤2% 才并;
  同址同价不并(需身份证据);写法差异不拆(地址归一化)。

## 五、税务属性接入(P1)与房龄(P2)

- 全年度 pid→folio→plan→land_coordinate→civic 区间索引;
  无 pid 记录按"规范化街名+类型+方向 + civic 区间 + 最紧跨度 +
  legal_type"回连:SINGLE 8,051,AMBIGUOUS 75(禁止自动确认)。
- yb 选取取 ≤ 成交年的最近税务快照;yb>成交年 →
  AGE_TEMPORAL_CONFLICT(2 条)。
- 六档房龄矩阵 + 价格中位数:
  `transactions_by_year_product_age.csv`。

## 六、验收门槛回答

| 问 | 答 |
|---|---|
| 23-26 各类型补回多少 | 2023: 1,478→**2,631**;2024: 1,490→**2,536**;2025 raw 9,137→canon 6,683;2026 →2,342(截 07-08) |
| 空间匹配恢复多少跨源关联 | 10,026 对确认级(EXACT_*);canonical 级 4,178 笔多源核实 |
| 哪些仅候选 | 51,510 对 SPATIAL_CANDIDATE_ONLY,全部不合并、留审计 |
| 3,231 疑似重复如何核销 | 4,217 条合并全部带可逆映射+原因;2025 重复登记净并 2,454 |
| 多源验证率 | canonical 多源占比 3/14,901(**0.02%**)→ 4,178/14,192(**29.4%**) |
| AGE_UNKNOWN | 9,868 → **662** |
| 覆盖缺口 | 仍在:23-24 非独立屋、2026-07-09+;已标 SOURCE_NOT_COVERED |

## 七、复现

```bash
.venv/bin/python scripts/sales_archive_v1_0_1.py
.venv/bin/python scripts/test_sales_archive_v1_0_1.py   # 28 PASS 合成回归
```

输入指纹见 `data/research/normalized/sales_archive_v1_0_1/manifest.json`
(含新采集 2023-24 全类型文件 SHA256)。行级 `sales_archive.csv`、
`merge_evidence.csv` 为受限数据,仅留本地。
