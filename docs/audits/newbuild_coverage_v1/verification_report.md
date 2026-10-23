# Evidence Verification V1.2.1 — verification_report.md

日期 2026-10-23。基于 V1.2(`b890f39`),新增分层实体关联、证据四桶、
来源可行性、分层基准。**V3s 价格/ROI/排序零改动**。

## 1. MPX 实体关联(P0)

关联链 `Permit → Parcel → Building → StrataPlan → StrataLot/PID → Sale`,
逐条输出 `match_method / match_keys / supporting_sources / ambiguity /
confidence`。**仅地址匹配一律 LOW,不标 HIGH**。

- `mpx_identity_linkage_audit.csv`:全量审计目标 3,9xx 行
- 匹配法分布:`none` ~1,5xx(无 folio/许可命中)/ `permit` 250 /
  `tax_folio(+plan)` 77+15 / `land_folio` 4 / 复合数条
- 置信度:MED/HIGH 仅在有 PID+folio 或 folio+plan 双证时

### 340 个 MPX 项目核销(继承 V1.2 结论,本轮补证据链字段)

| 类别 | n |
|---|---|
| 有任何成交 | 74 |
| 许可前成交 | 73(74 地址/75 笔) |
| → CONFIRMED_TEARDOWN_SALE(旧建筑 yb<2000 或旧房形态) | 19 |
| → PRE_PERMIT_SALE(历史属性不足以定论) | 55 |
| POST_PERMIT_SALE_REVIEW | 1(2609 DUNDAS,见§2) |
| 无成交 | 266 |

## 2. 三笔特殊记录定论

| 地址 | 结论 | 依据 |
|---|---|---|
| 2609 DUNDAS | **NOT_MPX_SALE** | HOUSE_DETACHED yb2025 fa1204 — 新建小房;MPX 许可为同址另一阶段 |
| 1909 CHARLES | **OLD_MULTI_RES_NOT_NEWBUILD** | yb=1909(疑门牌误录字段)+ Multiple Residence + bca $1.67M — 旧多户楼 |
| 3122 MARINE DR SE | **VACANT_LAND_NOT_BUILDING** | Multi-Family (Vacant) + improvement=0 + 30,765sf — 空地 |

**`ZERO_VERIFIED_MPX_UNIT_SALES` 成立**:三笔复核全部排除。

## 3. HOUSE 核销(P1a)

217 可用 → 四桶:

| 桶 | n | 含义 |
|---|---|---|
| VERIFIED_FULL | ~187 | 原始字段齐+pid/CONSISTENT+GST 可判 |
| RECOVERED_NEEDS_REVIEW | ~2 | yb/fa 恢复,待二次验证 |
| KEY_UNCERTAINTY | ~28 | GST 未知或 whole-suspect 或无 pid |
| BANNED | 33 | 时点冲突(售<yb)隔离出价格池 |

**33 条冲突**:全部 `BANNED`(不入基准);不定性为"拆地销售"——
恢复 yb=新楼年份 vs 售时旧楼,缺历史属性佐证,诚实隔离即可。
`house_verified_evidence.csv` 逐条列 bucket/basis/price_source。

## 4. DUPLEX 核销(P1b)

389 条逐条标 scope/pair/pid/unit_no/strata_plan/permit_project/
match_conf — `duplex_project_unit_audit.csv`。65 组配对为异 PID 真半户
(V1.2 已定),本轮补 folio plan 交叉。

## 5. 分层基准(P3)

`verified_market_benchmark.csv` 159 格:
`区域|产品|面积带|窗口|VERIFIED_MARKET_SALES|USABLE_WITH_LIMITATIONS`
分离;n_sales/n_projects/n_permit_linked/n_first_sale/n_resale/
n_unknown_gst/p25-p75/fresh 齐全;LOCAL vs BORROWED 独立评级。

## 6. 来源可行性(P2)

见 `sales_source_feasibility.md` + `sales_source_trial_results.csv`。
关键:REALTOR api2 仅挂牌无 sold;GVR/MLS 授权=首选解;BCA Data
Advice 解 829 yb;LTSA 抽样;CoV 无成交数据集。

## 7. 剩余缺口

| 缺口 | 解法 |
|---|---|
| MPX 单元成交 | MLS 授权 / 逐项目人工收集 |
| HOUSE yb×829 | BCA Data Advice |
| 33 冲突定性 | 历史 folio/街景人工核 |
| post-2026-07-08 | 下一 arcgis 快照 |
