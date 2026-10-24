# 统一矛盾审计 + 双视图排名 — 小范围实施方案(建议稿)

> 本稿为**建议方案**,本轮未执行;实现时走 shadow 文件 +
> 人工批准,零回写 V3s/估值模型。范围刻意限定:不动估值算法、
> 土地价、售价、融资、成本,只统一"审计证据层"与"展示层"。

## A. 统一矛盾审计(命名空间 + 去重关联)

现状:V1.4.5(`listing_consistency_audit_v1_4_5.py`,C01–C11
语义集 A)与 V1.5.1(`listing_valuation_v1_5.py detect()`,
C01–C08 语义集 B)各自触发、编号冲突、部分同义。

方案:单一输出 `consensus_findings.csv`,每条规则加
**命名空间前缀**,不覆盖不相加:

| 新 ID | 来源 | 语义 | 关系 |
|---|---|---|---|
| `A-C01` | V1.4.5 | Ask/BCA 低 vs 模型地价低 | 与 `B-C01` 同义 → `alias_of` 关联去重 |
| `A-C02` | V1.4.5 | fair_land vs BCA_land 越界 | ↔ `B-C02` |
| `A-C03` | V1.4.5 | Ask≥BCA 但折价 | ↔ `B-C03` |
| `A-C04` | V1.4.5 | 同社区相似地估值差 | ↔ `B-C04` |
| `A-C05` | V1.4.5 | 贵地价+高 ROI | B 无对应 |
| `A-C06` | V1.4.5 | 相似新房 $/sf 差 | ↔ `B-C06` |
| `A-C07` | V1.4.5 | 相似房源 ROI 差≥30pt | ↔ `B-C07`(**注意 B-C07 语义不同**) |
| `A-C08` | V1.4.5 | 500M vs COMM 回测 | B 无 |
| `A-C09/10/11` | V1.4.5 | 法规矩阵/状态拆分 | B 无 |
| `B-C05` | V1.5.1 | (V1.5 独有语义) | A 无 |
| `B-C08` | V1.5.1 | (V1.5 独有语义) | A 无 |

实现:
1. `scripts/unified_audit.py`(新):读两套 findings CSV,
   输出 `docs/audits/unified_audit_v1/consensus_findings.csv`,
   列 `rule_id`(带前缀)、`listing`、`scenario`、`severity`、
   `detail`、`alias_of`(同义规则指针)、`status`。
2. 去重规则:`(listing, 语义等价对)` 相同 → 一条主记录 +
   `alias_of` 挂另一规则;**计数 = 语义去重后,不做行数相加**。
3. `test_unified_audit.py`:断言——每条 V1.4.5 199 发现在
   统一表中可溯源;V1.5.1 57 条同;命名空间无碰撞;
   `alias_of` 双向一致;幂等(重跑输出同 SHA)。

工作量:单文件脚本 + 测试,不重算任何估值。

## B. 双视图排名(VERIFIED 主视图 + 全方案证据视图)

现状:V1.5.1 已把 `out_of_support`/CONDITIONAL 保留估值但
排除排名——方向对,但"隐藏"是硬逻辑,用户无选择权。

方案:`render_listing_map_v1_5.py` 加**视图切换**(默认
VERIFIED 视图,不改默认排序):

- **视图 1(默认)「已验证排名」**:仅 VERIFIED ∧ 在支撑域
  方案参与最优 ROI 排序——与现状一致。
- **视图 2「全部方案证据」**:每套房源展示
  best-verified + best-conditional + best-out_of_support 三行,
  CONDITIONAL/OOS 行带明显徽标(`⚠法规`/`⚠外推`),
  ROI 列照常显示但**不参与主排序**,附注"不进已验证排名"。
- 筛选器加两项:`含条件方案` `含超支撑域`(checkbox),
  勾选才把这些行纳入排序视图——**显式用户选择**,非自动。
- `out_of_support` 行的 sale/ROI 单元格在标头注明
  "建面 >历史 P95,估值带未校准"。

数据侧改动为零:`v15_sale_and_roi.csv` 已有
`state/out_of_support` 字段,渲染层按字段分支即可。

实现:`render_listing_map_v1_5.py` 增 ~60 行(视图切换 +
筛选条件 + 徽标),`test_valuation_v1_5.py` 加断言:
- 默认视图排序结果与现 v15_roi_bridge S3 完全一致;
- 全证据视图下 OOS/CONDITIONAL 行出现且带徽标;
- 行数 = 224(不因视图增减房源);
- 真实浏览器回归(playwright-cli)覆盖切换+组合筛选。

## C. 合并/集成顺序建议

1. 先合 V1.4.5 → 已在 V1.5.1 完成(`0edf8bd`);
   我两份验收报告在 `research/v1-5-1-acceptance-review` 上,
   不阻塞主线。
2. 统一审计脚本(A)可独立做,无副作用。
3. 双视图(B)改渲染器单文件,含浏览器回归后可并入 V1.5.1。
4. watchlist 快照 SHA256 进 manifest(成本极低,先补)。

以上全部 shadow 文件,不触碰 `listing_valuation_v1_5.py`
的估值逻辑;批准后再实现。
