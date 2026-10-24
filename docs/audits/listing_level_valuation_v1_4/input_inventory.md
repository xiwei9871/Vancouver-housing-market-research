# V1.4 Input Inventory(P0 盘点)

| 输入 | 位置 | 状态 |
|---|---|---|
| ACTIVE 房源(Ask/lot/frontage/depth/zoning/lat-lng/bca/bca_land) | `reports/watchlist/rebuild-value-2026-10-20.json` | 224 套 ACTIVE;bca_land 缺 52、frontage 缺 5、zoning 缺 6(降级 LOW_CONFIDENCE/BLOCKED) |
| V3s 逐房源方案(HOUSE/DUPLEX/MPX build_sqft、net_saleable、units、verified) | 同上 `analysis.scenarios`(2026-06 bylaw 核验版,含 PTT) | HOUSE verified 198 / DUPLEX 167 / MPX 105;不重新实现法规 |
| 历史成交(HOUSE 归一化价、band、fa、lot、community) | `normalized/house_hpi_normalization_v1_2/house_price_normalized_per_row.csv` | 5,386;band=resolved_yb 口径 |
| DUPLEX/MPX 代理归一化价 | `attached_proxy_scenarios_per_row.csv` | 2,184 行 PROXY 标 |
| pid/lat/lng/strata 回填 | `normalized/sales_archive_v1_0_3/sales_archive.csv` | canonical_tx join |
| BCA land/improvement(评估年份) | `raw/official/property-tax-2023..2026-full.csv` | pid+roll-year join;缺失=不可验证,不进主池 |
| resolved_yb(精确 age_at_sale) | `normalized/housing_age_recovery_v2_4/age_recovery_per_row.csv` | canonical_tx join |
| 成本模型 | V3s `rebuild_value_analysis.py` 现行口径(350/400/450+40permit+PTT+fin+selling+GST) | 常量复制+测试对账,不二次建模 |
| 缺:逐房源房龄/现有建筑状况 | 以 bca 改善占比代理 utility flag | 记录在案,不虚构 |
