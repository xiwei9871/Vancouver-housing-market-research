# Evidence Gate V1.2.2 — pipeline_regression_report.md

日期 2026-10-23。基于 V1.2.1(`5a6ebbd`),关闭评审指出的四处缺陷。
**V3s 价格/ROI/排序零改动。**

## 评审缺陷 → 修复对照

| 评审问题 | 修复 |
|---|---|
| 同一 folio 的 pid+plan 被当两个源 | `n_independent_sources` 按源集合计;tax_folio 单源=1 |
| 字段数≥2 自动 HIGH | HIGH 必须跨实体:`strata_plan_in_project` 或 `sale_pid_is_project_parcel` |
| 94 条 HIGH 存疑 | **重检:仅 15 条保留 HIGH,79 条降级**(真跨实体证据) |
| 73 笔/74 笔口径混淆 | `mpx_sale_count_reconciliation.csv`:74 tx ∈ 73 项目 |
| 旧建筑≠已拆除 | 19 条改 `PRE_PERMIT_OLD_BUILDING_SALE`;`CONFIRMED_TEARDOWN=0`(LDH 集无拆除许可,不臆断) |
| 特殊记录是代码字符串 | `mpx_specials_evidence.csv` 带 comp/arcgis 字段级证据 |
| 幂等测试假阳性 | **真双 subprocess 重跑 + SHA256 比对** |
| 首尾比较丢中间事件 | `price_events.py` 完整事件链(RELISTED/DROP/INCREASE/UNKNOWN) |
| 测试依赖私有数据 | PURE/FIXTURE/INTEG 分层;无数据 → SKIPPED |

## 关联重检结论(诚实降级)

| 置信度 V1.2.2 | n | 含义 |
|---|---|---|
| HIGH | **15** | strata plan ∈ 项目 folio 集 = 真跨实体 |
| MED | 6 | 双源无跨实体证明 |
| LOW | 331 | 单源 |
| NONE | 1,179 | 无任何关联证据 |

旧 94 条 HIGH 只有 15 条经得起跨实体标准——评审判断正确。
`review_queue` 现挂 ambiguity/冲突(本轮 0)。

## MPX 成交核销(口径守恒)

```
340 项目 → 74 项目有成交 → 75 笔
  ├─ 许可前 74 笔 ∈ 73 项目(1 项目 2 笔)
  │    ├─ PRE_PERMIT_OLD_BUILDING_SALE 19(yb<2000/旧形态,拆除未证实)
  │    └─ PRE_PERMIT_SALE 55(属性不足)
  └─ POST_PERMIT_SALE_REVIEW 1(2609 DUNDAS)
CONFIRMED_TEARDOWN_SALE = 0(LDH 许可集仅 New Building,拆除许可不在源内)
ZERO_VERIFIED_MPX_UNIT_SALES 不变
```

## 双维分级(来源验证 × 市场可比性)

| source_verification | n | comparability | n |
|---|---|---|---|
| SOURCE_VERIFIED(arcgis 双源交叉) | 2,285 | DIRECT_COMPARABLE | 576 |
| SOURCE_TRACEABLE(pid+CONSISTENT) | 944 | ADJUSTED_COMPARABLE | 30 |
| UNVERIFIED | 2,392 | NOT_ELIGIBLE | 5,015 |

DIRECT 内:first_sale 216 / resale 360;custom_estate_suspect 252
(高价 AND 大地块双条件)。

## 价格事件链(P0A)

- `scripts/price_events.py` 纯函数;`render_v3s_shadow.py` 接链显示
  最近事件+累计
- `price_event_chain_audit.csv`:52 个有事件的挂牌全量审计
- 4 真案回归:KILLARNEY=RELIST_LOWER✓ SLOCAN=PRICE_INCREASE✓
  E8TH=RELIST_HIGHER✓ E19TH=2×PRICE_DROP✓

## 测试

| 套件 | 结果 |
|---|---|
| test_price_events.py | **21/21 PASS**(8 合成夹具+4 真案+审计csv) |
| test_newbuild_v1_2_2.py | **25 PASS / 0 FAIL / 0 SKIPPED** |
| └ 幂等 | 双 subprocess SHA256 一致 |
| test_newbuild_evidence_v1_2 | 24/24 回归(未动) |

## 剩余边界(诚实标注)

1. HIGH=15 仍依赖 tax folio plan 字段完整性;EPS plan 与许可的正式
   对应关系需 LTSA/BCA 源补证 → 采购方案 C
2. SOURCE_VERIFIED=2,285 的"双源"是 comps↔arcgis 交叉;两者均可能
   源自同一 MLS 上游 → MLS 授权源接入前保留 TRACEABLE 语义
3. custom_estate 为启发式;无 builder/定制标识源
4. 事件链 scan 为抓取日,挂牌发生日 ≤ 观测日
