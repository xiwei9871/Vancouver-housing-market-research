# Valuation Methodology V1.4

## 分离原则
- 土地估值 = 今日市场价值(土地型旧房 comps,land_share≥0.95 主池 /
  90-95% 辅助池,独立输出)。
- 新房估值 = "今天建成"的价值(matched $/sf × 方案 net_saleable)。
- 未来情景 = future_market_factor 独立乘数;基准 0% 不假设涨跌;
  已对 2026-09 归一化的价格不再二次扣历史跌幅。
- GST 单扣一次:EXCL(售价为未税,5% 记成本)或 INCL(含在售价,
  净收入=售/1.05);土地、地下室、LWH 不重复计价(方案
  net_saleable 单一计价面积)。

## 匹配与回退
土地:MICRO_500M→COMM_LOTBAND→COMM_ADJ_BAND→COMM_ANY→
NEARBY_COMM_1.5KM_ADJ(社区 P50 比值调整)→SIDE_FALLBACK(低置信)。
新房:500M→COMM→SIDE;THIN 明确标注。
comp_ids 保留前 12 个可比 tx,可追溯。

## 成本(V3s frozen)
hard=建面×(350/400/450+40permit);fin=offer×5%×1年+
hard×7.725%×1年(统一1年开发融资窗口;出售时间与ROI分离,
开发前长期持有成本独立记录不叠加);selling=售价×4%;GST 5%;PTT=BC阶梯;
total=offer+ptt+hard+fin+selling+gst;roi=(revenue-total)/total。

## 置信
HIGH(n≥8 micro/comm)→MEDIUM(3≤n<8)→LOW(THIN/SIDE/缺字段)→BLOCKED。
