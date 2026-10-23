# HOUSE 5,386 全量新建识别普查(Issue #1)

基线:sales_archive_v1_0_3(冻结未改);本层=纠正研究口径。

## 普查瀑布(合计=5,386)
| 分类 | n |
|---|---|
| CONFIRMED_AGE_00_03 | 128 |
| PROBABLE_AGE_00_03 | 1 |
| REVIEW_REBUILD_STALE_YB | 52 |
| AGE_04_10 | 327 |
| AGE_11_20 | 435 |
| AGE_21_30 | 436 |
| AGE_31_50 | 927 |
| AGE_51_PLUS | 2797 |
| POST_SALE_BUILD | 1 |
| LAND_OLD_HOUSE_SALE | 23 |
| IDENTITY_CONFLICT | 0 |
| AGE_UNKNOWN | 259 |

## 0–3 年各年(VERIFIED=CONFIRMED)
| 年 | CONFIRMED | PROBABLE | 原档案00_03 | 纠正后00_03 |
|---|---|---|---|---|
| 2023 | 41 | 0 | 20 | 41 |
| 2024 | 48 | 0 | 9 | 48 |
| 2025 | 26 | 0 | 0 | 26 |
| 2026 | 13 | 1 | 0 | 13 |

- 0–3 档总量:29 → **128**(净增 99,
  与 V1.0.4 纠正口径一致;全量普查确认这 128 即全部
  可证实 0–3 成交 —— 池外新增识别为 0,另有
  52 笔带新建信号
  (许可/FIRST_SALE/池命中)但无 ≤sy 新房龄证据,
  列入 REVIEW 待人工)
- 逐条证据见 exceptions/逐笔 CSV 的
  `classification_reasons`(stale_roll|permit|nb217|
  enriched 来源可复算)

## 217 复核
- 日期+价格逐笔双校验见
  house_217_event_price_reconciliation.csv(匿名);
- GST 口径:价格差≈1.05× 标 GST_RATIO,不算错配。

## 三口径
- CAPTURED_HOUSE_SALES:5386(未加行)
- AGE_CLASSIFIED:5126
- NEWBUILD_COMPARABLE(严门):111
  (pid+CONFIRMED_00_03+价格+首销/转售;其中 PSF 可用
  111;比 V1.0.4 的 122 收紧:
  旧口径混入纠到 04_10 的行)

## 覆盖与截点
2023/24 PARTIAL_COVERAGE(last_sale 窗口截断);
2025 受 2026 转售覆盖;2026 cutoff=2026-07-08。
COVERAGE_GAP 保留:无 pid 73 行仅地址级证据。
