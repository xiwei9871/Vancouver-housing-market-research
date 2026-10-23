# 成交来源可行性 — sales_source_feasibility.md

日期 2026-10-23。原则:合法渠道试采;受限源只出采购方案,不绕过。

## 结论速览

| 源 | 状态 | 能解什么 |
|---|---|---|
| REALTOR.ca api2 | ✅ 已试(仅挂牌) | 无 sold;不补成交 |
| **GVR/MLS SOLD** | 🔒 需授权 | **首选**:MPX 单元成交+HOUSE 首销+829 yb 旁证 |
| BCA arcgis viewer | ✅ 在用 | 已贡献;sale 有、yb/fa 需逐 folio |
| BCA Data Advice | 🔒 付费批量 | 829 yb_no_source + MPX 单元面积 |
| LTSA | 🔒 按条计费 | 法定成交背书,抽样核验 |
| CoV opendata | ❌ 无成交数据集 | 已穷尽(无 sale/occupancy) |
| 开发商公开销售页 | ⚠️ 碎片 | 341 MPX 项目逐案人工 |

## 逐源说明

### REALTOR.ca api2(公开挂牌 API)
`scripts/test_realtor_api2.py` 已证可达(频控存在)。
返回**当前在售**字段:asking/address/mls/status — **无 sold price/sold
date 端点**。对已捕获市场只有"要价"维度,不解决成交。

### GVR / REBGV MLS SOLD(会员数据)
唯一能覆盖"MPX 单元已售"缺失的渠道。需持牌经纪或 GVR 数据协议
(通常订阅+用途限制)。**强烈建议优先采购**:解决 MPX 单元、HOUSE 首销、
半户口径、829 yb 旁证四类缺口。

### BC Assessment
- arcgis viewer(现用):sale_date/sale_price/desc/pid/GIS parcel — 已有
- 逐 folio assessment:yb/fa/unit mix — 有频控,适合点查不适合批量
- **Data Advice 批量**:folio 级全字段,付费,可解 829 yb_no_source

### LTSA
title/strata plan 法定记录,按条计费。建议用作**抽样核验**而非批量源:
对高价/争议方案逐案 title pull。

### CoV 官方
目录已穷尽:无成交数据、无 occupancy 状态集。供给端(许可+税表)已用满。

### 开发商公开销售
341 个 MPX 项目逐一收集 marketing/sold 披露 — 人力密集,适合作 MPX
个案核验而非批量管线。

## 推荐路径

1. **GVR MLS sold export(授权)** — 一批解决 MPX 单元+首销
2. **BCA Data Advice** — 补 yb/fa 字段缺口
3. **LTSA 抽样** — 争议/高价方案核验
4. **人工 MPX 专案表** — 341 项目逐个查开发商披露(后备)
