# 授权来源采购方案 — authorized_source_acquisition_plan.md

日期 2026-10-23。**未授权源一律 BLOCKED,不购买不绕过。**
需用户批准的事项在文末汇总。

## 方案 A:GVR/MLS SOLD 导出(首选)

**能解**:MPX 单元成交、HOUSE 2018+ 首销、DUPLEX 半户口径、
2026-07-08 后新增成交 — 一次覆盖全部主缺口。

| 路径 | 说明 | 状态 |
|---|---|---|
| A1 持牌经纪合作 | 与温哥华持牌经纪签数据共享协议,由其 MLS 系统导出 sold comps | 需用户指定/批准 |
| A2 REBGV/GVR 数据产品 | 直接询价 GVR 统计/数据授权(非会员可能有受限包) | 待询价 |
| A3 第三方合法聚合 | Zoocasa/HouseSigma 等展示 sold 但无批量授权 | 不采(条款不符) |

**动作**:按 `sales_data_requirements.md` 字段清单+许可 8 问发询价。
先用单社区 2023+ 小样本(如 Killarney 全部 sold)验证字段覆盖率
再谈全量。**预估 $200–1500/年(经纪关系)或机构协议价。**
用户待批:是否已有可合作的持牌经纪渠道。

## 方案 B:BC Assessment Data Advice(批量字段)

**能解**:829 条 yb_no_source、MPX 单元 fa、folio/strata 结构。

- BCA 面向商业客户提供 folio 级批量数据(Data Advice/Custom Data)
- 动作:向 assessment 询价 829-folio 最小字段包(见需求文档)
- 预估:按 folio 计费,单批数百加元量级
- 用户待批:询价授权(不产生购买)

## 方案 C:LTSA 抽样核验(按条计费)

**能解**:MPX 分层产权真相、争议锚点、DUPLEX 单元身份 — 不用作批量源。

| 对象 | 数量 | 目的 |
|---|---|---|
| 74 个有成交 MPX 项目 | ≤10 抽样 | 验证 strata/parcel 链 |
| 2609 DUNDAS | 1 | 许可后成交定性 |
| 高价锚点/争议方案 | ≤10 | title 级背书 |
| DUPLEX 配对 | ≤10 | 半户法定身份 |

每案 title/plan 检索 ~$10–40 官费 + 人工。**总预算 < $1,000。**
动作:只在特定 P0 结论依赖产权证据时执行,用户逐案批准。

## 方案 D:公开源持续试采(零成本,持续)

- CoV opendata:已穷尽(无成交/occupancy 集)——定期复查新集
- 开发商营销页:341 个 MPX 项目逐个查"已售/成交价"披露——
  人力密集,建议只对进入候选尽调的 ~20 个项目做
- REALTOR api2:仅在售挂牌(已在用);**要价≠成交价,不混入成交池**

## 集成验收标准(任一源接入时)

1. 新记录经 `house_duplex_evidence_tiers` 同级管线重跑
2. SOURCE_VERIFIED 要求与现有 arcgis 或双源交叉一致
3. MPX 单元必须 strata_plan/pid 链接到 permit 项目才计入 MPX 池
4. 增量导入测试:`sales_source_trial_results.csv` 记覆盖率/字段完整性

## 需用户批准清单

| # | 事项 | 阻塞解除 |
|---|---|---|
| 1 | GVR/MLS 渠道(经纪合作或机构协议) | MPX 单元+首销 |
| 2 | BCA Data Advice 询价 | 829 yb |
| 3 | LTSA 按案检索授权 | 争议方案 title |
| 4 | 许可用途书面确认(8 问) | 合规边界 |
