# 独立区域价格基准 V1 — regional_benchmark.csv

口径: 仅 comps 成交(产品类 HOUSE/DUPLEX/MPX)+ yb≥2018 + 有地上建面 +
有成交价 + asof 一致性通过 + n≥3 → P25/P50/P75 $/sf。
窗口: 2023+ / recent24 / recent12;带 = 地上建面带。
市级借用层 `_CITY` 仅用于影子验证回退,标 `BORROWED_CITY`。

## 规模

- 单元格: **149**(含 _CITY 层 33)
- 产品分布: DUPLEX 66 / HOUSE 44 / MPX 6 格
- 等级: A(n≥8) 48 · B(5-7) 27 · C(3-4) 41 · _CITY 33

## 代表性读数(2023+ 窗口,P50 $/sf)

| 社区 | HOUSE 1600-2000 | DUPLEX ~1700-2200 |
|---|---|---|
| Hastings-Sunrise | $1,731 (n16,A) | — |
| Renfrew-Collingwood | $1,526 (n5,B) | 见 CSV |
| K-CC | $1,653 (n7,B) | — |
| Sunset | $1,613 (n6,B) | — |
| Victoria-Fraserview | $1,540 (n5,B) | — |
| Riley Park | $2,140 (n6,B) | — |
| WPG | $2,361 (n14,A) | — |
| Dunbar | $2,130-2,231 (n5-9) | — |

**价格梯度符合市场直觉**: 西区 $2,000-2,400 > 东区 $1,500-1,750 —
独立证据与 V3s 现有方向一致,不存在"全市场同价"的荒谬基准。

## 已知限制(必须随基准一并阅读)

1. **大带(2800+)_CITY 池混入两个争议锚点**: 2549 KENT($826/sf 低价)
   与 78 MALTA(estate lot)在 2800-3400 带 n=13 中占 2/13;
   3400+ 带几乎全是**西区 resale**(Dunbar/Shaughnessy/WPG,Kerrisdale)。
   → 东面带 ≥2800 的影子读数属于**借入+混合产品**口径,仅作上界参考,
   **不作裁定依据**;裁定需获取 MLS 一手新建成交源。
2. MPX 仅 6 格 — fa 缺失 97% 是根因,不是市场无多户成交。
3. recent12 窗口格少: 成交捕获止于 2026-07-08 数据源截点 +
   arcgis 源 yb 缺失,freshness 已逐格标注。
4. gst_basis 混合 new_first_sale/resale — 逐格未再细分,
   记录级 `sales_evidence.csv` 保留口径,必要时可切分。

## 复现

`.venv/bin/python scripts/newbuild_evidence_v1.py` → `regional_benchmark.csv`
