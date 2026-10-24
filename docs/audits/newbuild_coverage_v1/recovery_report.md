# Evidence Recovery V1.1 — 恢复报告

管线: `scripts/newbuild_evidence_v1_1.py` · 产物 `data/research/normalized/audit_v1_1/`
快照截点: **2026-07-08**(成交数据最新日期);窗口 = 2023+(≥2023-01-01) /
recent24(≥2024-07-08) / recent12(≥2025-07-08)。

## 一、P0 修复核销

| 项 | V1 问题 | V1.1 修复 |
|---|---|---|
| 产品分类 | 非空 product_class 全归 MPX(959) | 显式映射;MPX 仅真多户,STRATA/OTHER 单列 |
| 许可↔项目 | permit 号当 project | 9,944 许可 → **9,634 唯一项目**(310 多许可地址),units_est 12,270 |
| 成交窗口 | 2023+ 实际未过滤 | 严格 ≥2023-01-01;recent 按数据快照日回推 |
| RC 社区 | 官方合并区未细分 | lat≥49.243→Renfrew(北),否则 Collingwood(南),无纬度未分 |
| folio 核查 | 未做 | 两年 rows=unique folio 全等;yb>report_year 异常 0 |
| 影子成本档 | indep_roi 统一 350/sf | HOUSE/DUPLEX t350,MPX t400(与展示档一致) |
| 604 核销 | sales_evidence 与瀑布口径不一 | 同一 `usable()` 函数 → HOUSE 250 + DUPLEX 389 = 639 行 |

## 二、恢复成果(三分法)

### HOUSE yb 缺失 2,369 条 → 恢复判定

| 结果 | n | 含义 |
|---|---|---|
| RECOVERED_ENRICHED ≥2018 | 35 | 确认新建,进可用池 |
| RECOVERED_TAX ≥2018 | 15 | 确认新建 |
| PRE2018 已判明 | 1,472 | 确认旧房 → 正确排除(非缺口) |
| AMBIGUOUS | 0 | 无一对多冲突 |
| NO_SOURCE | 829 | 仍无法判新旧(缺 BCA 明细授权) |

**yb 缺失从 2,369 → 832**。恢复率有限是真实的:enriched 只对高价段
查询过 BCA 明细(`bca_detail_status`: CONFIRMED 977 / NOT_SELECTED 4,016)。
**fa 恢复 1,507 条**(enriched first+second floor)→ HOUSE has_fa 2,598→4,105。

### DUPLEX 拆分

- 553 项目 / 622 记录:**68 组 PAIRED_HALVES**(同项目两户分别成交,保留双双)
- 15 整栋成交(WHOLE 口径,不与半户混算)
- 可用半户样本 389(不变——拆分后确认无错删)

### STRATA 分类(最关键修复)

635 条 STRATA_UNIT_UNKNOWN → **516 TOWNHOUSE + 119 APARTMENT**
(arcgis `official_bca_description` 全部命中,零 UNRESOLVED)。

**级联修正**: 230 条 `MULTIPLEX_3_4_CANDIDATE` 中 227 条 BCA 描述实为
Strata Townhouse → 移出 MPX 池;35 条 `MULTIPLEX_WHOLE_BLDG` 中 30 条
实为 walk-up apartment → 出池。**V1 的 MPX"959条/可用20"是分类错误,
真实 MPX 成交池≈0** —— 多户成交在现有成交源中基本未被捕获
(multiplex 本身 2023 年底 RM-8N 才放开,符合预期)。

### 5,613 条排除 arcgis 记录重审计

| 判定 | n | 说明 |
|---|---|---|
| APARTMENT | 5,324 | 高层/框架公寓 |
| OTHER | 273 | 商业/仓储/酒店/车位(修复 "ware**house**"子串误判后) |
| UNRESOLVED | 16 | 混合用途/综合商业,抽样人工待定 |

**0 条低密度住宅被误排除** —— V1 "全为高层/商业"结论经逐条描述证据确认成立
(初版 warehouse 子串误判已修复,误判率 0)。

## 三、证据分级(P2)

- VERIFIED_COMPARABLE: 639 行(pid/一致性 + yb + fa + asof_ok)
- PARTIAL_EVIDENCE: yb 恢复值/缺 fa/缺 pid — 留痕不进可比池
- UNVERIFIED: STRATA_UNKNOWN 未判明、OTHER — 不进池

## 四、区域基准 V1.1

- 164 格(`regional_benchmark_v1_1.csv`):含 n_sales + **n_projects** +
  n_first_sale/n_resale 分列 + borrowed 标签 + 锚点剔除
  (1185 E17 / 2549 KENT / 78 MALTA 不出现在任何基准格)
- HOUSE 41 格 / DUPLEX ~90 格 / MPX 0(诚实)
- TOWNHOUSE 辅助池 743 条可见但不入三主池基准

## 五、影子验证(零回写)

445 行;**417 可估**(V1 为 ~445 但含 MPX 假读数);28 个 MPX 方案
全部 NOT_ESTIMABLE(真实,不再借城市屋假样本)。
27 锚点支配方案:可读数行 borrowed 池**已剔除锚点**,
读数较前轮更干净但仍是 BORROWED 级。

## 六、覆盖率对照

`coverage_v1_vs_v1_1.csv`:HOUSE 全市 215→**250**(+35,
Dunbar+10/Arbutus+4/?区+2 等);DUPLEX 389→389(口径修正后恒定);
MPX 20(V1 误计)→ **0**(诚实)。

## 七、剩余缺口(未解决)

1. HOUSE yb 829 条 NO_SOURCE — 需 BCA 明细授权(逐 folio)
2. TOWNHOUSE/MPX 单元建面近全缺 — 需许可附图/GVR 单元数据
3. MPX 一手成交源缺失(RM-8N 项目许可 341,成交未捕获)
4. 成交捕获渠道:MLS/GVR 授权导出仍未接入
5. arcgis UNRESOLVED 16 条混合用途待人工
