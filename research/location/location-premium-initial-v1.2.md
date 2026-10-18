# Initial Location Premium Research V1.2

This is a research-only directional estimate. No value is written into production scoring, RALV, or MOS.

## Method

A ridge-controlled log-price model controls lot area, frontage, depth, living area, year built, BCA improvement, beds, baths, sale month, and zoning group. Neighbourhood output is a combined residual and is centred on the sample-weighted City of Vancouver mean. Road output is conditional on neighbourhood and uses quiet residential class E as the reference.

- Eligible rows: `2932`
- Area model rows: `2932`
- Road model rows: `2921`
- Automatic application: `DISABLED`

## Area residuals

| Neighbourhood | Initial CAD residual | Samples | Confidence |
|---|---:|---:|---|
| West Point Grey | $534,000 | 115 | MEDIUM |
| South Cambie | $416,000 | 36 | LOW |
| Kitsilano | $415,000 | 148 | MEDIUM |
| Oakridge | $409,000 | 48 | LOW |
| Dunbar-Southlands | $392,000 | 221 | MEDIUM |
| Kerrisdale | $304,000 | 101 | MEDIUM |
| Shaughnessy | $285,000 | 11 | LOW |
| Arbutus Ridge | $179,000 | 80 | MEDIUM |
| Fairview | $178,000 | 5 | UNKNOWN |
| Riley Park | $164,000 | 194 | MEDIUM |
| Marpole | $154,000 | 114 | MEDIUM |
| Knight | $131,000 | 1 | UNKNOWN |
| Renfrew | $131,000 | 1 | UNKNOWN |
| Main | $109,000 | 1 | UNKNOWN |
| Collingwood | $23,000 | 3 | UNKNOWN |
| Mount Pleasant | $-2,000 | 23 | LOW |
| Grandview-Woodland | $-77,000 | 160 | MEDIUM |
| Killarney | $-164,000 | 127 | MEDIUM |
| Strathcona | $-168,000 | 20 | LOW |
| Sunset | $-188,000 | 216 | MEDIUM |
| Kensington-Cedar Cottage | $-191,000 | 417 | MEDIUM |
| Victoria-Fraserview | $-195,000 | 216 | MEDIUM |
| Renfrew-Collingwood | $-206,000 | 310 | MEDIUM |
| Hastings-Sunrise | $-220,000 | 364 | MEDIUM |

## Road adjustments

| Road class | Initial CAD adjustment vs E | Samples | Confidence |
|---|---:|---:|---|
| B | $-164,000 | 239 | MEDIUM |
| D | $-49,000 | 94 | MEDIUM |
| E | $0 | 2588 | MEDIUM |

## Not separately identified

School, park, view, beach, and transit are retained as zero/unknown until controlled transaction evidence can separate them from neighbourhood and road effects.

## Safety rule

The combined neighbourhood residual must not be added to a separately estimated school premium unless the latter is independently identified; otherwise location would be double-counted.
