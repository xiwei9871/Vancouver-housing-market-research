# HOUSE Sample Recovery Gate 报告

生成:2026-10-23T15:09:39
基线:Sales Archive V1.0.3(冻结,未修改)。本层只产出
研究口径纠正,V3s 零修改。

## 为什么 2025/26 0–3 年 HOUSE 成交为 0
217 条 newbuild HOUSE comps **全部已在档案**(pid 命中 215,
地址命中 2,事件+价格完全一致 217),零缺失成交。零值根因
是房龄证据,不是样本缺失:
- 大部分(97 条)yb=2018–2021,成交时房龄 4–10 年,本来
  就不该进 0–3 档;
- 真 0–3 岁新房中,**84
  条税卷 yb 落后重建**(卷上仍是被拆旧房 yb,如 1922/1919),
  被错误归入 AGE_51_PLUS;
- **23 条档案 yb 缺失**。

## P0 逐条归属
| 类别 | n |
|---|---|
| IN_ARCHIVE_CORRECT | 110 |
| IN_ARCHIVE_AGE_STALE_TAX_YB | 84 |
| IN_ARCHIVE_AGE_MISSING | 23 |
| IN_ARCHIVE_PRODUCT_MISCLASS | 0 |
| IN_ARCHIVE_EVENT_CONFLICT | 0 |
| IN_ARCHIVE_MERGED_WRONG | 0 |
| MISSING_FIELDS_EXCLUDED | 0 |
| NOT_IN_ARCHIVE | 0 |
| IDENTITY_UNCERTAIN | 0 |

## P1 恢复(enriched/candidates 池 605 条池外记录)
- 真新建成交恢复:**3** 笔
  (0<=成交龄<=3,FIRST_SALE,均已在档案仅标签错);
- 池内但成交时是旧房:563;
- 成交后建设(土地/旧房成交):22;
- 非 HOUSE 产品(townhouse/warehouse 等):17;
- 池外疑似(FIRST_SALE+旧yb,未证实):2。
许可只作佐证,未新增任何成交记录。

## P2 三口径
- CAPTURED_HOUSE_SALES:5386(档案内 HOUSE canonical,
  恢复只改标签不加新行)
- AGE_CLASSIFIED_HOUSE_SALES:5183
  (纠正后含推断)
- NEWBUILD_COMPARABLE_SALES:122
  (pid+价格+sale_kind 核实,纠正后 0–3 档)

## P3 覆盖前后
- AGE_00_03 成交:before=29 → after=128
- 双门检:TOTAL_PRICE_ELIGIBLE(身份+产品+房龄+价格,
  不强要面积)/ PSF_ELIGIBLE(+地上面积);分组
  STRICT>= 10 严格样本 / EXPLORATORY>= 5 /
  REFERENCE_ONLY;薄组保留并给 2023–2026 与 2025–2026
  合并口径。
- 仍缺真新建首次销售的社区见
  house_price_eligibility_by_region.csv(AGE_00_03 组缺失的
  community)。

## 数据局限
- corrected_yb 来自 BCA enriched 当前卷:卷滞后是根因,
  纠正值=当前已建成房龄;corrected_yb>成交年 的行标冲突,
  不强行纠正。
- 池外疑似 2 笔仅有 FIRST_SALE+旧yb 信号,
  未计入任何统计。
- 2023/24 PARTIAL_COVERAGE 与 2026-07-08 截点口径不变。
