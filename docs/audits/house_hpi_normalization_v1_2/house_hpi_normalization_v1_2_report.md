# HOUSE HPI Normalization V1.2 — 报告

日期:2026-10-23。输入池 = V1.1 的 5,386 笔 HOUSE 冻结成交
(档案截点 2026-07-08),归一化目标 = 2026-09(最新完整 GVR 月报)
与 2026-07(档案截点复现)。V3s 零修改。

## 1. 官方成交量核验(B 层)

| 年 | GVR 东+西 Detached | Archive HOUSE | 差 | % |
|---|---|---|---|---|
| 2023 | 1,719 (901+818) | 1,620 | -99 | -5.8% |
| 2024 | 1,675 (882+793) | 1,647 | -28 | -1.7% |
| 2025 | 1,410 (765+645) | 1,495 | +85 | +6.0% |

口径差异(BCA 产权登记 vs MLS 合同/收录范围/产品归类)已写明;
三份年终包交叉锚定(2024/2025 年值由两份报告互证)。
**全量 HOUSE 规模可信;新房识别缺口是独立问题**(V2.x 结论)。

## 2. HPI 序列(P0)

- 45 个月(2023-01→2026-09)× 3 产品 × 3 区域:
  **396 个 DIRECT 单元格 + 9 个 DERIVED(仅 2025-01,
  该月官方重发包缺 Benchmark 表)+ 0 INDEX_MISSING**。
- 2025-01 反推候选一致性:近端 3 个来源(2025-02 1M /
  2025-04 3M / 2025-07 6M)指数差 ≤0.3 index 点;12M 远端
  来源差 ~3 点 —— 即 HPI 修订存在的实据。
- 修订审计(`hpi_revision_crosscheck.csv`,1,431 对):
  中位 |drift| 0.04%,P90 0.88%;少数单元格 >2%
  (最大 5.99%,集中在 Vancouver West 2023-02 前后,
  Townhouse/Detached)——该月前后指数在后期报告中被上修。
  受影响成交在逐笔与聚合层标 `REVISION_SENSITIVE`(本池 157 笔)。
- 用户提供的三年系数全部核对一致:Detached 东 -13.9% / 西 -16.4%;
  Townhouse 东 -13.1% / 西 -8.4%;Apartment 东 -11.1% / 西 -9.8%
  (2026-09 报告 3-Year Change 列)。

## 3. 归一化方法(P1/P2)

- `price_2026_09 = price × idx(2026-09)/idx(sale_month)`,逐笔逐月;
  同月目标 factor=1;下跌市场历史价 factor<1。
- 东/西按逐笔 lng vs Main St 子午线(-123.1005)划分;
  ±0.0025 lng 内标 BORDER_TX(本池 119 笔)。
- 产品映射:HOUSE→Detached(官方);CONDO→Apartment;
  TOWNHOUSE→Townhouse;DUPLEX_*/MULTIPLEX_UNIT→代理三情景
  (中性 Townhouse/偏弱 Detached/偏强 Apartment),
  详见 `hpi_product_geography_mapping.csv`。
- 缺失 HPI 一律不进归一化中位(本轮实际 0 缺失);
  百分位全部由调整后逐笔价重算。
- GST 情景独立:`price_net_gst5_2026_09` 单列,源价不改写。
- 禁止二次归一化:输入含 `price_2026_*` 字段即报错退出。

## 4. 主结果(0-5 年新 HOUSE,main pool)

| 范围 | n | 名义 P50 | 2026-09 P50 | 归一化 $/sf | 平均 factor |
|---|---|---|---|---|---|
| Vancouver East | 101 | $2,699,000 | **$2,474,488** | $1410 | 0.8998 |
| Vancouver West | 77 | $4,200,000 | **$3,802,106** | $1837 | 0.8839 |
| 全市 | 178 | $3,249,875 | **$2,863,550** | $1650 | 0.8929 |

分年成交月系数(Detached,2026-09 目标,年内各月均值):

| 年 | 东区 | 西区 |
|---|---|---|
| 2023 | 0.8987 | 0.8806 |
| 2024 | 0.8752 | 0.8556 |
| 2025 | 0.9054 | 0.8856 |
| 2026 | 0.9799 | 0.9826 |

(逐年逐月精确值见 `historical_price_normalization_factors.csv`;
归一化必须用成交月系数,不是年均值。)

## 5. V1.1 名义价的时间构成偏差(P3)

V1.1 名义 2023-24 vs 2025-26 的 0-5 新房 P50 比值 0.855
(14.5% 名义下跌)。归一化后同两期比值见
`normalization_convergence_check.csv`:

- 全市 0-5:名义 0.855 → 归一化 **0.909** —— V1.1 把早期高价
  计入名义中位,**系统性高估约 4-6%**(方向 = 早期权重溢价);
- 西区 0-5:名义 0.936 → 归一化 **0.998**,时间效应几乎全部
  来自指数下跌,构成效应很小;
- 东区 0-5:名义 0.933 → 归一化 0.966,残差 ~3.4% 为构成/
  品质/样本效应(不要求完全收敛)。

**结论:V1.1 名义中位相对 2026-09 购买力高估;全市 0-5
新房名义 $3.25M → 归一化 $2.86M(-12%)**。

## 6. 社区标准新房(0-5)归一化价

| 社区 | n | 名义 P50 | 2026-09 P50 | 提示 |
|---|---|---|---|---|
| Hastings-Sunrise | 23 | $2,875,777 | **$2,524,252** | OK |
| Renfrew(北) | 13 | $2,650,000 | **$2,474,488** | OK |
| Kensington-Cedar Cottage | 18 | $2,800,000 | **$2,509,883** | OK |
| Collingwood(南) | 3 | $2,400,000 | **$2,067,299** | THIN |
| Riley Park | 17 | $3,999,999 | **$3,327,193** | REVISION_SENSITIVE:1 |
| Dunbar-Southlands | 21 | $4,500,000 | **$3,985,075** | OK |
| West Point Grey | 17 | $4,375,000 | **$3,817,954** | REVISION_SENSITIVE:2 |
| Sunset | 11 | $2,815,000 | **$2,585,001** | OK |
| Killarney | 5 | $3,140,000 | **$2,787,958** | OK |
| Marpole | 10 | $3,753,225 | **$3,304,638** | REVISION_SENSITIVE:2 |
| Kitsilano | 6 | $3,912,500 | **$3,631,015** | OK |
| Victoria-Fraserview | 6 | $2,746,666 | **$2,601,010** | OK |
| Grandview-Woodland | 9 | $1,588,650 | **$1,397,904** | OK |
| Kerrisdale | 6 | $4,801,875 | **$4,275,092** | OK |
| Arbutus Ridge | 6 | $3,980,050 | **$3,464,084** | REVISION_SENSITIVE:1 |
| Mount Pleasant | 4 | $1,258,425 | **$1,130,608** | THIN |
| Strathcona | 2 | $1,422,500 | **$1,248,386** | THIN |
| ? | 1 | $2,900,000 | **$2,781,398** | THIN |

全市 $4.00M 名义中位 → 归一化 $3.34M;**不得直接套用到东区**
(东区 $2.47M / 西区 $3.80M 差异 ~54%)。

## 7. 重建标准件(1,800–2,200sqft / 3,000–5,000lot / 0-5 / freehold)

见 `house_rebuild_2000sqft_2026_benchmark.csv`:

- 全市 n=48,名义 P50 $4.00M → **2026-09 P50 $3.34M**,
  P25 $2.79M / P75 $3.81M,固定 2,000sqft 参考价 **$3.46M**;
- 东区 n=21,P50 $2.77M,ref2000 $2.81M;
- 西区 n=27,P50 $3.80M,ref2000 $3.76M;
- 社区级:Hastings-Sunrise $2.83M / Riley Park $3.44M /
  Dunbar $3.79M / WPG $3.80M(归一化 P50);
  薄样本(n<5)标 THIN 并给出 SIDE_POOL/CITY_POOL 邻近参考。

## 8. 旧房买入端 ↔ 新房出售端(同 2026-09 基准)

`house_old_new_gap_normalized.csv`(old=31-50+51+,new=0-5):

| 范围 | 名义差 | 归一化差 | 名义比 | 归一化比 |
|---|---|---|---|---|
| Vancouver East | $939,000 | **$886,424** | 1.534 | 1.558 |
| Vancouver West | $1,325,000 | **$1,250,594** | 1.461 | 1.49 |
| __CITY__ | $1,204,375 | **$1,033,330** | 1.589 | 1.565 |
| Dunbar-Southlands | $1,547,060 | **$1,337,807** | 1.524 | 1.505 |
| Hastings-Sunrise | $1,143,277 | **$967,624** | 1.66 | 1.622 |
| Kensington-Cedar Cottage | $1,030,000 | **$916,340** | 1.582 | 1.575 |
| Riley Park | $1,639,999 | **$1,209,548** | 1.695 | 1.571 |

- 归一化后旧/新价差 **绝对值普遍收窄 8–26%**(如全市
  $1.204M→$1.033M):旧房价格端 2023-24 权重高、下调幅度大,
  买入成本在 2026-09 基准下低于名义值;
- 比值变化方向不一(东 1.534→1.558 微升,西 1.461→1.490,
  全市 1.589→1.565):旧/新房的成交时间分布不同导致因子不同。
  这正是"买入端与卖出端必须用同一时间基准"的意义。

## 9. 敏感性

A. 时间调整合理性:见 §5,方向性收敛达成;残差为构成效应。
B. 指数代理:仅 DUPLEX_*/MULTIPLEX_UNIT 需要代理(三情景+
   代理误差说明);HOUSE 用官方 Detached HPI 无代理。
   归一化本批 0 笔 PROXY / 0 笔 INDEX_MISSING。
C. 外部新房验证:`cmhc_market_crosscheck.md` —— CMHC CMA
   Single/Semi 吸纳中位 $1.83–3.0M;我们城市核心新 HOUSE
   归一化 $2.47M(东)/$3.80M(西)处于其 P40–P80 上沿,
   量级一致。CMHC 2023→2025H1 对该段零覆盖,不能检验早期价。
D. 修订敏感:{n_rev} 笔成交落在指数后期修订 >2% 的月份
   (主要 2023-02 前后西区),聚合列计数标出,不剔除;
   全表见 `hpi_revision_sensitivity.md`。

## 9b. 产品完成状态(整合门区分)

`normalization_status_by_product.csv`:

| 产品 | 状态 | 说明 |
|---|---|---|
| HOUSE | COMPLETE_OFFICIAL | 5,386 笔,Detached HPI 逐笔归一化 |
| CONDO | COMPLETE_OFFICIAL | 14,068 笔,Apartment HPI 逐笔归一化(同引擎) |
| TOWNHOUSE | INDEX_MAPPED_NOT_NORMALIZED | 官方 Townhouse HPI 已映射,逐笔归一化留待下轮 |
| DUPLEX_HALF/WHOLE | PROXY_SCENARIOS_ONLY | 无官方独立指数;三情景敏感性,非官方 |
| MULTIPLEX_UNIT | PROXY_SCENARIOS_ONLY | 同上 |

`duplex_proxy_scenarios.csv` 三情景示例(DUPLEX_WHOLE 东区,
名义 P50 $1.69M):中性(Townhouse)$1.49M / 偏弱(Detached)
$1.51M / 偏强(Apartment)$1.53M —— 代理区间 ±2%,明确标非官方。

## 9c. 同社区×地块档 新旧配对价差

`house_rebuild_spread_pairs.csv`(旧=31+ freehold 作土地购入代理,
新=0-5 freehold 1800-2200sf 标准件作重建出售代理;价差非毛利):

| 社区×地块 | n新/n旧 | 旧 P50(26-09) | 新 P50(26-09) | 价差 |
|---|---|---|---|---|
| Hastings-Sunrise 4000-5000 | 6/16 | $1.75M | $2.84M | $1.09M |
| Kensington-CC 4000-5000 | 2/49 | $1.71M | $3.25M | $1.54M(THIN) |
| Dunbar-Southlands 4000-5000 | 10/46 | $2.41M | $3.79M | $1.37M |
| Riley Park 4000-5000 | 7/46 | $2.35M | $3.56M | $1.21M |
| West Point Grey 4000-5000 | 9/30 | $2.38M | $3.80M | $1.42M |

全表 17 对;THIN 标记 n<3 的档。价差仅为"买入旧房 vs 卖新房"
的市场基准差,不含建造成本/拆迁/GST/融资 —— 不构成 ROI。

## 10. 不能稳健结论的社区

- **THIN**:Collingwood(南) / Mount Pleasant / Strathcona /
  Oakridge / South Cambie / Fairview / Downtown / "?"
  (0-5 档 n<5);
- **BORDER**:Mount Pleasant / Riley Park / Sunset 社区
  跨 Main St 分界,逐笔 lng 已正确处理,但社区聚合仍含两侧
  混合 —— 看 scope 级东/西行更稳;
- **REVISION_SENSITIVE**:含 2023 上半年西区成交的池
  (WPG/Riley Park/Marpole 等),因子对 HPI 版本选择敏感 ~±5%;
- Grandview-Woodland 0-5 P50 异常低($1.4M):社区内含小户型/
  非标准独立屋样本,需人工复核构成,不直接用于重建估值。

## 11. 交付物

- `cmhc_market_crosscheck.md`
- `condo_normalized_summary.csv`
- `duplex_proxy_scenarios.csv`
- `gvr_monthly_hpi_2023_2026.csv`
- `historical_price_normalization_factors.csv`
- `house_hpi_normalization_v1_2_report.md`
- `house_newbuild_normalized_by_community.csv`
- `house_old_new_gap_normalized.csv`
- `house_old_normalized_by_community.csv`
- `house_price_nominal_vs_2026.csv`
- `house_rebuild_2000sqft_2026_benchmark.csv`
- `house_rebuild_spread_pairs.csv`
- `hpi_product_geography_mapping.csv`
- `hpi_revision_crosscheck.csv`
- `hpi_revision_sensitivity.md`
- `manifest.json`
- `normalization_convergence_check.csv`
- `normalization_status_by_product.csv`
- `official_sales_coverage_comparison.csv`
- `manifest.json`
- `house_hpi_normalization_v1_2_report.md`

逐笔归一化明细留本地 `data/research/normalized/
house_hpi_normalization_v1_2/house_price_normalized_per_row.csv`
(受限数据,gitignored)。复现:
`python3 scripts/gvr_hpi_ingest_v1_2.py --fetch`(重抓 PDF,可选)
`python3 scripts/house_hpi_normalization_v1_2.py`
`python3 scripts/test_house_v1_2.py --run`
