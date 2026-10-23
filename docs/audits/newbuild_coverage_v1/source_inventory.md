# 数据源普查 — Source Inventory V1

生成: `scripts/newbuild_evidence_v1.py` · `data/research/normalized/audit_v1/source_inventory.csv`

## 分类汇总

### A. 官方开放数据(本轮新增,已落地)

| source_id | 内容 | 行数 | 范围 | 完整性 | 缺陷 |
|---|---|---|---|---|---|
| cov-permits-ldh-2017 | issued-building-permits LDH+Laneway | 9,944 | 2017+ 签发 | 完整导出 | issued≠completed;完工状态需交叉证 |
| cov-tax-2026-yb2018 | property-tax-report 2026 | 13,963 folio | yb2018+ | 完整 | 仅 2026 快照;strata folio 单元级 |
| cov-tax-2023-yb2018 | property-tax-report 2023 | 13,944 folio | 对照快照 | 完整 | — |

合规性: 官方开放数据 API,无授权限制,已保留原始导出。
存储边界: `data/research/raw/` 与 `data/research/normalized/` 在 .gitignore 内,
原始与规范化产物留本地;仓库仅提交代码+文档。复现: 重跑
`scripts/newbuild_evidence_v1.py` 或从官方 API 按上述参数重拉
(`issued-building-permits`: where=issuedate>='2017' and permittypegroup
含低密度住宅; `property-tax-report`: reportyear + year_built in
("2018"…"2026"),分号分隔 CSV)。

### B. 本地主成交表

| source_id | 行数 | 日期 | 完整性 | 缺陷 |
|---|---|---|---|---|
| standard-residential-comps-v2 | 6,580 | 2021-01~2026-09 | 规范表 | arcgis 源记录缺 yb/fa(2,351 条);GIS lot 字段 ≠ 法定地块 |

### C. BCA/ArcGIS 原始快照(明细见 CSV)

- `bca-arcgis-sales-2025-present-all.json` 8,317 键 — 其中 5,613 不在主表,
  逐条验证为高层公寓/商业 strata,**非目标产品**,排除正确
- `bca-arcgis-sales-*-enriched.json` 各快照与主表完全重合
- `*-over-6m-*.json` 探针 146 条未入主表 — >$6M 豪宅超出 lot 过滤窗,边缘有效
- `bca-sales-*newbuild*.json` 340/459/144 条 — 已在主表

### D. 其他资产

- `v3-1-land-transaction-pool.csv` / `v3-1-pool-presale.csv` — 土地/V3.1 期池
- `laneway-desc-permits-full.json` — 早前许可解析(被本轮官方全量取代)
- `zolo-full-market` 抓取快照 — 网页抓取产物,合规性受限,仅历史参考
- `reports/watchlist/rebuild-value-2026-10-20.json` — 挂牌快照(非成交)
- 区域/学校/地块 enrichment JSON 若干 — 属性增强,非成交源

### E. 未取得(合法路径见 data_gaps_and_acquisition.md)

- MLS/GVR(REBGV)批量成交导出 — 订阅/授权限制,未取得
- LTSA 转让登记批量数据 — 需付费/申请
- BC Assessment 逐 folio 建面明细 — 需要更细粒度授权接口
