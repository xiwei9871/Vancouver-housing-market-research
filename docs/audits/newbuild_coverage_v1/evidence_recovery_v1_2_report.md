# Evidence Recovery V1.2 — 项目身份与销售证据核销报告

管线: `scripts/newbuild_evidence_v1_2.py`(纯函数 `nbx_core.py`)
产物: `data/research/normalized/audit_v1_2/` · 快照 2026-07-08 · 零 V3s 改动

## 一、P0:MPX 证据链核销(permit→parcel→building→strata→sale)

341 条 MPX 许可 → **340 个项目**(1 组同址双许可)。逐项目关联
tax folio 与成交记录,按签发日切分售前/售后:

| 状态 | 项目数 | 含义 |
|---|---|---|
| TEARDOWN_SALE_ONLY | 73 | 有成交但全部在许可签发**之前** — 旧房土地销售,非 MPX 单元 |
| POSSIBLE_POST_PERMIT_SALE | 1 | 2609 DUNDAS:2024-12 许可,2026-06 成交 $1.16M — 疑含许可土地易手,待人工 |
| HAS_STRATA_NO_SALE | 0 | 尚无分层 folio 出现在 tax26 |
| PERMIT_ONLY | 266 | 仅许可,无成交无分层证据 |

**成交证据链结论:MPX 项目存在、但单元级销售未进入已捕获成交源。**

- `ZERO_VERIFIED_MPX_UNIT_SALES_IN_CAPTURED_DATA = true`
  (捕获数据零 MPX 单元成交 — 不等于温哥华没有,MLS/GVR 一手渠道未接入)
- 2 条**整栋级** CONFIRMED_MULTIPLEX 成交(1909 CHARLES $3.8M "Multiple
  Residence" + 3122 MARINE DR SE $1.93M "Multi-Family")— 证据有效但缺单元
  面积,仅作 PARTIAL_EVIDENCE
- 43 条 UNRESOLVED(strata/whole 判定证据不足,保留待核)

误分类复核:审计集 959 条(STRATA_UNKNOWN 635 + CANDIDATE 230 +
WHOLE 35 + OTHER 59)按 permit 项目类型 + BCA 形态 + folio 分层联合判:
TOWNHOUSE 752 / APARTMENT 152 / UNRESOLVED 43 / OTHER 10 /
CONFIRMED_MULTIPLEX 2。**允许 approved=MPX + form=TOWNHOUSE 共存**——
两条 CONFIRMED_MULTIPLEX 均来自 whole-bldg 描述而非仅 zoning。

R1-1 multiplex:340 个项目中 mpx_era(≥2023-10-01 签发)已在册;
识别范围不限 RM-8N(zoning 仅作旁证,不单独立案)。

## 二、P1a:HOUSE yb 核销恒等式(闭合)

```
缺失总数 2,369 = HOUSE 2,351 + 非HOUSE 18(OTHER×17 + MPX_WHOLE×1)
HOUSE 2,351 = 新建确认 50(ENRICHED 35 + TAX 15)
            + 旧房确认 1,472(ENRICHED<2018)
            + AMBIGUOUS 0 + NO_SOURCE 829
瀑布 yb_missing HOUSE = 829 = NO_SOURCE(与 V1.1 的 832 差 3:
        V1.1 口径含 3 条后重分类记录,V1.2 产品重判后自然闭合)
```

**时点冲突拦截 33 条**:enriched 恢复的 yb=2025 > 成交年 → 该成交为
重建前旧房土地销售(如 4363 9TH AVE W 2024 成交/yb2025)。
`usable()` 已加 `_yb_conflict` 排除 → HOUSE 可用 250→**217**。

HOUSE 250 拆分四级:VERIFIED_COMPARABLE 187 · USABLE_WITH_LIMITATIONS 28
(yb/fa 恢复或口径软缺陷)· PARTIAL 2(无 pid 且非 CONSISTENT)。

## 三、P1b:DUPLEX 单元身份

553 组核销(`duplex_unit_identity_audit.csv`):
**PAIRED_UNITS 65 组**(≥2 个独立 pid/单元键,两户各自成交,双双保留)·
RESALE_SAME_UNIT 3(同 pid 重复出售,不是两户)· WHOLE 15(整栋口径)·
LONE 470。68→65+3 修正:3 组"疑似配对"实为同一半户重售。

## 四、P2/P3:基准 V1.2 与证据血缘

- 156 格 `regional_benchmark_v1_2.csv`:每格 `n_sales` +
  `n_projects`(基址去重)+ `n_projects_permit_linked`(真实许可链)+
  `n_first_sale / n_resale / n_unknown_gst` 三分(未知不默认二手)+
  `fa_src_enriched / yb_src_recovered` 血缘计数 + `fresh`
- `confidence`: `LOCAL_A/B/C` vs `BORROWED_A/B/C` 独立评级
- 锚点三件套(1185E17/2549KENT/78MALTA)仍全格剔除
- MPX 零格(诚实);影子 445 行保留对照、不出新 ROI 结论

## 五、最终验收表

| 指标 | HOUSE | DUPLEX | MPX |
|---|---|---|---|
| 已确认成交记录 | 217 | 389 | 0 单元(2 整栋 PARTIAL) |
| 独立项目数 | 3,563 许可项目 | 553 成交项目/1,730 许可 | 340 许可项目 |
| 可售单元数 | — | 130+ 半户(65 对已证) | ~2,7xx 许可单元估计 |
| VERIFIED_COMPARABLE | 187 | 389 | 0 |
| USABLE_WITH_LIMITATIONS | 28 | — | — |
| PARTIAL | 2(+2,437 非新建) | 233 | 2 |
| UNVERIFIED | 2,347 | — | 43 |
| 最新成交 | 2026-07-08 | 2026-07-08 | 2025-07-03(整栋) |

## 六、剩余缺口

1. HOUSE yb 829 条 NO_SOURCE(需 BCA 明细授权)
2. MPX 单元成交 + 单元建面 — 需 MLS/GVR 一手渠道或开发商销售表
3. 2609 DUNDAS 1 条 post-permit 成交待人工定性
4. 43 条 UNRESOLVED strata + 16 条 arcgis 混合用途
5. 成交源截点 2026-07-08,之后市场动态未知
