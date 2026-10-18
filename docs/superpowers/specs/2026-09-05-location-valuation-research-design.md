# Vancouver Location Valuation Research V1

## Goal

建立一套与生产筛选评分分离的、可追溯的 Vancouver location / school / physical-lot 定价研究层，用真实成交、官方 GIS 和已有本地 enrichment 估计区域与微观位置价值，并明确区分证据、先验和未知数据。

## Scope

本轮只生成 `research/location/` 研究产物，不修改 `research/screening.py`、当前 Current Use / Redevelopment / Dual Value 排名或 RALV/MOS 生产逻辑。输入优先使用本地缓存；官方数据不足时不伪造成交 premium。

## Architecture

`location_valuation.py` 提供纯函数和一个编排入口：读取当前 166 套 enrichment、历史成交/比较样本、学校缓存和官方 location 字段，标准化观察记录，生成 matched-pair 候选、school/area/micro-location estimates、稀疏样本 hedonic 诊断和 benchmark 分解。所有估计携带 `value`、`method`、`sample_size` 和 `confidence`。

Model A 计算 `market_land_basis`、`normalized_physical_lot_basis`、每平方英尺指标和可解释的 `total_location_value`。Model B 不在本轮重算，只在报告中确认仍与 Model A 分离，并使用 local redevelopment exit value 作为 RALV 输入。

## Evidence policy

- 官方 school catchment、BCA、road、zoning 和本地官方 GIS 快照优先。
- 真实成交才可产生成交支持的 premium；配置 prior 必须标记 `PRIOR_ONLY`。
- matched pairs 少于配置门槛时不输出确定性结论。
- hedonic 只在样本达到门槛、字段有足够变化且没有明显共线性时报告估计；否则输出 `INSUFFICIENT_SAMPLE` 或 `LOW_CONFIDENCE`。
- 未证实的 premium 在自动排名中按 0 或不应用，不会为了完整性填入假数字。
- school 只进入 `total_location_value` 一次；area residual 在控制 school、lot、house、road、view、park 和时间后计算。

## Outputs

生成以下版本化文件：

```text
research/location/location-methodology-v1.md
research/location/location-feature-schema-v1.json
research/location/school-premium-v1.csv
research/location/area-baseline-premium-v1.csv
research/location/micro-location-premium-v1.csv
research/location/matched-pairs-v1.csv
research/location/hedonic-model-v1.md
research/location/benchmark-validation-v1.md
research/location/location-model-config-v1.yaml
research/location/RECOMMENDATION.md
```

## Benchmark acceptance

2876 W18、3974 W10、333 E13、486 E King Edward、4561 Nanaimo 必须输出 raw asking、现房价值、school、area、micro adjustments、market land basis、normalized lot basis、normalized lot $/sqft 和各项 confidence。验收重点是解释 Westside premium、低 basis、主路 discount 和 redevelopment value 的分离，不是写死某个地址名次。

## Testing

测试覆盖：输入标准化、成交/要价区别、school 不重复计价、unknown/low-confidence 不伪造 premium、matched-pair 匹配、area residual 计算、物理土地标准化、BCA improvement prior、共线性/稀疏样本诊断、benchmark 输出和十份 artifact 的字段/文件完整性。
