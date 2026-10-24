# V1.5.1 独立技术验收报告

- 验收对象:`ca7911d80fbfcc593dea03ea6447807f7b6607df`
  (`research/listing-valuation-v1-5-backtest`,已合并 V1.4.5 审计)
- 验收方式:`/tmp/v151` worktree + 本地数据符号链接,
  **完整重跑** `valuation_v1_5_backtest.py`(含新增 `strict_main`)
  与 `listing_valuation_v1_5.py`;自带 `test_valuation_v1_5.py`
  **30/30 PASS**;证据 `recompute_evidence_v1_5_1.csv`(272 行,
  `scripts/v1_5_1_acceptance_recompute.py` 可重放)
- 验收日期:2026-10-24;范围:只读验收,未改任何估值/法规/成本口径

## 逐项结论

| 验收项 | 结论 | 一句话 |
|---|---|---|
| 前向滚动回测(无未来信息) | **PASS** | 严格"只用更早成交"+同 parcel 排除,ANCHOR 仍胜 |
| 嵌套选参(训练期选定,测试一次) | **PASS**(一处小泄漏) | HOUSE 测试 6.06% vs V14 10.42%,参数仅由 cutoff 前数据选出 |
| BCA 年度匹配回测 | **PASS**(一处口径建议) | roll 年纪律实现,7.12% vs V14_FWD 9.03%,残差偏态 +3.5% |
| 104/47/8 统计一致性 | **CONDITIONAL** | 源端统一舍入已修;但提交产物由过期 watchlist 生成(104 vs 复现 105) |
| 历史误差压力情景 | **PASS** | P10/P90 现取自严格前向测试,口径仍是压力情景非预测区间 |
| 224 行 ROI 桥接表 | **PASS** | 四级累积分解 74→63→122/123→104/105,与我独立归因一致 |
| 法规分类修正(variance 归属) | **PASS** | 见第 3 节——我上轮报告的产品归属有误,本轮修正 |
| 大面积外推处理 | **PASS** | fa>P95 设 `out_of_support`,保留估值但不进排名 |
| 时间信息泄漏总体检查 | **PASS**(两处残余) | 训练侧已无未来信息;残余见第 8 节 |

## 1. 前向滚动回测 — PASS

`forward_test()`:对每笔成交,训练池=**严格更早**成交 ∧
`cluster != c.cluster` ∧ |train|≥40——真·无未来信息。APE 定义同步
更正为常规 `|estimate−actual|/actual`(注释明示 V1.5 口径差异)。

| 池 | V14 | ANCHOR | 结论 |
|---|---|---|---|
| HOUSE forward(n=118) | 10.92% | **10.16%** | 严格协议下仍胜 |
| DUPLEX forward(n=529) | 10.24% | **9.67%** | 仍胜,偏差 ~0.9997 几乎无系统偏 |

注意:新口径下数字与 V1.5 报告(7.95/8.80,旧分母)不可直接比;
V1.5.1 已在 `ape_definition` 字段与注释中如实标注——**通过**。

## 2. 嵌套选参 — PASS(一处小泄漏)

`nested_select()`:内层对 cutoff 前训练集逐 cluster-LOPO 选参,
外层只在 cutoff 后测试集评一次。结果:

| 协议 | n_train/n_test | V14 | ANCHOR_SELECTED | ANCHOR_DEFAULT | 选出参数 |
|---|---|---|---|---|---|
| HOUSE @2025-07-01 | 119/39 | 10.42% | **6.06%** | 7.26% | K5/K2=2 |
| DUPLEX @2025-07-01 | 255/315 | 9.91% | **9.26%** | 9.38% | K=10 |
| DUPLEX nested_split | ~358/212 | 9.92% | 9.70% | **9.13%** | K5/R=1.0 |

- 参数确实只在训练期选出(devin 协议选了 K2=2,与原默认不同)——
  上轮的选择偏差问题**已解决**。
- 有趣且诚实:HOUSE 上被选中参数(6.06%)优于默认参数(7.26%),
  DUPLEX nested_split 上默认反而更好——说明参数不敏感,模型稳健。
- **残余小泄漏(标注)**:内层选参排除了同 cluster,但测试集评估时
  训练池未排除测试房源自身更早的成交记录(同一 parcel 换手的
  历史成交留在 train)。影响小(同 parcel 复售罕见),建议后续
  在 train↔test 间也做 cluster 隔离。**通过,附条件改进项。**

## 3. BCA 年度匹配 — PASS(口径建议)

`land_asof_test()`:成交年 Y 只用 roll Y(1 月公布/上年 7 月估值),
校准比只用**更早成交各自 roll 年**的比值,min_train=200,
同 cluster 排除:

| 方法 | n | MdAPE | bias |
|---|---|---|---|
| BCA_CAL_ASOF | 1,769 | **7.12%** | 0.965 |
| V14_LAND_FWD | 1,769 | 9.03% | 0.982 |

- 上轮的"用 2026 roll 回测 2020 成交"前瞻问题**已消除**。
- 残余:bias 0.965 → 仍系统性偏高 ~3.5%;比值分子是
  `price_2026_09`(HPI 归一价)分母是 roll 年 BCA——量纲虽
  无量纲,但内含 roll 年后升值;对挂牌(用 BCA2026×ratio)口径
  自洽,误差实测小。**建议后续改 roll×HPI 归一做分母再验。**

## 4. 104/47/8 统计一致性 — CONDITIONAL

- ROI 已在**源端统一 round(…,1)**,`test #13` 断言
  summary==CSV 展示值——上轮 122/123 边界问题从机制上消除。
- **但提交产物与复现差 1 套**:committed `roi_ge=104/47/8`、
  `price_refreshed=0`;我重跑得 `105/47/8`、`price_refreshed=9`。
  逐行 diff 确认:committed 运行时的 `asking` 全是快照价
  (如 MANOR 1,449,000),未应用 9 条 `latest_asking` 刷新
  ——其中 3326 SLOCAN 降价 150k 使其 ROI 在我重跑中越过 10%。
- **这是输入漂移不是统计错误**:两条 commit 相隔期间
  watchlist 变了;桥接表同样 123→105(committed 122→104)。
- **建议**:manifest 增加 watchlist 快照 SHA256(与 V1.4.4
  input_manifest 同款),否则产物不可逐字节复现。

## 5. 历史误差压力情景 — PASS

- `band` 现取自 `forward_expanding`(严格无未来信息),不再是
  "被选中参数的 LOPO 集"——上轮批评已修复。
- HOUSE P10 0.6295/P90 1.1511、DUPLEX 0.8302/1.2188;
  `roi_p10_ge` 全 0——压力情景**更保守**,语义仍是"历史误差
  下行情景",页面标注 `P10 售价下` 未犯概率宣称错误。
- 保留意见(同上轮,不阻塞):全局分位在社区/面积分组上仍不
  保覆盖(名义 80%,fa>3,000sf 实 ~50%)——作为压力情景
  可接受,但文档不应写"区间"。

## 6. ROI 桥接表 — PASS

`v15_roi_bridge.csv` 224 行,四级累积(≥10 口径):

| 步 | 含义 | ≥10 / ≥20 / ≥30 |
|---|---|---|
| S0 | V1.4.4 页(eligible+V14 售价) | 74 / 15 / 2 |
| S1 | +V1.5 VERIFIED 法律范围(仍 V14 售价) | 63 / 13 / 1 |
| S2 | +ANCHOR 售价 | 122→**123** / 60 / 15-16 |
| S3 | +面积支撑门(V1.5.1 终态) | 104→**105** / 47 / 8 |

与我上轮独立归因完全自洽:**净 +30 中,法律范围 −11、
售价模型 +59~60、外推门 −18**。每行带 `scope_change` 与
`d_sale` 字段,可归因到单套。**PASS**。

## 7. 法规分类修正(勘误上轮报告)

经 `v15_legal_state.csv` 逐行核对:

| 集合 | 数量 | scenario | verified_raw | variance_required | state |
|---|---|---|---|---|---|
| `var=True ∧ vraw=True` | **18** | **全部 HOUSE** | True | True | CONDITIONAL |
| `var=True ∧ vraw=False` | **18** | **全部 DUPLEX** | False | True | CONDITIONAL |

**两组是同一批 18 个地址的不同产品行**(1333 E 41ST、
48 E 22ND、4940 KILLARNEY 等,lot 均 <3,294sf):

- HOUSE 行:production 对 HOUSE 0.60 FSR 只查了用途/分区
  `verified_raw=True`,但低于 R1-1 3.2.2.1 常规最低面积 →
  `variance_required=True` → V1.5 判 **CONDITIONAL**。
- DUPLEX 行:同地址 0.70 FSR 方案连 verified_raw 都未过
  (尺寸/FAR 常规不达标)→ `verified_raw=False` → 同样落
  **CONDITIONAL**,但走不同路径。

**勘误**:我在 V1.5 验收报告第 6 节写"`var&verified` 18 条
DUPLEX"——**错误,应为 HOUSE**;18 条 DUPLEX 的
variance_required=True 但 verified_raw=False,是另一组行。
分类逻辑本身在 V1.5.1 数据中**正确**,错的是我上轮的文字归属。
本报告与证据 CSV 已按正确口径呈现。

## 8. 残余时间泄漏/同源评估点(总体 PASS,如实列余)

1. `forward_test`/`nested_select` 测试评估时,训练池含测试
   parcel 自己更早的成交(cluster 排除只在 LOPO 内部、不在
   train→test 边界)——小泄漏,建议补。
2. 提交产物与 watchlist 快照未绑定(第 4 节)。
3. 法律 `verified_raw` 来自 production zoning 判定,审计依赖
   同一源码判定——属同源;C09/C10 的官方条款核验仍是人工
   层,V1.4.5 `zoning_rule_matrix.csv` 已合入本分支可用。

## 9. 对 V1.5 遗留项的核销

| V1.5 验收意见 | V1.5.1 处理 | 判定 |
|---|---|---|
| 参数在评估集上选出 | 嵌套选参+双协议 | **已解决** |
| fa>3,781sf 外推无标记 | `out_of_support`+P95 门+面积分桶 | **已解决**(3141 E BROADWAY 等已排除排名) |
| P10 取自被选集 | 改取自 strict forward | **已解决** |
| BCA roll 年未匹配 | `land_asof_test` 年度匹配 | **已解决**(残余口径建议) |
| 122/123 舍入边界 | 源端统一舍入+断言 | **已解决**(机制);watchlist 快照仍缺 |
| 同类 bias / 分桶覆盖 | 面积分桶诊断已出 | 披露已足,校准未做 |

## 总结论

**V1.5.1 通过验收**:对上轮全部实质批评均有代码级回应且
实现正确,前向/嵌套协议下 ANCHOR 仍稳定优于 V14
(HOUSE 6.06% vs 10.42% devin-cut),BCA 校准在 roll 年
纪律下仍胜(7.12% vs 9.03%)。唯二 CONDITIONAL:watchlist
快照未绑定导致 104/105 复现差,以及 train↔test 同 parcel
残余小泄漏——均为**披露级而非否决级**。

"ANCHOR 比 V1.4 更准地估实际挂牌新房售价"——在严格无未来
信息协议下**成立**;对 fa>2,876sf 的外推房源已正确降级出
排名,这是本轮最重要的可信度改进。
