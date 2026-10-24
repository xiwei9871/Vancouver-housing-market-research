# V1.5.3 Economic Integrity Gate — Phase A 审计报告

- 审计基线:`research/listing-valuation-v1-5-backtest` @ `460085354039890c123ac9de728c497dd948e97d`(V1.5.2)
- 审计分支:`audit/v153-economic-integrity-phase-a`
- 性质:只读审计。V1.4.4 / V1.5.2 代码、参数、输出、排名均未修改(测试断言 V1.5.2 输出 sha256 不变)。
- 复现:`python3 scripts/economic_integrity_v1_5_3.py && python3 tests/test_economic_integrity_v1_5_3.py`

## 0. 结论摘要

| 案例 | 方案 | V1.5.2 ROI | 审计 ROI(同几何·全成本) | 审计 ROI(口径对齐) | 15% 剩余地价(对齐) | Ask | Gate |
|---|---|---|---|---|---|---|---|
| 880 E 33rd | HOUSE | 47.7% | 40.7% | **14.1%** / 5.5% | $1.38M | $1.40M | FAIL_RESIDUAL_BELOW_ASK(差 $20k) |
| 880 E 33rd | DUPLEX | 16.0% | 9.2% | 无对齐数据 | $1.25M | $1.40M | FAIL_RESIDUAL_BELOW_ASK |
| 5109 Slocan | HOUSE | 8.3% | 2.3% | −17.5% / −24.0% | $0.56M | $1.20M | FAIL_RESIDUAL_BELOW_ASK |
| 5109 Slocan | DUPLEX | −1.2% | −7.7% | 无对齐数据 | $0.68M | $1.20M | FAIL_RESIDUAL_BELOW_ASK |
| 7455 West Blvd | HOUSE | 44.4% | 38.2% | 12.2% / 3.5% | $1.62M | $1.70M | FAIL_RESIDUAL_BELOW_ASK |
| 3141 E Broadway | HOUSE | 46.6% | 40.7% | 14.4% / 7.5% | $1.58M | $1.60M | FAIL_RESIDUAL_BELOW_ASK |
| 3055 Rosemont | HOUSE | 20.0% | 14.7% | −7.2% / −13.9% | $0.98M | $1.60M | FAIL_RESIDUAL_BELOW_ASK |

对齐 ROI 两列分别为 `ALIGNED_COMPS_GEOMETRY`(按 comps 实际产品建:地上 0.458×地块 + 完工地下室 0.543×地上)和 `ALIGNED_FSR060_INCL_BSMT`(地下室计入 0.60 FSR)。

**核心发现**:V1.5.2 的高 HOUSE ROI 主要来自售价与成本的物理口径不一致,不是售价模型误差。comps 的 $/sf 以地上面积为分母,但这些成交几乎都带完工地下室;模型把这个单价乘到 0.60 FSR 的全部地上面积上,又不计地下室成本。口径对齐后,880 E 33rd 的剩余地价($1.38M)与 BCA 校准市场地价($1.33M)基本收敛;V1.5.2 口径下两者相差 $0.69M。收敛是对齐的结果,单价没有做任何调整。

## 1. 代码溯源(`data/audit/code_provenance.csv`,行号由脚本从源码实时计算)

| 项 | 文件:行 | 值 / 说明 |
|---|---|---|
| `BUILD_COST` | `scripts/listing_level_valuation_v1_4.py:42` | HOUSE 350 / DUPLEX 400 / MPX 450 $/sf,作用于 `build_sqft`(地上 FSR 面积);无 GC 报价 |
| `PERMIT_PSQFT` | `:43` | $40/sf,内容未文档化 |
| `SELLING_PCT` / `GST_PCT` | `:44` / `:45` | 4% / 5% |
| `FIN_LAND_PCT_YR` / `FIN_BUILD_PCT_YR` / `BASE_YEARS` | `:46-48` | 5% × 100% 地价 × 1 年;7.725% × 100% 建安 × 1 年;无 LTV、无分期 |
| `cost_model()` | `:534-558` | 总成本 = 地价 + PTT + 建安(+$40) + 融资 + 销售 + GST;ROI = (售价−总成本)/总成本 |
| `bc_ptt()` | `:523-531` | 1/2/3% 三档 |
| `est_anchor()` | `scripts/valuation_v1_5_backtest.py:106-150` | 社区基准(K=5 向东/西区收缩)× 附近修正(0.6km,K2=6,±15%)× `(目标单户面积/comp 地上面积)^k` |
| `fit_k()` | `:72-86` | HOUSE k=−0.088,DUPLEX k=−0.564 |
| `build_newbuild_pools()` | `listing_level_valuation_v1_4.py:309-346` | HOUSE 池:房龄档 0-5、非 strata、excl 为空、`fa` 与价格齐全;`psf = price_2026_09 / fa` |
| 排名 | `scripts/listing_valuation_v1_5.py:99-248` | 仅 VERIFIED + ANCHOR;P95 只标注不剔除(V1.5.2 起) |
| 方案 FSR 来源 | 主仓库 `scripts/rebuild_value_analysis.py:87`(**未提交**) | R1-1 HOUSE 0.60 / DUPLEX 0.70 / MPX 1.00;RM-7 按 s3.11 套 R1-1 |

**可复现性缺口**:方案几何(FSR、建面、户数、variance 标志)来自冻结的 `reports/watchlist/rebuild-value-2026-10-20.json`。生成它的 `rebuild_value_analysis.py` 在主仓库是未提交修改,分支上已提交的版本是 `zoning_rules()` 返回 HOUSE 0.70,与 JSON 里的 0.60 不一致。`docs/research/rebuild-value-methodology.md:90` 的表格也仍写 0.70。

## 2. 逐笔 comps(`data/audit/comps_trace.csv`)

- 覆盖:880、5109 × HOUSE / DUPLEX 的全部定价池记录。HOUSE 每例 158 笔,DUPLEX 每例 570 笔。所有记录都参与 `fit_k` 和东/西区先验,所以全部列出;另列 1–4 条同社区排除记录,写明排除原因。
- 每行字段:地址、成交日、名义价、原始来源文件、record_id、URL、模型社区与官方 local area、房龄(原始 yb / 修正 yb / 冲突标记)、strata、zoning、两种地块面积(档案 / BCA dims)、地上面积、完工地下室状态、laneway 许可匹配、HPI 系数、2026-09 价、尺寸调整系数、角色(COMM_BASE / NEARBY / SIDE_PRIOR / K_FIT)、社区基准占比、附近权重、**精确留一贡献**(去掉该笔并重拟合 k 后的单价变化)。
- UNKNOWN 与 NO 严格区分:
  - BCA 地下室字段缺失记 UNKNOWN,不按 0 处理。
  - laneway 文件里的 "NONE" 记 `NO_PERMIT_MATCH`,不等于确认没有 laneway。
  - garage 和装修档次全部记 UNKNOWN,数据源没有这两个字段。
- 去重:同一 `parcel_cluster` 的多条记录以 `parcel_cluster_records` 列标出。社区角色和附近角色在同一行合并,不会重复成行。
- 没有补造任何面积。地上面积缺失的记录不进池(V1.4 过滤);地下室没有数据就保持 UNKNOWN。

**HOUSE 池产品事实**:
- 有 BCA 地下室字段的 111 笔全部为 YES;明确为 NO 的 0 笔;UNKNOWN 或无原始记录的 47 笔。
- 完工地下室 / 地上面积中位 0.543。
- 有 BCA dims 的 64 笔:地上面积 / 地块中位 0.458,只有 4.7% 超过 0.60。

**880 E 33rd HOUSE**:
- 社区基准 Riley Park n=17,附近 0.6km 内 8 笔,修正系数 ×1.043,得 $1,775/sf。
- 留一贡献最大的几笔里,有 3 笔确认带 laneway 许可(648 30th E、439 22nd W、4596 Ross),4 笔为 POSSIBLE。也就是说,这些 comps 的成交价可能含 LWH 价值,但单价分母只有主屋地上面积。

**5109 Slocan HOUSE**:
- 社区基准 Collingwood(南) 只有 n=3,收缩后大部分权重来自东区先验。
- 5485 Wales St(3,078sf,$603/sf,TEMPORAL_CONFLICT)单笔把估价拉低 $160/sf。

## 3. 成本覆盖(`data/audit/cost_coverage.csv`,28 项)

V1.5.2 `cost_model` 中标记为 UNKNOWN(未计入)的项:拆除、地下室建造、预备费、City DCL、ACC、Metro DCC、TransLink DCC、市政接驳、持有期地税、保险、贷款费。另外 $40/sf「办证」的具体内容没有文档,审计只计一次;如果 $40 里已含 DCL,审计最多重复计 $7.53/sf,已在表中披露。

审计口径(费率取自主仓库影子脚本 `cost_audit_round6_20261020.py`,sha256 `8b6eea82…`):
- 拆除 $35k;预备费 5%;DCL $7.53/sf;ACC $2.32/sf;
- Metro DCC 按 2027 费率、净增户计;TransLink 按户数并抵扣 1 户 SFD;
- 接驳 $40–70k;地税 0.45%/年;保险 $12k/年;
- 融资:土地 LTV 60% @6%、建安 LTV 65% @9%、线性放款、贷款费 1.5%;周期 18 个月(许可 6 + 施工 10 + 2);
- 地下室 $200/sf(仅对齐方案;文档区间 $150–250,无报价)。

UNKNOWN 且未量化:GST ITC、新房 GST 退税对售价的影响、BC 住房炒卖税(builder 豁免条件未取得官方文本)、空置/投机税、BC Housing 注册与 2-5-10 保修、ACC 豁免所需的市政府认定面积。

## 4. 税费与融资公式单独验证(`tests/`)

- PTT:逐例手算 1/2/3% 分档,$1.4M → $26,000,完全一致。本组案例地价都低于 $3M,所以没有用到 $3M 以上的附加档,不影响结果。
- GST:V1.5.2 标签是 "EXCL",但实际按售价 × 5% 计成本。经济上这等同于「售价含 GST」,而且比正确的 5/105 多计 0.24% 售价。审计默认 `INCL_NO_ITC = sale × 5/105`,EXCL 和含 ITC 作为敏感性列出。
- 融资:土地利息、建安利息、贷款费逐月独立重算,误差小于 $2。
- 剩余地价:二分法解出后代回引擎,ROI = 15.00%;并验证 RL0 > RL10 > RL15 单调。

## 5. HBU(`data/audit/hbu_matrix.csv`)

官方 bylaw PDF(`bylaws.vancouver.ca` R1-1 / RM-7)在 2026-10-24 返回 Cloudflare 验证页。按规则没有绕过,因此**没有任何方案能标为 LEGALLY_VERIFIED**。

| 案例 | HOUSE | DUPLEX | TRIPLEX | MULTIPLEX |
|---|---|---|---|---|
| 880 E 33rd(R1-1,4,012sf,56.8×75.2ft) | COND_UNVERIFIED | COND_UNVERIFIED | NOT_PERMITTED(进深 75.2 < 99.7ft) | NOT_PERMITTED(同左) |
| 5109 Slocan(RM-7,3,536sf) | COND_UNVERIFIED(s3.11 套 R1-1,未核原文) | COND_UNVERIFIED | COND_UNVERIFIED(s3.4 conditional,0.60 起) | UNKNOWN |
| 7455 West Blvd | COND_UNVERIFIED | COND_UNVERIFIED | COND_UNVERIFIED | COND_UNVERIFIED |
| 3141 E Broadway / 3055 Rosemont | COND_UNVERIFIED | COND_UNVERIFIED | NOT_PERMITTED(大地块最少 4 户条款) | COND_UNVERIFIED |

- 所有方案都把法定最大 FSR 直接当作可售面积。退线、高度、覆盖率、浅地块的体量可实现性都没有检查,记 UNKNOWN。
- 880 进深只有 75.2ft,0.60 FSR 能否在退线内实现尤其存疑。
- 先前文档 `v2-4-3-product-decision-basis` 援引 R1-1 4.1.1 / 4.1.2,认为地下室计入 FSR。若成立,`ALIGNED_COMPS_GEOMETRY`(合计约 0.71 FSR)需要 cellar 豁免才合法,而 comps 本身的合法路径也未核实。

## 6. 经济闭环(`data/audit/residual_land_value.csv`、`residual_gap_attribution.csv`、`cashflow_*.csv`)

**880 E 33rd HOUSE 剩余地价(目标 ROI 15%)分解**:

| 步骤 | 剩余地价 | Δ | 证据强度 |
|---|---|---|---|
| S0 V1.5.2 模型 | $2.17M | — | 仅模型 |
| S1 同几何 + 全成本 + LTV 融资 | $2.02M | −$145k | MEDIUM(费率来自影子脚本;建安档未验证) |
| S2 按 comps 产品建(含地下室成本) | $1.38M | −$643k | MEDIUM(BCA 字段;地下室单价无报价;合法性未核) |
| S2′ 地下室计入 0.60 FSR | $1.19M | −$830k | MEDIUM |

市场地价(BCA 校准)$1.33M − 对齐后剩余地价 $1.38M = −$0.05M,基本收敛;V1.5.2 口径下为 −$0.69M。
5 个案例的 HOUSE 方案中,S2 都是最大的单项(−$0.38M 到 −$0.90M);全成本补齐一项为 −$0.13M 到 −$0.20M。

**敏感性**(全部列在 CSV 中):售价 ±10% / −20%、建安 +$50 / +$100、周期 24 / 30 月、利率 +200bp、GST 三口径、地下室 $250、组合情景(−10% & +$100 & 24 月)。
以 880 HOUSE 对齐方案为例:基准 14.1%;售价 −10% 为 3.8%;24 个月为 11.8%;GST 按 EXCL 计为 20.7%。

**Gate 判定**:以 15% 目标利润下的剩余地价对比 Ask,5 个案例 10 个方案全部 `FAIL_RESIDUAL_BELOW_ASK`。
- 880 HOUSE 只差 $20k;按 10% 目标的剩余地价是 $1.50M,高于 Ask。
- DUPLEX 池 570 笔没有 BCA 地下室字段,无法做口径对齐,判定只基于模型几何加全成本。

## 7. 已确认错误

1. **COST_AREA_MISMATCH(主因)**:售价单价来自带地下室的成交,却乘到 0.60 FSR 全地上面积,且不计地下室成本。V1.5.2 沿用了 V1.4 的这一缺陷。早在 `docs/research/v2-4-1-cost-area-reconciliation` 中就已记录,但没有修复。
2. **成本漏项**:拆除、预备费、四项市政收费、接驳、持有成本、贷款费都不在 `cost_model` 中。补齐后剩余地价下降 $127k–$201k。
3. **GST 标签与公式不一致**:标为 "EXCL",实际按 5% × 售价计成本,比 5/105 多计 0.24% 售价。
4. **社区别名与官方边界不一致**:listing hood "Fraser" 映射到 Riley Park,但按官方 local area 边界,880 E 33rd 落在 Kensington-Cedar Cottage(距 Riley Park 边界 299m)。224 套中有 4 套受影响。改用 KCC 基准时,880 HOUSE 单价为 $1,545/sf,比现值低 13%。
5. **方案几何不可复现**:生成器未提交,已提交版本的 FSR 与冻结 JSON 不一致。

## 8. 待核实(UNKNOWN)

- R1-1 / RM-7 原文:地下室或 cellar 是否计入 FSR;浅地块 0.60 FSR 的体量可实现性;RM-7 s3.11 与 s3.4。需要人工下载 PDF。
- comps 的 GST 口径(全部是 UNKNOWN_LIKELY_*);laneway POSSIBLE 记录共 80 条。
- 建安 $350/400/450 与地下室 $200/sf:没有 GC 报价。
- ACC 豁免所需的「市政府认定原建筑面积」;TransLink 重建抵扣规则;BC 炒卖税的 builder 豁免条件。
- 5109 Slocan:Ask/BCA 只有 0.67,而且现有房是 1992 年建、改良值 $257k。挂牌价远低于评估值的原因不明,可能涉及房屋状况、产权或其他未披露事项。

## 9. 不可复现事项

- 干净 clone 无法运行:输入在 `data/research/raw|normalized`(gitignore),审计里由 worktree 符号链接接入。
- 审计脚本按绝对路径读取主仓库的三个未跟踪文件:`vancouver-local-area-boundary-official.geojson`、`laneway-flags-v2.json`、`rebuild_value_analysis.py`(未提交版本)。
- bylaw 原文无法自动获取(Cloudflare)。

## 10. 是否进入 Phase B

**建议进入 Phase B,但只在以下决策获批后开始,不直接改模型**:
1. 产品定义:HOUSE 目标产品是否含完工地下室。这决定售价口径和成本口径如何对齐。
2. 成本引擎是否补入第 3 节列出的漏项,以及 GST 标签与公式的修正方式。
3. 社区映射:是否按官方 local area 修正 "Fraser" 等别名,或者把边界附近的房源改为两社区加权。
4. 外部证据:人工取得 R1-1 / RM-7 原文;为 880 E 33rd 取得至少一份 GC 预算报价(按 18 个月和 10% 目标看,它是唯一接近盈亏线的核心案例)。

未获批准前,V1.4.4 / V1.5.2 的估值与排名保持原样。
