# Vancouver Residential Sales Archive V1 — 报告

日期 2026-10-23。基线 `c2d91f7`。**CAPTURED_ONLY ≠ 全市成交总量**
(无权威分母)。V3s 零改动。

## 最终答案:实际捕获多少

**14,901 个唯一交易实体**(输入行 6,580 comps + 9,370 arcgis = 15,950,
多源合并+同址同价重复登记归并后)。

### 按年

| 年 | 捕获成交 |
|---|---|
| 2023 | 1,478 |
| 2024 | 1,490 |
| 2025 | 8,911 |
| 2026(至07月刷新) | 3,022 |

2025-26 陡增=arcgis 层就位;2023-24 只有 comps 单源 →
**真实覆盖缺口主要在 2023-24**(MLS/BCA 历史 sold 均未接入)。

### 按产品

| 产品 | n |
|---|---|
| HOUSE | 6,763 |
| CONDO | 5,126 |
| TOWNHOUSE | 1,539 |
| DUPLEX_HALF | 1,192 |
| DUPLEX_WHOLE | 15 |
| MULTIPLEX_WHOLE | 3 |
| MULTIPLEX_UNIT | **2** |
| OTHER_RESIDENTIAL | 154 |
| UNKNOWN | 107 |

MPX 单元成交在全部捕获源仍≈0 — 已是稳定的结构事实。

### 按房龄(成交时点)

| 档 | n |
|---|---|
| AGE_00_03 | 814 |
| AGE_04_10 | 491 |
| AGE_11_20 | 557 |
| AGE_21_30 | 428 |
| AGE_31_50 | 842 |
| AGE_51_PLUS | 1,835 |
| **AGE_UNKNOWN** | **9,868** |
| AGE_TEMPORAL_CONFLICT | 66 |

房龄已知率仅 33.8% — 因为 arcgis 层无 yb,而 tax 集只覆盖
2018+ folio。**房龄是本案最大属性缺口**。

### 证据与类型

| sale_kind | n | confidence | n |
|---|---|---|---|
| RESALE | 3,679 | SOURCE_VERIFIED(双源) | 3,229 |
| FIRST_SALE | 490 | SOURCE_TRACEABLE | 11,672 |
| PRECONSTRUCTION_OR_LAND | 66 | | |
| LAND_OR_VACANT | 17 | | |
| UNKNOWN | 10,649 | | |

### 瀑布

captured 14,901 → price_known 14,901 → age_known 5,033 →
fa_known 5,583 → pid_known 5,049 → comparable_ready 3,400
(价+yb+fa+HOUSE/DUPLEX/MPX,无冲突) ;dup_flagged 3,231(同址同价
近邻重复登记,标记不删)。

## 修复清单(任务六)

- ✅ 地址不再唯一化:`unit-aware` key,同址同日不同价→`#2` 后缀保留
- ✅ BCA 单候选 ≠ PID 确认(identity 分级仍在)
- ✅ 同街 strata 不作 MPX 已核实关联
- ✅ yb_at_sale 时点化:tax2023 快照优先,2026 属性不反推历史
- ✅ 测试纯函数独立(archive_core 无私有依赖)

## 能支撑独立价格分析的社区

见 `transactions_by_community_age_product.csv`:Kitsilano/
Grandview-Woodland/Kensington-Cedar Cottage/Riley Park/Sunset/
Mount Pleasant 记录最多;Downtown 主要为 CONDO(arcgis-only)。
HOUSE 深样本集中在 Dunbar-Southlands/Killarney/Kerrisdale/
Collingwood(南) 等——各格 n 与缺失率已列,薄样本带置信度。
