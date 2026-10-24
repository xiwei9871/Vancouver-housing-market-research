# 产品分类规则 — V1

## 许可侧(issued-building-permits)

优先 `specificusecategory`,描述文本兜底;附属单元记 tags 不单列产品:

| 规则 | 产品 | tags |
|---|---|---|
| Laneway House | LANEWAY(辅助池,不入三主池) | — |
| Single Detached (+Sec Suite)/Infill/1FD w Family | HOUSE | SEC_SUITE/INFILL/CUSTOM |
| Duplex*(含 w/Secondary Suite、Lock-off 组合) | DUPLEX | SEC_SUITE/LOCKOFF |
| Multiple Dwelling / 描述含 multiplex | MPX | — |
| 其余 | OTHER | 不强行归类 |

单元数: `N-unit front/rear building` 解析;`two-family`/`3 SFD` 词汇兜底;
DUPLEX 默认 2、HOUSE 默认 1(+laneway 附属)。描述无单元数 → NULL 不猜。

## 成交侧(comps-v2 product_class)

| product_class | 归入 |
|---|---|
| HOUSE_DETACHED | HOUSE_POOL(含 suite/laneway,附属不单列) |
| DUPLEX_* | DUPLEX_POOL(半户/整栋按记录口径) |
| MULTIPLEX_3_4_CANDIDATE / MULTIPLEX_WHOLE_BLDG | MPX_POOL |
| STRATA_UNIT_UNKNOWN / OTHER | **UNKNOWN — 不入任何主池**(959→265 分界) |

## 单元级价格口径(防双算)

- DUPLEX 半户成交: $/sf = 半户价 ÷ 半户地上面积;整栋价不除以半户面积。
- MPX strata 单元: 每单元独立记录;不得用"单套价×单元数"作项目总收入
  (面积/楼层/户型/时点差异);整栋成交与单元成交分列。
- Basement/LWH: 计地上面积为分母,地下与 LWH 贡献单独建模(B400 现行)。
- GST: `gst_basis` 记录 new_first_sale/resale/unknown_no_yearbuilt;
  new-build 基准以 new_first_sale 为首选,resale 标口径。

## 状态机

许可: ISSUED(数据只有签发态;取消/完工不由本表证明)
完工: folio 出现 + yb≥2018 + 2023→2026 folio 新增 = 弱完工证据(COMPLETED?)
销售: 成交记录存在 = SOLD(预售 vs 交割由 gst_basis 区分)
