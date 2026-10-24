# HPI 历史修订敏感性

**重要口径**:本 HPI 序列由 45 份月度官方报告各自发布的当月指数
组成 —— 是"当时发布值"(as-published)序列,**不是同一修订版本的
完整历史序列**。GVR/CREA 的 MLS HPI 模型会周期修订历史值,
因此不同月报对同一历史月份的隐含指数可能不一致。

## 修订漂移实测(hpi_revision_crosscheck.csv,1431 对)

- 中位 |drift| = 0.04%
- P90 |drift| = 0.88%
- 最大 |drift| = 5.99%

## 高敏单元格(|drift|>2%,16 个)

| product | area | 受影响月 | 观测次数 | 最大漂移% |
|---|---|---|---|---|
| Townhouse | Vancouver West | 2023-02 | 5 | 5.99 |
| Townhouse | Vancouver West | 2023-03 | 5 | 5.63 |
| Single Family Detached | Vancouver West | 2023-02 | 5 | 4.77 |
| Townhouse | Vancouver West | 2023-04 | 5 | 4.46 |
| Single Family Detached | Vancouver West | 2023-01 | 5 | 3.63 |
| Townhouse | Vancouver West | 2024-02 | 4 | 3.48 |
| Townhouse | Greater Vancouver | 2023-02 | 5 | 3.18 |
| Townhouse | Vancouver East | 2025-09 | 4 | 3.16 |
| Apartment | Vancouver West | 2024-01 | 3 | 3.01 |
| Townhouse | Vancouver West | 2024-03 | 4 | 2.90 |
| Townhouse | Greater Vancouver | 2023-03 | 5 | 2.36 |
| Townhouse | Vancouver East | 2023-03 | 5 | 2.24 |
| Townhouse | Vancouver West | 2023-01 | 5 | 2.21 |
| Townhouse | Greater Vancouver | 2023-04 | 5 | 2.19 |
| Townhouse | Greater Vancouver | 2023-01 | 5 | 2.17 |
| Single Family Detached | Vancouver West | 2024-01 | 3 | 2.12 |

## 含义与处理

- 绝大多数单元格跨版本一致性极好(<1%),HPI 作为时间归一化
  工具可信;
- 少数月份(主要为 2023-02 前后 Vancouver West
  Detached/Townhouse)存在 ~5% 版本修订:早期报告的隐含值
  与后期直接发布值分歧。归一化该月成交时,因子对版本选择
  敏感 ±5%,已在逐笔与聚合输出标 `REVISION_SENSITIVE`。
- 若要完全统一版本,需向 GVR/CREA 购买同一修订版的官方
  完整 HPI 历史 —— 本轮公开渠道不可得,已如实记录。
