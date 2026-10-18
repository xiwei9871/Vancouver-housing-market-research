# Initial Location Premium Research V1.3

This is a research-only directional estimate. No value is written into production scoring, RALV, or MOS.

## Method

A ridge-controlled log-price model controls lot area, frontage, depth, living area, year built, BCA improvement, beds, baths, sale month, and zoning group. Neighbourhood output is a combined residual and is centred on the sample-weighted City of Vancouver mean. Road output is conditional on neighbourhood and uses quiet residential class E as the reference.

- Eligible rows: `5048`
- Area model rows: `5048`
- Road model rows: `5036`
- Automatic application: `DISABLED`

## Area residuals

| Neighbourhood | Initial CAD residual | Samples | Confidence |
|---|---:|---:|---|
| West Point Grey | $658,000 | 309 | MEDIUM |
| South Cambie | $546,000 | 73 | MEDIUM |
| Kitsilano | $543,000 | 249 | MEDIUM |
| Shaughnessy | $526,000 | 167 | MEDIUM |
| Dunbar-Southlands | $397,000 | 511 | MEDIUM |
| Oakridge | $344,000 | 119 | MEDIUM |
| Arbutus Ridge | $237,000 | 231 | MEDIUM |
| Kerrisdale | $212,000 | 292 | MEDIUM |
| Knight | $145,000 | 1 | UNKNOWN |
| Renfrew | $144,000 | 1 | UNKNOWN |
| Fairview | $142,000 | 6 | UNKNOWN |
| Main | $123,000 | 1 | UNKNOWN |
| Riley Park | $109,000 | 296 | MEDIUM |
| Marpole | $63,000 | 199 | MEDIUM |
| Collingwood | $-21,000 | 3 | UNKNOWN |
| Strathcona | $-142,000 | 46 | LOW |
| Mount Pleasant | $-178,000 | 86 | MEDIUM |
| Grandview-Woodland | $-197,000 | 232 | MEDIUM |
| Killarney | $-338,000 | 167 | MEDIUM |
| Victoria-Fraserview | $-341,000 | 300 | MEDIUM |
| Kensington-Cedar Cottage | $-358,000 | 558 | MEDIUM |
| Sunset | $-360,000 | 301 | MEDIUM |
| Renfrew-Collingwood | $-365,000 | 418 | MEDIUM |
| Hastings-Sunrise | $-384,000 | 482 | MEDIUM |

## Road adjustments

| Road class | Initial CAD adjustment vs E | Samples | Confidence |
|---|---:|---:|---|
| B | $-154,000 | 391 | MEDIUM |
| D | $-41,000 | 153 | MEDIUM |
| E | $0 | 4492 | MEDIUM |

## Not separately identified

School, park, view, beach, and transit are retained as zero/unknown until controlled transaction evidence can separate them from neighbourhood and road effects.

## Safety rule

The combined neighbourhood residual must not be added to a separately estimated school premium unless the latter is independently identified; otherwise location would be double-counted.
