# Newbuild V2.2 报告

基线 d2b26bc;V3s 冻结。cutoff=2026-07-08。
匿名=HMAC-SHA256。竣工证据无 occupancy 源→
最高 ASSESSMENT_COMPLETION,不冒名 VERIFIED。

## P0-A 竣工证据分层(128)
- ASSESSMENT_COMPLETION:87
- OCCUPANCY_VERIFIED:0(数据源缺,如实 0)
- 其余 NEW_BUILDING_AGE_SUPPORTED/NONE:
  41
  缺什么:无 Final-Inspection/Occupancy 免费源;
  需 BCA/市政竣工记录才能升 COMPLETION_VERIFIED。

## P0-D 价格门(累计,不跳档;域=全部 5,386 HOUSE)
| 门 | n |
|---|---|
| SALE_VERIFIED | 4967 |
| NEW_HOUSE_STATUS_VERIFIED | 128 |
| TOTAL_PRICE_COMPARABLE | 4967 |
| PSF_COMPARABLE | 3001 |
| GST_BASIS_VERIFIED | 0(无 GST 免费源) |
| NEWBUILD_PRICING_READY | 101 |

注:前四门为全 5,386 的独立证据统计;
READY=五门交集(V2.1 严格 111 中剔除小面积/
非 freehold/面积口径不合者)。
- 小面积(<1200sqft)逐笔:见 111 对账 small_area_flag;
- GST:FIRST_SALE→UNKNOWN_LIKELY_INCLUDED+5% 净价情景;
  RESALE→UNKNOWN_LIKELY_EXCLUDED。

## P0-B 队列瀑布
- A:589 疑似(V2.1 口径):见
  candidate_589_identity_waterfall.csv
- B:52 REVIEW:见 review_52_final_reconciliation.csv
- C:旧房突变反例:334 行
- D:无匹配地块 jump=10925,无成交匹配=
  7763
  (标 SALE_NOT_FOUND_IN_ARCHIVE,不判未售)

## 血统
CONFIRMED 62 / SPLIT 162 /
全部:255
多后继不再自动 CONFIRMED;duplex 拆分不误并。

## 四池统计
first_sale_vs_resale_by_region.csv:
A=0-3 首销,B=0-3 转售,C=4-10 近新,D=11+旧房,
分池不混。

## 阻塞(免费数据尽头)
- Occupancy/Final-Inspection:无合法免费源;
- GST 口径:MLS/BCA 均无字段,只能 UNKNOWN+情景;
- 许可≠开工≠竣工:无施工进度数据;
- 需付费/人工源:BCA 详情、LTSA 产权、MLS GST 标注。
