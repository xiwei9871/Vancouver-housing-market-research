# 影子交叉验证 — shadow_impact.csv(V3s vs 独立证据基准)

口径: 每 ACTIVE 方案 `V3s估值 / 独立基准P50 / Δ / 原ROI / 影子ROI / borrowed / grade`。
独立基准 = comps 成交重建池(与 V3s 解耦,未用 V3s 估值/影子价训练)。
**零回写** — 仅供人工批准。

## 总体覆盖(445 方案行)

| grade | 行数 | 含义 |
|---|---|---|
| A | 356 | 社区/市级同带 n≥8 |
| B | 78 | n 5-7 |
| C | 10 | n 3-4 |
| NONE | 1 | 无法估值 |

borrowed: 自有社区格 167 行 / BORROWED_CITY 278 行(社区无格,借市级同带,已标)。

## 对 27 个锚点支配待批方案

| 结果 | 数 | 说明 |
|---|---|---|
| 有独立读数 | 22 | 全部 BORROWED_CITY(2800/3400 带,见下警示) |
| NOT_ESTIMABLE | 5 | 1205 E49/5769 Knight/3153 Parker/3090 Kitchener/5454 Melbourne |

警示: 22 个"有读数"方案借用的市级大带池**本身含 2549 KENT / 78 MALTA
两个争议锚点 + 西区 resale 主导**——方向性参考(V3s 低估多数),
不构成价格裁定。真正裁定需干净的一手新建成交源。

方向性结论(供参考): 市级 2800-3400 带 P50≈$1,895/sf →
东面带 2800-3300sf 新屋理论价 $5.4-6.3M;多数 V3s 值
($3.1-4.6M,KENT 锚点组 $2.4-2.7M)系统性偏低 → 印证"单锚支配"诊断,
但幅度不可直接采信(借入池偏西区品质)。

## DUPLEX/MPX 行

- DUPLEX 行: 单元级 $/sf × 2 户 = 整栋收入口径(见 product_classification);
  独立池较厚(389 条),读数可信度高于 HOUSE 大带。
- MPX/TRIPLEX/FOURPLEX 行: 基准池 20 条几乎全部 NOT_ESTIMABLE ——
  MPX 影子验证本轮无独立证据支撑,保留 UNKNOWN。

## 零回写确认

- 未改 `valuation-v3s-shadow-2026-10-22.json` 任一字节
- 未改 `house_anchor_patch_v3s.json`
- 影子 ROI 使用与 V3s 同一 dev_cost 函数,仅替换售价输入
