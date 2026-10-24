# 成交数据字段需求 — sales_data_requirements.md

日期 2026-10-23。目标:2023-01-01 以来温哥华市 HOUSE / DUPLEX /
MULTIPLEX(含低密度 strata 单元)真实成交记录。

## 必备字段(逐源)

| 字段 | GVR/MLS | BCA Data Advice | LTSA | 用途 |
|---|---|---|---|---|
| MLS ID / 挂牌号 | ✔ | — | — | 与 watchlist 回连 |
| 成交价 + 成交日期 | ✔ | ✔(sale) | ✔(transfer) | 基准价 |
| civic address | ✔ | ✔ | ✔ | 地址键 |
| PID | ✔* | ✔ | ✔ | parcel 关联 |
| strata plan(EPS/BCS…) | ✔* | ✔ | ✔ | **MPX 单元链核心** |
| strata lot/unit no | ✔* | ✔ | ✔ | 半户/单元身份 |
| year_built | ✔ | ✔(YearBuilt) | — | 新建判定 |
| above-grade floor area | ✔ | ✔(AboveGradeArea) | — | $/sf 基准 |
| basement area | ✔ | ✔(BasementArea) | — | 总价口径 |
| lot area | ✔ | ✔ | — | 地值/定制房 |
| property type / form | ✔ | ✔(LegalType) | ✔ | 产品分类 |
| tenure(freehold/strata/leasehold) | ✔ | ✔ | ✔ | 半户口径 |
| first-sale vs resale 标识 | 派生(seller=builder/开发实体?) | 派生(首笔folio成交) | ✔(transfer历史) | 首销分离 |
| GST included | ✔(可派生) | — | — | 口径 |

*MLS 需确认 PID/strata plan 是否在可导出字段内(部分版面对外不输出)。

## 覆盖要求

- 时间:2023-01-01 起,持续增量
- 地理:City of Vancouver 全域(23 社区)
- 产品:HOUSE detached / DUPLEX half+whole / TOWNHOUSE /
  APARTMENT(排除参考)/ **R1-1 multiplex 单元(EPS strata)**
- 量级预估:全市住宅年成交 ~8–10k,目标子集 ~2k/年

## 许可用途澄清清单(询价时必须书面确认)

1. 内部投资研究用途是否允许?
2. 原始数据本地存储期限/加密要求?
3. 自动化管线批量分析是否允许?
4. **衍生统计(区域中位数、计数、审计 flag)能否进私有 GitHub?**
5. 记录级衍生(地址+价格审计行)在私有库是否允许?
6. 公开仓库只允许聚合统计的条款能否接受?
7. 数据更新频率与增量交付方式(API/SFTP/邮件)?
8. 违约/终止条款,历史数据处置方式?

## 829 yb_no_source 专项(BCA)

字段最小集:`PID / Folio / YearBuilt / AboveGradeArea /
BasementArea / StrataPlan / LegalType / AssessmentYear`
范围:829 个 folio(清单在 audit_v1_2_1 本地产物)。
