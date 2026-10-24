# V1.5 独立技术验收报告

- 验收对象:`f5de744b39701d6beb243035c72b16e4a5170719`
  (branch `research/listing-valuation-v1-5-backtest`)
- 对照基线:Devin V1.4.5 `98094d06d8bd7361bbdad378d995f6c6f77f795f`
- 验收方式:worktree `/tmp/v15` + 符号链接本地冻结数据,
  **完整重跑** `valuation_v1_5_backtest.py` 与
  `listing_valuation_v1_5.py` 并逐行对账;复算证据
  `recompute_evidence.csv`(165 行,`scripts/v1_5_acceptance_recompute.py` 可重放)
- 验收日期:2026-10-23;验收人:Devin(独立复核,未参与 V1.5 开发)

## 总结论

**ANCHOR 售价模型方向成立,可以进入试用;但"能更准地估实际挂牌地块售价"这个
命题只被有条件的证据支持,不支持无条件替换。**主要保留意见:(1) ANCHOR 的
默认参数是在被报告的同一 LOPO 样本上选出的,且网格中另有更优组合,头部指标
有轻度选择乐观;(2) HOUSE 时间留出只有 n=12,外推到 >3,781sf 的建案时
ANCHOR 明显失效;(3) 售价 P10/P90 是历史误差分位,不是预测区间,在社区/面积
分组后覆盖率严重偏离名义值。

| 验收项 | 结论 |
|---|---|
| 样本外表现(LOPO/时间留出/参数选择) | **CONDITIONAL** |
| 大面积 HOUSE 外推(>3,781sf) | **FAIL**(3141 E BROADWAY 等不可信) |
| ROI 74→122 归因 | **PASS**(已逐套分解,见下) |
| BCA 校准独立回测 | **CONDITIONAL** |
| P10 口径 | **FAIL 语义层面**(分组覆盖不成立,措辞必须限缩) |
| 18 条法规条件方案 | **PASS**(verified+variance→CONDITIONAL,保留测算不进排名) |
| 统计一致性 121/59 vs 122/60 | **PASS 机制确认**(展示 1 位小数舍入,非统计错误,需统一口径) |
| V1.4.5 审计融合 | **CONDITIONAL**(规则编号冲突,需命名空间合并) |

## 1. 样本外表现 — CONDITIONAL

我重跑的 LOPO 指标与提交完全一致(可复现):

| 池 | V14 | COMM | ANCHOR |
|---|---|---|---|
| HOUSE\|ALL (n=158) | 9.83% | 10.35% | **7.95%** |
| HOUSE\|CLEAN (n=81) | 9.61% | 9.78% | **8.18%** |
| HOUSE\|TEMPORAL (n=12) | 6.36% | 9.17% | **3.75%** |
| DUPLEX\|ALL (n=570) | 10.19% | 10.39% | **8.80%** |
| DUPLEX\|CLEAN (n=249) | 11.0% | 12.1% | **9.66%** |
| DUPLEX\|TEMPORAL (n=106) | 12.07% | 14.15% | **9.63%** |

**肯定的部分**:LOPO 逐折重新 `fit_k`,同 parcel_cluster 排除姊妹
duplex/重复成交,方向在所有池一致;`k='fit'` 优于 `k=0` 优于
`k=-0.55`(HOUSE 7.95/8.55/9.18)——V1.4 的 V3s 面积曲线确实偏陡,
DUPLEX k=-0.5638 反而接近 V3s。

**保留的部分**(必须披露):
- **参数在评估集上选出**:`GRID` 只在 `variant=="ALL"` 上打分,
  报告的 ANCHOR 7.95%/8.80% 是被选配置在同一评估集上的分数——
  属于选择偏差,不是嵌套 holdout。而且网格里 `R=1.0`(7.79%)和
  `K=10`(7.91%)**实际优于默认配置**,说明参数选择本身缺乏
  严格标准。
- **HOUSE 时间留出 n=12**,不足以支撑"样本外更准"的结论;
  DUPLEX n=106 的结果(9.63 vs 12.07)是较实的支持证据。
- `worst_30pct_share` HOUSE 10.8%:约 1/10 成交仍差 >30%,
  尾部分布没有被锚定方案消除。

**建议**:接受 ANCHOR 为 sale 主模型的条件是——补一个
param-selection 严格协议(内层 LOPO 选参、外层评估),或在报告
中明示默认参数为工程设定而非最优;把 HOUSE temporal 结论标注
n=12。

## 2. 大面积 HOUSE 外推 — FAIL(对受影响房源)

回测 HOUSE 池最大 `fa=3,781sf`,`>3,000sf` 仅 6 笔;ANCHOR 在
这 6 笔中的 3 笔高估 51–127%(fa3,758 实 $1,091/sf 估 $1,691;
fa3,078 实 $603 估 $1,369)。原因:拟合 k=-0.088 接近平坦,
面积衰减几乎不生效,大房按社区中位单价直接外推。

| 房源 | 方案 | 建面 | V1.5 估售 | 风险 |
|---|---|---|---|---|
| 3141 E BROADWAY | HOUSE | 4,483sf | **$6.07M @ $1,353/sf,ROI 46.6%** | 超出训练域 19%,n_near=0,不可信 |
| 715 E 63RD | HOUSE | 3,838sf | $5.43M @ $1,415/sf,ROI 38.6% | 同上,边缘外推 |
| 1861 E KENT N | HOUSE | 3,946sf | $4.87M @ $1,234/sf,ROI 26.2% | 同上 |

这三个正好是 ROI 排名最顶部的房源——**最高 ROI 的标的最依赖
最不可信的外推**。推广条件必须加 `fa>3,781sf → EXTRAPOLATED`
标记并可对这类房源强制采用保守面积曲线或降权。

## 3. ROI 74→122 归因 — PASS(已逐套分解,口径 GST-EXCL)

净变化 +48(display 口径;raw 122)。分解:

| 方向 | 数量 | 驱动 |
|---|---|---|
| 升过 ≥10% | **62** | **全部 HOUSE 方案的 ANCHOR 售价更高**(psf ↑ ~10–25%,如 3939 BOUNDARY $985→$1,349/sf、4561 NANAIMO $1,112→$1,450/sf)。无一套来自法规/方案变化 |
| 跌出 ≥10% | **13** | **~11 套法规门控**(最优方案降为 CONDITIONAL:3151/3135-3137 HEATHER、522 E CORDOVA、48 E 22ND、2284 E 45TH、2733 E 28TH、2216/266/1333 E 41ST 系、4883 BEATRICE、614 E 19TH)——属正确纪律;**2 套售价下调**(1661 E 49TH 12.2→7.9、3253 SCHOOL 10.1→9.6) |

即:**"ROI 大增"= ANCHOR 把 HOUSE 售价估高了,不是发现了新的
机会**。ANCHOR 中位偏差 ~0.99(V14 ~1.00)——它更准确但没有
系统性保守,升过 10% 线的 62 套本质上是"原先被 V14 陡面积
曲线压低的房源回到合理单价"。同时 9 套换了 `latest_asking`
(614 E 19TH +$100k 等),与 ROI 无关但影响 Ask 列。

## 4. BCA 校准独立回测 — CONDITIONAL

- `LAND|both`(n=1,964):BCA_CAL MdAPE **5.78%** vs V14_LAND
  8.38% vs BLEND 6.56% —— BCA 校准确实更准。
- `post_bca_valdate`(n=557,成交日在 2026 roll 估值日之后):
  BCA_CAL **6.62%** vs V14 8.26% —— 在真正无前瞻的子集上仍胜。
- **但**:bias_median_ratio post-date = 0.965 → BCA 校准在新样本
  上系统性高估 ~3.5%;且社区 ratio 中位数对所有历史成交统一用
  2026 年度 roll 计算——对预测"现在的挂牌"是合理的,但作为
  回测协议,更严格的版本应按成交年匹配对应 roll-year。建议补
  `ratio_year_matched` 变体后再定稿"BCA 校准替代 comps"的结论。
- C02(fair vs BCA 差>20%)触发 15 条已保留为独立对照——正确的
  双锚设计,不互相覆盖。

## 5. P10 口径 — FAIL(语义层面)

- `p10_ratio`/`p90_ratio` 是 LOPO actual/estimate **历史误差分位**
  在评估集上取的,本身不保证覆盖;按构建在合并池恰好 ~80%。
- **分组后名义 80% 不成立**:fa>3,000sf 覆盖 50%(3/6);
  Oakridge 25%(1/4)、Fairview 60%、Kensington-CC 70%。
  社区间明显失校准——一个全局分位不能宣称逐社区概率覆盖。
- 页面上 `P10 $2,999,741`/`P10 8.0%` 是**历史误差下行情景**,
  不应被读作"有 90% 把握售价不低于 X";当前文案未犯这个错误,
  但建议把标签改为"P10(历史误差下行情景)"并禁用任何
  "10% 亏损概率"表述。
- 值得肯定:roi_p10 作为压力情景展示是合理设计,只是不能当
  预测区间。

## 6. 18 条法规条件方案 — PASS

- `DUPLEX:CONDITIONAL=18` 逐条核对:`verified_raw=True ∧
  variance_required=True` → `CONDITIONAL`,保留假设售价/ROI
  但不进 VERIFIED 排名——正确实现了"可申请放宽 ≠ 已批准"。
- 全套 CONDITIONAL 共 49 条(HOUSE 31 + DUPLEX 18):18 条
  variance + 其余为空/C-*/CD-1 zoning 上 verified → CONDITIONAL,
  与 V1.4.5 的 ASSUMPTIVE_ZONING 语义兼容。
- `verified_raw` 字段保留原始标志,不混淆;`reason` 列说明
  判断依据。**建议**:给 18 条 variance 附 R1-1 3.2.2.9 条款
  引用(目前 reason 只有"lot below standard")。

## 7. 统计一致性 121/59 vs 122/60 — PASS(机制确认)

- 提交 `v15_summary.json` raw 数:**122/59/15**。
- 展示 CSV `roi_p50` 已按 1 位小数写出:边界行
  `1222 E 51ST` / `2665 WARD` 显示 "10.0",`3055 ROSEMONT` /
  `4458 SKEENA` / `5302 INVERNESS` 显示 "20.0"——按展示值过滤
  得 123/60,比 raw 122/59 各多 1。
- **机制**:展示舍入造成边界房源过线,非统计错误;但 summary
  与 CSV 筛选口径必须统一(建议 summary 同时报 raw 数与
  displayed 数,或 CSV 保留 2 位小数)。
- 用户任务中写的 121/59 vs 122/60 与本提交实测(122/59 vs
  123/60)略有出入,可能源自不同快照;机制相同。

## 8. 审计融合 — CONDITIONAL

- V1.5 分支基于 `aa45207`(早于 V1.4.5 审计提交
  `98094d0`),不含 `listing_consistency_audit_v1_4_5.py` 及
  199 条发现/9 个交付文件。
- V1.5 自带 `v15_contradictions.csv`(57 条)用 **C01–C08 但与
  V1.4.5 的 C01–C11 语义冲突**(如 V1.5 C07=verified∧variance,
  V1.4.5 C07=相似房源 ROI 差)——直接合并会规则碰撞。
- **合并方案**(供审批,本轮不执行):V1.4.5 规则集改名
  `V14A-Cxx`,V1.5 保留 `V15-Cxx`;`consistency_findings.csv` 加
  `ruleset` 列合并两集;V1.4.5 的 BCA/配对/回测结果作为 V1.5
  土地与售价模型的独立对照保留,不覆盖。

## 推广条件(建议写入 V1.5)

1. `fa > 3,781sf` 的建案标 `EXTRAPOLATED`,不参与排名或强制
   保守面积曲线;3141 E BROADWAY 的 $6.07M 不可作决策依据。
2. `n_comm < 5` 或 `n_near < 3` 的 ANCHOR 估值降级披露。
3. 参数选择补内层 holdout 或在文档声明为工程默认。
4. P10/P90 标签改为"历史误差情景",不做概率覆盖宣称;
   社区/面积分组覆盖率单独报告。
5. ROI 汇总统一 raw 数口径,CSV 保留足够精度或另报
   displayed 数。
6. 与 V1.4.5 审计产物按命名空间合并,互不覆盖。

## 复现命令

```bash
git worktree add /tmp/v15 f5de744b39701d6beb243035c72b16e4a5170719
ln -s <main>/data/research/{normalized,raw,watchlist} /tmp/v15/data/research/
ln -s <main>/reports/watchlist /tmp/v15/reports/
cd /tmp/v15 && python3 scripts/valuation_v1_5_backtest.py \
  && python3 scripts/listing_valuation_v1_5.py
cd <main> && python3 scripts/v1_5_acceptance_recompute.py
```
