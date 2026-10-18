# Initial Location Premium Research V1.4

This is a research-only directional estimate. No value is written into production scoring, RALV, or MOS.

## Method

A ridge-controlled log-price model controls lot area, frontage, depth, living area, year built, BCA improvement, beds, baths, sale month, and zoning group. Neighbourhood output is a combined residual for the fixed V1.4 standard property and is centred on the sample-weighted City of Vancouver prediction for that profile. Road output is conditional on neighbourhood and uses quiet residential class E as the reference.

- Eligible rows: `5048`
- Area model rows: `5036`
- Road model rows: `5036`
- Neutral standard value: `$2,061,000`
- Automatic application: `DISABLED`

## V1.4 standard property

`{"baths": 2, "bca_improvement": 219000.0, "bca_improvement_reference": "ELIGIBLE_SAMPLE_MEDIAN_CONTROL", "beds": 3, "condition": "NEUTRAL", "condition_definition": "Normally habitable, ordinary maintenance, no luxury renovation premium, no major deferred maintenance.", "depth_ft": 122, "frontage_ft": 33, "living_area_sqft": 1700, "lot_area_sqft": 4026, "lot_shape": "REGULAR_RECTANGULAR", "park_facing": false, "profile_name": "VANCOUVER_STANDARD_PROPERTY_V2", "property_type": "DETACHED_FREEHOLD", "road_class": "E", "sale_date": "2026-07-01", "view": "NONE", "waterfront": false, "year_built": 1960, "year_built_reference": 1960, "year_built_sensitivity": [1940, 1960, 1980], "zoning": "R1-1", "zoning_group": "R"}`

The primary residual uses the 1960 midpoint. 1940 and 1980 are reported as age sensitivity only; they are not averaged into the primary value.

## Area residuals

| Neighbourhood | Standard property value | Neutral standard value | Residual | Samples | Confidence |
|---|---:|---:|---:|---:|---|
| West Point Grey | $2,703,000 | $2,061,000 | $642,000 | 309 | MEDIUM |
| Kitsilano | $2,586,000 | $2,061,000 | $525,000 | 249 | MEDIUM |
| South Cambie | $2,572,000 | $2,061,000 | $511,000 | 73 | MEDIUM |
| Shaughnessy | $2,551,000 | $2,061,000 | $490,000 | 167 | MEDIUM |
| Dunbar-Southlands | $2,442,000 | $2,061,000 | $381,000 | 511 | MEDIUM |
| Oakridge | $2,394,000 | $2,061,000 | $333,000 | 119 | MEDIUM |
| Arbutus Ridge | $2,300,000 | $2,061,000 | $239,000 | 231 | MEDIUM |
| Kerrisdale | $2,273,000 | $2,061,000 | $212,000 | 292 | MEDIUM |
| Fairview | $2,196,000 | $2,061,000 | $135,000 | 6 | UNKNOWN |
| Riley Park | $2,158,000 | $2,061,000 | $97,000 | 296 | MEDIUM |
| Marpole | $2,111,000 | $2,061,000 | $50,000 | 199 | MEDIUM |
| Strathcona | $1,914,000 | $2,061,000 | $-147,000 | 46 | LOW |
| Mount Pleasant | $1,887,000 | $2,061,000 | $-174,000 | 86 | MEDIUM |
| Grandview-Woodland | $1,860,000 | $2,061,000 | $-201,000 | 232 | MEDIUM |
| Victoria-Fraserview | $1,738,000 | $2,061,000 | $-323,000 | 299 | MEDIUM |
| Killarney | $1,737,000 | $2,061,000 | $-324,000 | 166 | MEDIUM |
| Kensington-Cedar Cottage | $1,720,000 | $2,061,000 | $-341,000 | 558 | MEDIUM |
| Sunset | $1,712,000 | $2,061,000 | $-349,000 | 299 | MEDIUM |
| Renfrew-Collingwood | $1,708,000 | $2,061,000 | $-353,000 | 418 | MEDIUM |
| Hastings-Sunrise | $1,689,000 | $2,061,000 | $-372,000 | 480 | MEDIUM |

## Road adjustments

| Road class | Initial CAD adjustment vs E | Samples | Confidence |
|---|---:|---:|---|
| B | $-147,000 | 391 | MEDIUM |
| D | $-39,000 | 153 | MEDIUM |
| E | $0 | 4492 | MEDIUM |

## Not separately identified

School, park, view, beach, and transit are retained as zero/unknown until controlled transaction evidence can separate them from neighbourhood and road effects.

## Safety rule

The combined neighbourhood residual must not be added to a separately estimated school premium unless the latter is independently identified; otherwise location would be double-counted.
