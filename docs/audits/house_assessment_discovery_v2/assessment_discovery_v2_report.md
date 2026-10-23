# Assessment Jump Discovery V2 报告

基线:816f6ae;档案/普查冻结,本轮=评估价值研究层。
筛查阈值非确认规则;无 SOLD 证据不计成交。
2026 cutoff=2026-07-08;2023 前无更早快照 →
NO_BASELINE_ASSESSMENT 单列。

## Schema 审计(prev↔prior-current 跨年口径)
{'match': 655495, 'diff': 378, 'na': 215532}
一致率 ≥99% 的年份对可直接跨年比较;差异行计入注记。

## 全市突变规模
- jump 候选地块:9164
  (STRONG/MODERATE 或近零基线→显著非零)
- 反向找到档案内成交(唯一 canonical_tx):3197
- 有建筑变化但无 SOLD 证据:6593(不计入销量)
- PID 变更血统边(predecessor→successor):106

## 四阶段证据门(全 5,386 挂载)
{'ALL': 5386, 'SALE_EVENT_VERIFIED': 5386, 'CONSTRUCTION_DETECTED': 734, 'NEW_BUILDING_VERIFIED': 174, 'NEW_HOUSE_SALE_VERIFIED': 111}
NEW_HOUSE_SALE_VERIFIED=111
严格可比口径(普查已证 ≤ 成交年的新房龄证据)。

## P3 瀑布回答(实测)
| 问 | 数 |
|---|---|
| 原 128 条中估值明显跳涨(STRONG+MODERATE) | 84 |
| 128 条召回率 | 84/128 = 66% |
| 52 REVIEW 获新增建设证据 | 20 |
| 259 UNKNOWN 获建设证据 | 0 |
| 旧房组(31_50/51+)疑似重建成交 | 336 |
| 全市突变反查命中档案内成交(唯一) | 3197 |
| ├ 其中已证 0-3 新房成交(事件行) | 119 |
| ├ 其中非 HOUSE 产品(误分排查,事件行) | 3046 |
| └ 其中疑似新房成交(建成先于成交,事件行) | 504 |
| 有建筑变化但无 SOLD 证据 | 6593 |
| 新增 VERIFIED 0-3 HOUSE 成交 | 0 |
| 真新房首销(FIRST_SALE ∩ CONFIRMED_00_03) | 44 |
| └ 0-3 中非首销(转售/未知) | 84 |
| 负例:土地/旧房成交后买家重建(STRONG) | 23 |

## 分年 0-3 成交
- CONFIRMED_AGE_00_03 2023: 41
- CONFIRMED_AGE_00_03 2024: 48
- CONFIRMED_AGE_00_03 2025: 26
- CONFIRMED_AGE_00_03 2026: 13
- PROBABLE_AGE_00_03 2026: 1

## 阈值敏感性
见 assessment_jump_threshold_sensitivity.csv
(50%/75%/100%×C_ref;128 条中多数未达 STRONG 阈,
按 P2 要求保留 MODERATE/LOW 候选层不牺牲召回)。

## 血统(P0-C)
106 条 predecessor→successor 边
(folio/land_coordinate/plan+lot 同键、时间互补)。
见 parcel_pid_rebuild_lineage_audit.csv(匿名哈希)。

## 盲区(诚实声明)
- 2023 年无 pre-2023 基线:只能看 2024-26 三年窗口;
- 2026 成交2026-07-08截断,且新建成价值可能
  需 2027+ 快照才显化 → 当年 jump 召回偏低;
- 评估滞后:突变年度≠竣工年度,jump_year 是卷面
  追认时间不是开工/完工时间;
- 无 pid 行(地址级)无法纳入血统;
- duplex/multiplex 单元跳变会命中多 pid,
  已按 canonical_tx 去重但单元口径需人工复核;
- 反查只覆盖已保存成交池(BCA/MLS/官方),
  未保存来源的成交不在反查域内。
