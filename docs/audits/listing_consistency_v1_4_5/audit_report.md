# V1.4.5 一致性审计报告 — 2026-10-23

规则集 `V1.4.5-C01..C11-r1`;对照基线 V1.4.4(SHA `aa45207`)。Phase A 只读,
不改估值输入/输出。

## 必答题

- **224 套中存在跨指标矛盾的房源(按地址去重)**:**115**
- **规则触发分布**:
  - C01: UNRESOLVED 7
  - C02: UNRESOLVED 11
  - C03: UNRESOLVED 4
  - C04: UNRESOLVED 3 / EXPLAINED_DIFFERENCE 1
  - C05: UNRESOLVED 1
  - C06: EXPLAINED_DIFFERENCE 44 / UNRESOLVED 77
  - C07: UNRESOLVED 34
  - C09: LEGAL_REVIEW_REQUIRED 17
- **ROI 计数(修复前后一致,本轮无估值改动)**:≥10% **74**
  / ≥20% **15** / ≥30% **2**
- **排名前 20 中估价或法规需改变的房源**:**2** —
  ['614 E 19TH AVENUE', '48 E 22ND AVENUE']
- **法规未验证/需专项核查的方案数**:**215**(LEGAL_UNVERIFIED+
  LEGAL_REVIEW_REQUIRED+CONDITIONAL_REVIEW)
- **C08 留出回测(cutoff 2025-07-01,train 119/test 39)**:
  - A_500M_FIRST: n=39 MdAPE=10.7% MAE=$178.0/sf P90APE=66.3%
  - B_COMM_NEARBY_ADJ: n=39 MdAPE=10.7% MAE=$178.0/sf P90APE=66.3%
  - C_COMM_WEIGHTED: n=39 MdAPE=10.7% MAE=$179.0/sf P90APE=66.3%
  - 判读:时间留出后 500m 环在任何测试点都凑不齐 ≥3 个训练期
    comps,A 与 B 逐笔完全一致;C 与 A 在 17/39 行有差异但统计量
    相同。**回测无法区分 500M 优先与社区基准——不支持切换算法**。
    但 C06 横截面证据(75 N SLOCAN:n=3 薄样本 → $690/sf vs 社区池
    $1,220/sf,差 45%)说明 n=3 的 500m 环摆动大,依赖披露而非
    改法。
- **C06/C07 集中度**:121 对 C06 中少数异常房源驱动大多数配对
  ——75 N SLOCAN STREET(32对), 5794 LANARK STREET(11对), 5755 DUMFRIES STREET(10对), 1380 E 54TH AVENUE(9对), 2175 WAVERLEY AVENUE(7对)。成对数量高 ≠ 每对独立问题。
- **P0-1(排序/筛选)**:V1.4.4 `aa45207` 已修(data-k 固定键+浏览器
  回归通过);本审计基线已含该修复。
- **P0-2(社区统计用冻结池)**:本分支已修——渲染器与逐房源估值
  复用同一 dims 土地池;Hastings-Sunrise lot P50 4,191 /
  Marpole 5,839 / Renfrew(北) 4,224 / Kensington-CC 4,048,与
  冻结池独立对账一致。
- **土地面积来源冲突**:冻结 dims 输入由渲染器/审计/估值三处
  共用;`input_manifest_v1_4.csv` 已含 SHA256。

## 分类汇总

| 分类 | 含义 | 数 |
|---|---|---|
| CONFIRMED_BUG | 有证据的程序/数据错误 | 0 |
| EXPLAINED_DIFFERENCE | 差异大但有解释 | 45 |
| UNRESOLVED | 证据不足 | 137 |
| LEGAL_REVIEW_REQUIRED | 需法规专项核实 | 17 |

## 交付文件

`consistency_findings.csv`(199 条)/ `listing_consistency_summary.csv`(224)/
`comparable_pair_anomalies.csv`(125)/ `valuation_bridge.csv`(35)/
`zoning_rule_matrix.csv`(27)/ `zoning_conflicts.csv`(672)/
`pricing_backtest.csv`(39)/ `audit_report.md` / `manifest.json`

复现:`python3 scripts/listing_consistency_audit_v1_4_5.py`
