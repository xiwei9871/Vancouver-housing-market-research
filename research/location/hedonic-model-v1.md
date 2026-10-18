# Hedonic Model V1

Status: **INSUFFICIENT_SAMPLE**  
Sale rows used: **10**  
Minimum required: **20**

The model is diagnostic only and is not connected to production scoring.

## Diagnostics

```json
{
  "minimum_rows": 20,
  "reason": "fewer_than_minimum_sales"
}
```

## Coefficients

```json
{}
```

The current input contains `10` normalized comparable rows. If the
status is `INSUFFICIENT_SAMPLE`, a coefficient is intentionally not produced.
