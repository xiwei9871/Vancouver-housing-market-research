# 数据缺口与获取方案 — data_gaps_and_acquisition.md

`missing_evidence_queue.csv` 为机读版;此处给人读版+获取路径。

## 缺口 1 — HOUSE year_built 缺失 2,335 条(最大单点)

- **现状**: `bca-arcgis` 源记录无 year_built/floor_above_sf;其中部分是
  2018+ 新房但无法判别 → 被当作 `ATTRIBUTE_MISSING` 排除。
- **已恢复**: tax-folio 地址回连确认 16 条 yb≥2018(`yb_recovered_records.csv`)。
- **残留**: ~2,300 条无法确认新旧 → 需 BC Assessment 逐 folio
  Assessment Search 或 LTSA parcel 回连;批量化需授权接口。
- **商业路径**: Landcor/BCA Data Advice 订阅(~$xxx/mo)、REW/MLS sold feed。

## 缺口 2 — MPX 地上建面缺失 934/959(97%)

- **现状**: 多户/strata 记录多数只有总价,无单元建面 → $/sf 不可算。
- **修复路径**: 许可附图(gross floor area by unit)、strata plan EPS
  单元表、开发商户型表;逐项目人工/半自动采集。
- **预计成本**: 341 个 MPX 项目 × ~15min ≈ 85h 人工;或接 GVR 单元数据。

## 缺口 3 — STRATA_UNIT_UNKNOWN 635 条

- 无 product_class 可判别(门牌前缀单元,来源未标户型)。
- 修复: strata plan → issued permit 回连(PID/address/folio),
  或 tax folio `legal_type=STRATA` + zoning 联合判定。

## 缺口 4 — 新建一手成交渠道(first sale)

- 官方许可 9,944 项目 vs 捕获首销 ~700 条 → MLS 成交 feed 未接入。
- **合法路径**: GVR(REBGV)MLS 订阅 / 经纪合作导出 / Zillow 研究许可。
  **不可用**: 绕过反爬抓取(合规禁令)。

## 缺口 5 — 完工/入住状态

- issued-permit 只有签发态;occupancy/completion 需另源:
  CoV permit status 子表、folio 首现年、utility connection。
  当前用 folio 出现+yb 作弱完工代理,已标 `COMPLETED?`。

## 缺口 6 — GST 口径

- `gst_basis` 记录 resold/new_first_sale/unknown_no_yearbuilt;
  unknown 主体是 arcgis 记录 — 与缺口 1 同源。

## 商业数据源建议(按性价比)

1. **GVR/MLS sold export**(授权经纪)— 一次解决缺口1/4/大部分3
2. **BCA Data Advice 批量 folio** — 补 yb/fa/unit mix
3. **LTSA historic sales** — 法定成交背书,成本/条计费
4. **CoV occupancy permits API**(若开放)— 补完工状态

## 不构缺口的已核销项

- arcgis 高层 strata 5,613 条: 非目标产品,排除正确
- >$6M 探针 146 条: 超 lot 窗,边缘有效
- permit "other" 桶 0 行: LDH 滤子已覆盖
