# Tax field schema audit

## 逐年行数 / 口径字段 / 缺失

| 快照年 | rows | 无pid | 缺impv | 缺yb | assessment_year 值 | report_year 值 |
|---|---|---|---|---|---|---|
| 2023 | 216524 | 493 | 1 | 3626 | ['2023'] | ['2023'] |
| 2024 | 224743 | 551 | 4960 | 8893 | ['2024'] | ['2024'] |
| 2025 | 226360 | 545 | 4410 | 10758 | ['2025'] | ['2025'] |
| 2026 | 228935 | 539 | 5288 | 13356 | ['2026'] | ['2026'] |

## 关键字段逐年存在性

| field | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| pid | Y | Y | Y | Y |
| folio | Y | Y | Y | Y |
| land_coordinate | Y | Y | Y | Y |
| plan | Y | Y | Y | Y |
| lot | Y | Y | Y | Y |
| legal_type | Y | Y | Y | Y |
| current_improvement_value | Y | Y | Y | Y |
| previous_improvement_value | Y | Y | Y | Y |
| current_land_value | Y | Y | Y | Y |
| previous_land_value | Y | Y | Y | Y |
| year_built | Y | Y | Y | Y |
| big_improvement_year | Y | Y | Y | Y |
| tax_assessment_year | Y | Y | Y | Y |
| report_year | Y | Y | Y | Y |
| zoning_district | Y | Y | Y | Y |
| from_civic_number | Y | Y | Y | Y |
| street_name | Y | Y | Y | Y |

all_fields=['big_improvement_year', 'block', 'current_improvement_value', 'current_land_value', 'district_lot', 'folio', 'from_civic_number', 'land_coordinate', 'legal_type', 'lot', 'narrative_legal_line1', 'narrative_legal_line2', 'narrative_legal_line3', 'narrative_legal_line4', 'narrative_legal_line5', 'neighbourhood_code', 'note', 'pid', 'plan', 'previous_improvement_value', 'previous_land_value', 'property_postal_code', 'report_year', 'street_name', 'tax_assessment_year', 'tax_levy', 'to_civic_number', 'year_built', 'zoning_classification', 'zoning_district']
distinct_pids=229017

## prev↔prior-current 跨年口径验证
{'match': 655495, 'diff': 378, 'na': 215532}
- match: 当年 previous_improvement_value 与上年 current 完全一致 → 跨年可比;
- diff: 不一致(改判/补录),不计跨年差;
- na: 无上年快照可对比(2023 全年+新增pid)。
value 基准日=assessment_year;快照时点=文件对应 report_year。