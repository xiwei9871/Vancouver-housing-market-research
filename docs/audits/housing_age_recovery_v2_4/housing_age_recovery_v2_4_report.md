# Housing Age Recovery V2.4 — 跨产品房龄恢复报告

基线 a52f911;研究层恢复,不修改冻结档案。
成交截点 2026-07-08(2026 为部分年)。

## 根因结论(实测,非推测)
1. **市开放数据税卷 `year_built`/`big_improvement_year`
   字段冻结在 ≤2022**——2023/2024/2025/2026 四年全表
   yb 上限同为 2022(分别 17/18/18/18 行)。任何
   2023+ 竣工建筑在税卷中天然无房龄。
2. `resolve_age()` 三层(精确 pid → native → tax_link)
   全部依赖税卷 yb → **所有产品** archive yb 对
   2023+ 成交封顶 2021。
3. 房龄纠正层(nb217/enriched/permit + census)只覆盖
   HOUSE → 非 HOUSE 2025-26 的 0-3 档全零。
4. strata plan/pid 首现年可恢复推断:roll 中 plan
   首次出现年 T → 单元约 T-1 建成可评估
   (fs=2023 是卷面下界,单列降级)。

## 修复前后 0-3 成交(全口径含推断)
| 产品 | 年 | 修复前 | STRICT | SUPPORTED | INFERRED | 修复后 |
|---|---|---|---|---|---|---|
| HOUSE | 2023 | 20 | 41 | 0 | 0 | 41 |
| HOUSE | 2024 | 9 | 48 | 28 | 0 | 76 |
| HOUSE | 2025 | 0 | 26 | 19 | 0 | 45 |
| HOUSE | 2026 | 0 | 13 | 9 | 0 | 22 |
| DUPLEX_HALF | 2025 | 0 | 0 | 285 | 0 | 285 |
| DUPLEX_HALF | 2026 | 0 | 0 | 68 | 0 | 68 |
| DUPLEX_WHOLE | 2023 | 31 | 31 | 0 | 0 | 31 |
| DUPLEX_WHOLE | 2024 | 14 | 14 | 67 | 59 | 140 |
| DUPLEX_WHOLE | 2025 | 0 | 0 | 2 | 0 | 2 |
| DUPLEX_WHOLE | 2026 | 0 | 0 | 4 | 0 | 4 |
| TOWNHOUSE | 2023 | 15 | 15 | 0 | 0 | 15 |
| TOWNHOUSE | 2024 | 7 | 7 | 150 | 0 | 157 |
| TOWNHOUSE | 2025 | 0 | 0 | 128 | 0 | 128 |
| TOWNHOUSE | 2026 | 0 | 0 | 45 | 0 | 45 |
| MULTIPLEX_UNIT | 2025 | 0 | 0 | 46 | 0 | 46 |
| MULTIPLEX_UNIT | 2026 | 0 | 0 | 5 | 0 | 5 |
| MULTIPLEX_WHOLE | 2025 | 0 | 0 | 0 | 2 | 2 |
| MULTIPLEX_WHOLE | 2026 | 0 | 0 | 0 | 3 | 3 |
| CONDO | 2023 | 164 | 164 | 0 | 0 | 164 |
| CONDO | 2024 | 104 | 104 | 428 | 0 | 532 |
| CONDO | 2025 | 0 | 0 | 231 | 0 | 231 |
| CONDO | 2026 | 0 | 0 | 62 | 0 | 62 |

## 关键回答
- **2025 CONDO 0-3=0**:非市场无成交,而是
  ①税卷 yb 冻结(见根因 1);②archive yb 来源封顶
  2021;③CONDO 不在 census/enriched 覆盖内。plan
  首现年恢复出 SUPPORTED/INFERRED 样本(见上表)。
- **DUPLEX_HALF 2025 474 笔无 0-3**:同理——半户为
  新 strata 单元,roll yb 空;其 plan 首现年大量为
  2024-25 → 恢复后 0-3 量级显著,但为
  SUPPORTED_AGE/INFERRED_AGE 证据层,非 STRICT。
- **HOUSE 2025 0-1=7 是否仍成立**:STRICT 层不变
  (census 已是房龄上限);0-3 档有 SUPPORTED 增量
  但 0-1 档 2025 无恢复增量——见
  house_0_1_age_reconciliation.csv;
  SUPPORTED/INFERRED 不得并入已确认口径。
- **真缺失 vs 年份错**:roll yb 存在且与 plan_fs 一致
  =真旧房;roll yb 存在但 plan_fs/jump 显著更晚 =
  BUILDING_IDENTITY_CONFLICT(旧年倒灌,重建后
  仍挂拆除前年);roll 无 yb 且 plan_fs 新 =
  年份缺失可恢复;无 pid/无 roll/无 plan =
  证据真空。
- **FIRST_SALE ≠ 新房**:sale_kind 与房龄分列;
  UNKNOWN kind 不自动归旧房。

## 口径边界
- 推断年 yb≈fs-1(roll 首现年-1)有 ±1 年不确定;
  band 边界行(age 恰为 1/3/5/10)在报告中注明。
- permit 签发 ≠ 竣工,permit+1 仅作 INFERRED 弱源,
  多弱源一致才升 SUPPORTED。
- TEMPORAL_CONFLICT:推断竣工晚于成交年 →
  该笔绝不被计入新房(pre-sale/旧房被正确保留)。
- V3s 冻结档案与销售 archive 零修改;恢复层仅在
  研究产物中。
