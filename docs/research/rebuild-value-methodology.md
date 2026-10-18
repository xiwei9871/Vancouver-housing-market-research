# 温哥华重建价值研究体系 — 方法论与实现路径

> 更新至 2026-10-16。主实现：`scripts/rebuild_value_analysis.py`；
> 最新报告：`reports/watchlist/rebuild-value-2026-10-14.html/.json`。

## 0. 目标与总原则

购买目标：**自住 3–7 年（住得不差）+ 保留重建期权（挣钱的确定性相对最强）**。

筛选顺序（每步不过就不进下一步，不靠加权平均掩盖硬伤）：

1. **先看价**：ask/BCA、ask/地价（land residual 折价）
2. **再看地**：zoning + 法定地块门槛（能不能建，不是想不想建）
3. **看产品**：house / duplex / multiplex 分别估值，市场证据决定形态
4. **看居住**：主干道邻接、天车、学区、社区质感（人工层）

原则：价好+地够用=候选；其余交人工。所有阈值要么来自法规原文，要么来自实测成交回归，不允许"设计偏好"伪装成"法规要求"。

## 1. 数据层（研究方法）

### 1.1 Listing 发现

- REALTOR.ca 全量边界 preset 抓取（east/west/south/central/oakridge 五区），
  `config/research-discovery.yaml` 定义边界。
- watchlist  reconciler：`scripts/reconcile_watchlist.py` — 去重、状态变迁
  （ACTIVE/SOLD/EXPIRED_OR_WITHDRAWN）、降价事件、重挂事件。
- 当前规模：**240 套追踪，183 活跃**。

### 1.2 官方数据 enrichment

| 来源 | 用途 | 实现 |
|---|---|---|
| BCA ArcGIS sales 层 | 成交 comps（价格/面积/年份/坐标） | `bca-arcgis-sales-*-enriched.json` + `*-geometry.json` |
| BCA PropertyInformation | 面宽/进深/PID/legal/房龄/房间数 | POST + HTML 解析（`_real_detail_transport`）；注意：必须 POST，返回 HTML 不是 JSON |
| BCA 宗地层 geometry | 无明细页时的面宽/进深回退 | 最小外接矩形推导，存 `manual-dimension-overrides.json` 标记 `BCA_PARCEL_GEOMETRY` |
| BCA TOTAL_LAND/TOTAL_BUILDING | 地价、地价占比 | ArcGIS 宗地属性字段；人工纠错表 `MANUAL_BCA_OVERRIDES` |
| 市政 zoning | FSR 规则族 | `zoning_rules()` |

**教训**：BCA 地址匹配必须验证门牌+方向（曾错配 `3281 E 2ND`→`3281 22ND AVE W`、
`2408 SE MARINE`→SW 同号）；wildcards 要 `LIKE '4561%MANOR%'` 全包围。

### 1.3 Comps 集

- 新房成交（year_built ≥ 2020），36 个月窗（`COMP_WINDOW_DAYS = 1095`）
- 191 条当前样本；strata 判定靠 legal_description（"STRATA LOT/PLAN EPS/BCS"）
  + 地址门牌前缀，`official_bca_is_strata` 不可靠
- `COMP_MIN_FLOOR_SQFT = 1000` 排除微型单元（coach house 单价虚高）
- 单侧异常过滤：只砍低于社区中位 40% 的异常低价，保留高价（`OUTLIER_DEVIATION`）

## 2. 估值层（分析对比方法）

### 2.1 社区基准价

- 社区采用价 = 该社区 detached、非 strata、≤3,500sf comps 的**稳健中位 × 0.80**
  保守折扣，取整 $25；`HOOD_MIN_COMPS = 2`
- 样本不足回退区域价（东 1100 / 南中 1200 / 西 1300）
- 分析师 override：Riley Park 1350、Sunset 1250、Mount Pleasant 1300、
  Hastings-Sunrise 1300、Renfrew-Collingwood 1300（实测值仍展示在报告里）

### 2.2 空间定价 `local_rate()`

- **双池结构**：detached 产品吃 detached comps，strata 产品（duplex/multiplex）
  吃 strata comps——strata 成交价本身已含"小单元溢价+产品形态折价"，
  再乘产品系数等于重复计算（曾犯此错，已删除）
- 核加权：400m 高斯核 + 18 个月半衰期 recency 权重；eff_n<2 回退社区/区域先验
- **面积衰减曲线**：`comp_$sf × (目标sf/comp_sf)^k`，把每条 comp 重定价到目标
  单元面积，封顶 [0.65, 1.45]
  - k_detached = **-0.55**（raw -0.74，社区固定效应内 -0.63，取保守值；
    >3,500sf 档样本薄）
  - k_strata = **-0.15**（实测 ≈0，取温和折价防小单元溢价虚高）
  - 实测分段中位：<1,500sf $1,760 → >3,500sf $792/sf
- **HOUSE 封顶 4,500sf**：超大地多余面积按花园/土地银行处理，不进估售

### 2.3 strata 证据层级（multiplex/duplex 定价来源）

500m 本地 strata → 社区 strata 中位 → 区域 strata 中位 → detached 推断
（标 `推断·无strata`）。证据源逐格标注：`本地·strata / 社区·strata / 区域·strata`。

### 2.4 产品形态折价

- **MULTIPLEX × 0.85**（`MPX_PRODUCT_FACTOR`）：strata 池是 townhouse/半独立屋
  主导（地面型、有院、户大）；叠拼多户单元无私人后院、share wall 多，
  清盘价估低 10-20%，取中值 15%
- DUPLEX 不打折：半独立屋本身就是池内产品

### 2.5 法规层 `zoning_rules()` + multiplex 门槛

| zoning 族 | HOUSE | DUPLEX | MULTIPLEX | 备注 |
|---|---|---|---|---|
| R1-1/RS/RA/RR | 0.70 | 0.70 | 1.00 | 主力 152/240 |
| R3-1/2/3 | 0.60 | 0.60 | 1.00 | 密度增益最大 |
| RT-* | 0.70 | 0.75 | 1.00 | 双拼区 |
| R4/R5/FSD | 0.70 | 0.75 | 1.00 | |
| RM-* | 0.70 | 0.85 | 1.00* | 各自 schedule，统一 1.0 是保守简化 |
| C/CD/DEOD/其他 | 0.70 | — | — | 个案审批 |

Multiplex 法定门槛（R1-1/missing-middle，AND 条件）：

- 基础档：**地块 ≥3,294sf 且 面宽 ≥32.8ft 且 进深 ≥99.7ft** → 3-4 户
- min-4 条款：地块 >4,984sf **或** 面宽 >43.6ft → **最少 4 户**（大地不许只做 3 户）
- ≥44ft + ≥4,995sf → 至 5 户；≥49.5ft + ≥5,996sf → 至 6 户（出租可到 8）
- 报告按**最少合法户数**建模（最大单元=最高单价=保守最优），区间标注在格内

### 2.6 成本与回报

- 建安（统一）：HOUSE **$350** / DUPLEX **$400** / MULTIPLEX **$450** /sf
- 设计办证 $40/sf；销售 4%；**GST 5%**
- 资金（先住后建，不计审批期，2 年建设+销售窗）：
  - 地价 × 10%（房贷 5%/年 ×2年）
  - 建安 × ~15.5%（35% 自有 @3.5% 机会成本 + 65% 开发贷 @10%，×2年）
- 输出双指标：**ROI**（回报率）+ **净利润$**（绝对额）。两者排序会分叉：
  multiplex 常是净利最高但 ROI 被成本基数摊薄。

## 3. 筛选与居住层

- **ask/地价** 是最干净的地价折价指标（ask/BCA 混入建筑价值会失真——
  破房贵地会显示"假便宜"）
- **主干道筛**：地址含 arterial 名 + 坐标距南北向主干道 <120m（Rupert/
  Nanaimo/Renfrew/Boundary/Hastings/Broadway/41st/Knight/Fraser/Main/
  West Blvd 等）——"靠路的折扣"不等于"地价没觉醒"
- 居住 3-7 年要求：现有房能住（1940-60s 可接受）、内街、社区质感

## 4. Pipeline 实现路径（每周）

```
1. discovery 全量 preset 抓取      → 新 listings + 状态变化
2. reconcile_watchlist.py          → 去重、降价/重挂/sold 事件
3. official enrichment             → BCA 明细/宗地/地价/面宽回退
4. run_weekly_photo_pipeline_*     → 照片 + auto-prefill + 校准卡片 HTML
5. rebuild_value_analysis.py       → comps + 估值 + 三方案 ROI/利润 + HTML/JSON
6. check_watchlist_outcomes.py     → 结局追踪（pending/sold/withdrawn）
7. render_watchlist_pages.py       → sold/降价/对照三页
```

人工层：卡片包 KEEP/WATCH/PASS/SPECIAL 分级 → 喂回结局校准。
本地服务 `http://127.0.0.1:8791` 看报告。

## 5. 已验证结论

- **Hastings-Sunrise 北段**是"地价低估+法规合规+居住可接受"三重叠最密的口袋；
  市场验证：3153 Parker 两周 pending、3910 W 10th（WPG 折价地）已成交
- 温西 strata 实测单价不低于东区（$1,992-2,134/sf 成交）——**缺供给≠缺需求**
- 真实成本假设下全市中位 ROI ≈ 0：值得做的本来就是少数，筛选器价值在找例外
- 推荐观察清单：3281 E 2nd（地价比 0.79，已降价）、3466 E 2nd（6 户天花板）、
  1380 E 54th、3148 E 1st、3153 Parker（对照）

## 6. 已知边界 / TODO

- RM 各区统一按 1.0 FSR，30 套可能系统性低估（逐区查 schedule）
- RT 区 duplex 用统一 3,294sf 门槛，部分 RT schedule 可能更宽松
- 开发贷按进度放款（平均占用 ~50% 建安）未建模——会抬 ROI 但方向一致
- 主干道邻接还是标签不是打分字段
- 去化深度/周期不进 ROI（ROI 是"建成即卖"静态值）
- strata 池含 2021-22 利率底部成交，recency 权重已降权未消除
