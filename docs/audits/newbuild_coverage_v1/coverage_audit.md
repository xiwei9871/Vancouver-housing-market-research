# New-Build Evidence Recovery V1 — 覆盖率审计

日期: 2026-10-23 · 快照 `audit_v1` · 输入指纹见 `manifest.json`
管线: `scripts/newbuild_evidence_v1.py` · 只读,不改 V3s

## 0. 一句话结论

**"新建住宅成交只有 0–3 条"主要是数据捕获与字段缺失问题,不是市场供给问题。**
官方低密度住宅许可 2017+ 共 **9,944 项**,其中 HOUSE 3,586 / DUPLEX 1,847 /
MPX 341 / Laneway 4,154;而我们的成交库同口径可用新建样本:HOUSE 215 /
DUPLEX 389 / MPX 20。缺口主体在成交侧(尤其属性字段),不在建设侧。

## 1. 官方供给名册(City of Vancouver issued-building-permits)

来源: `opendata.vancouver.ca / issued-building-permits`(官方 API,2017+)
文件: `data/research/raw/official/issued-permits-ldh-2017.csv` (9,944 行)

产品分类规则(可审计,`specificusecategory` 优先 + 描述兜底):

| 产品 | 许可项目数 | 口径 |
|---|---|---|
| HOUSE | 3,586 | Single Detached(+w/Sec Suite)+ Infill + 1FD w/Family Suite |
| DUPLEX | 1,847 | Duplex + w/Secondary Suite + Lock-off 变体 |
| MPX | 341 | Multiple Dwelling(描述解析 N-unit front/rear) |
| LANEWAY | 4,154 | Laneway House(辅助证据,不入主池) |
| OTHER | 16 | 无法分类,未强归 |

- 状态口径: 全部为 **ISSUED**(官方签发≠完工;完工需 folio/成交交叉证)。
  2026 年签发未完工项只进供给管线,不进成交样本。
- 单元数: HOUSE 默认 1(+laneway 附属标),DUPLEX 默认 2,
  MPX 由描述 `N-unit front/rear building` 解析;**主户与附属 suite 分列 tags**。

## 2. 单元册(property-tax-report)

来源: `opendata.vancouver.ca / property-tax-report`
快照: report_year=2026(yb≥2018 共 13,963 folio)+ 2023 对照(13,944)

| 分区倾向 | folio 数 | 说明 |
|---|---|---|
| CD(综合开发,多为高层公寓) | 10,010 | 不进低密度池 |
| HOUSE 区(RS/R1-1 等) | 2,079 | 新建独栋单元册 |
| DUPLEX_OR_MPX 区(RT/RM 等) | 1,370 | 双拼/多户单元册 |
| OTHER | 504 | — |

- `legal_type=STRATA` 为单元级 folio;LAND 为整块。
- 2023→2026 同 folio 比对用于识别新分层/产权拆分(first_seen 标签)。
- 注意: strata PID 与原始地块 PID 不同;BIG_IMPROVEMENT_YEAR ≠ YEAR_BUILT(未用)。

## 3. 成交证据普查(全源,不只 comps-v2)

`source_inventory.csv` 全量;关键发现:

- 主表 `standard-residential-comps-v2.csv`: 6,576 唯一成交键(addr+date)
- `bca-arcgis-sales-2025-present-all.json`: 8,317 键,其中 **5,613 不在主表** —
  逐条检查为高层/商业/仓储 strata,**非低密度产品**,排除正确
- `over-6m` 探针文件: 146 条未入主表(>$6M 豪宅,多数超地块过滤窗)
- 其余 enriched/candidates 文件与主表完全重合(0 条遗漏)

## 4. 筛选流水账(coverage_waterfall.csv,全社区×产品)

城市合计:

| 产品 | 成交记录 | asof_ok | yb2018+ | 有建面 | 有价格 | **可用新建样本** |
|---|---|---|---|---|---|---|
| HOUSE | 4,999 | 4,952 | 215 | 2,598 | 4,952 | **215**(恢复+16→231) |
| DUPLEX | 622 | 610 | 404 | 573 | 610 | **389** |
| MPX/未知* | 959 | 949 | 387 | **25** | 949 | **20** |

*MPX 行含 STRATA_UNIT_UNKNOWN 635 条,不能证明为目标产品,不强行归类。

### 三产品各自的瓶颈(三种不同病)

- **HOUSE = 属性缺失主导**: 2,351 条成交**缺 year_built** —— 其中 2,348 条
  全部来自 `bca-arcgis` 源(`gst_basis=unknown_no_yearbuilt`)。成交本身真实,
  但无法判定新旧 → `ATTRIBUTE_MISSING`。经 tax-folio 地址回连恢复 **16 条**
  确认 yb2018+(见 `yb_recovered_records.csv`,含 1076 PARK DR 等)。
- **DUPLEX = 捕获率中**: 389 条可用;但官方 DUPLEX 许可 1,847 项(≈3,700 户),
  覆盖率约 ~10%。剩余为 `SOURCE_MISSING`(MLS/GVR 渠道未批量接入)。
- **MPX = 字段缺失主导**: 959 条记录中 934 条**缺 floor_above_sf** →
  无法算 $/sf → `ATTRIBUTE_MISSING`,非"没有成交"。

### 重点社区 HOUSE 可用新建样本(asof_ok → yb2018+)

| 社区 | 许可项目 | 成交记录 | yb2018+ | 可用 |
|---|---|---|---|---|
| Hastings-Sunrise | (见 waterfall permits 列) | 479 | 27 | 27 |
| K-CC | — | 556 | 21 | 21 |
| Riley Park | — | 301 | 21 | 21 |
| Grandview-Woodland | — | 225 | 13 | 13 |
| Sunset | — | 293 | 10 | 10 |
| Victoria-Fraserview | — | 302 | 10 | 10 |
| Marpole | — | 196 | 8 | 8 |
| Killarney | — | 170 | 5 | 5 |
| Kitsilano | — | 234 | 5 | 5 |

社区层分类: `ATTRIBUTE_MISSING`(arcgis 无 yb)+ `SOURCE_MISSING`
(成交未捕获)并存;`FILTER_EXCLUDED` 占比小(lot 窗/带外仅边缘影响);
**无社区可判 `GENUINELY_THIN`** —— 许可证明供给存在。

## 5. 遗留缺口与获取方案 → `data_gaps_and_acquisition.md`

## 6. 验收对应

- 项目数/单元数/成交数三层分列 ✓(project_registry / unit_registry /
  sales 证据=comp 记录层)
- 不将许可数当已售分母 ✓;未知出售不记未售 ✓
- 未强制最低样本数;0–3 社区逐层归因已给 ✓
