# Capture Gap Report — 覆盖缺口

## 各源覆盖现实(source_inventory)

| 源 | 年份 | 字段缺口 | 性质 |
|---|---|---|---|
| comps_v2 | 2023–2026 | 部分 yb/fa | MLS 派生,持续 |
| bca_arcgis | **仅 2025–2026** | 无 yb/fa/strata_plan | 公开层,逐事件行但同址同日同价存在重复登记 |
| bca_detail | 当前快照 | sales_history 未捕获 | 详页,已冻结批量 |
| tax2023/26 | yb2018+ folio | 非全量 | 官方开放 |

## 已确认缺口

1. **2023–24 单源**:arcgis 层不含 2025 前年成交 → comps 之外的真实成交数未知,且 comps 自身为筛选池非普查。
2. **yb 覆盖率 33.8%**:arcgis-only 记录 8,911+ 中绝大多数无 yb → AGE_UNKNOWN 9,868。
3. **fa 覆盖率 37.5%**:arcgis 无面积,comps 部分缺。
4. **MPX 单元**:全源 2 条 MPX_UNIT + 3 WHOLE,均为 PARTIAL。
5. **strata plan**:仅经 pid→folio 取到;arcgis ROLL/OID 未全连 folio。
6. **同址同日同价重复登记** 3,231 条已 flag:arcgis 层按 folio/事件重复行,已归并标记未删。
7. **Burnaby/外区 105 行**保留在档案(出 Vancouver 范围,不删)。

## CAPTURED_ONLY 声明

- 本档案**不是**温哥华 2023+ 全部成交 — 无权威分母。
- 2023 年全市住宅成交估计 ~9–10k(REBGV 公布口径),当前捕获
  1,478 → **明显欠覆盖**;2025 年 8,911 条接近量级但因 arcgis
  重复登记与单源混合,真实唯一成交仍可能少于此数。
- 不得用本档案数推断市场份额或覆盖率。
