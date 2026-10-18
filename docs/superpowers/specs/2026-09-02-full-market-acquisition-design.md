# Greater Vancouver Full-Market Acquisition Design

Date: 2026-09-02  
Status: Design approved by user; written-spec review required before implementation  
Scope: Phase 2 acquisition and Stage 1 candidate discovery only

## 1. Objective

将现有的 bounded Playwright 试验扩展为可重复运行的 full-market acquisition pipeline，扫描配置范围内的公开在售 detached-house listing universe，并在本地执行第一阶段筛选。

“Full-market”在本项目中定义为：

1. 对配置的 Greater Vancouver municipality entrypoints 全部启动；
2. 对每个 source 能够发现的搜索结果分页持续跟进，直到自然结束、明确达到 source 的最大公开页数，或被 source/政策限制中断；
3. 对搜索卡片做高召回采集，再由本地 Stage 1 规则筛选；
4. 保存每个搜索页、详情页、分页状态、跳过原因和覆盖状态，使每周结果可以审计和增量比较；
5. 不把“某一个网站宣称的总房源数”当成已经抓完，也不把 MLS 完整覆盖暗示为已经实现。

本阶段仍然是 read-only。不会登录、发布、修改外部数据，不绕过 CAPTCHA/挑战，不调用未公开或未经授权的 MLS/DDF/IDX endpoint。

## 2. User-approved acquisition policy

### 2.1 Market buckets and search ranges

扫描范围和价格范围必须继续放在 YAML config，不写死在 spider 中。初始筛选范围如下：

| Market bucket | Core / research range | Notes |
| --- | ---: | --- |
| Vancouver West | CAD 1.5M–2.0M | 包括 Westside；具体 neighbourhood 可继续独立 benchmark |
| West Vancouver | CAD 1.5M–2.0M | 这是 research range，不是 fair value |
| North Vancouver | CAD 1.0M–1.6M | City 与 District 分开记录 |
| Vancouver East / 非高端 Westside Vancouver | CAD 1.0M–1.6M | 包括靠近 Burnaby 的 East Vancouver |
| Burnaby North / 其他纳入 Burnaby bucket | CAD 1.0M–1.6M | 作为 affordability benchmark |

范围只用于发现候选，不代表这些 asking 一定便宜。范围外房源默认不进入标准 Stage 1 候选，但可以通过显式 config 开启特殊机会通道。

### 2.2 Current-use gate

主要研究对象是当前可识别为 freehold detached house / single-family house 的物业。未来的 duplex/triplex 是 redevelopment scenario，不得因为 listing 文案写着 duplex potential 就把当前 duplex、strata、coach house、leasehold 等混入 house 主队列。

最低当前使用条件：

- bedrooms >= 3；
- bathrooms >= 2.5；
- finished living area >= 1,500 sqft；
- listing/property type 能确认属于目标 detached-house 范围；
- ownership 已确认是 Freehold Non-Strata，或处于 `NEED_VERIFICATION` 而不是被猜测为 freehold。

字段未知时不得用 0 或默认值替代。记录为 `UNKNOWN`，并将记录送入人工补充/`NEED_VERIFICATION` 队列。

### 2.3 Lot gate and exceptions

标准 redevelopment research gate：

- lot area >= 3,000 sqft；
- frontage >= 33 ft；
- depth >= 100 ft；
- dimensions 必须来自可靠字段或明确可审计的来源。

面积评分继续采用用户确认的锚点：3,000 sqft 以下不通过标准土地门槛，4,000 sqft 约 80 分，6,000 sqft 及以上 100 分；frontage 33 ft 为可用门槛，50 ft 及以上为优秀参考点。实际分数由现有 lot geometry model 计算。

小 lot 不应被错误解释成“没有任何价值”：Vancouver West 的小 lot 可以在显式标记为 `HOUSE_REBUILD_ONLY` 的特殊通道中保留，用于评估未来重做 single detached house 的 current-use/resale value；它不能因为房子能重建就自动获得 duplex/triplex land score。

本阶段不把坡度作为主筛选变量，也不因未取得 slope 数据而臆测结论；如果现有配置启用了 steep-lot 排除，则结果中必须明确标注该配置规则，后续可单独调整。

### 2.4 Permanent defects

主路、卡车路线、铁路、高速相邻等是永久性 location defect，要扣分并进入 investment thesis，但不统一 hard reject。`road_class + distance + outdoor_noise_exposure + current_price_dislocation` 联合判断。

用户确认的初始规则继续保留为配置项：major road 50m 内且当前折价不超过 15% 时，通常排除标准候选；若折价足够大，保留为 `SPECIAL_SITUATION`/人工复核，而不是自动丢弃。室内隔音改善能力和室外噪音暴露必须分别保存。

## 3. Architecture

### 3.1 Chosen architecture

采用两层采集架构：

```text
source entrypoints
        |
        v
Scrapy scheduler + dupefilter + JOBDIR
        |
        +-- scrapy-playwright for browser-rendered search/detail pages
        |
        v
append-only crawl artifacts / manifest
        |
        v
existing source parser + normalization + provenance/conflict handling
        |
        v
existing SQLite storage + Stage 1 scan + report/watchlist
```

Scrapy 负责全市场分页、请求队列、去重、限速、失败重试和断点续跑；scrapy-playwright 负责需要浏览器渲染的公开页面；现有 `public_feeds.py` 的 parser、`acquisition.py` 的归一化与 Stage 1、`storage.py` 的历史保存继续作为系统核心，不另建平行 property database。

### 3.2 Process boundary

`research-crawl` 和现有 `research-acquire` 分成两个 CLI 进程：

1. `research-crawl` 启动 Scrapy reactor，完成一个 source/job 的页面采集，写入 append-only crawl manifest 和 raw artifacts；
2. `research-acquire --from-crawl <manifest>` 读取 manifest，通过现有 acquisition adapter 归一化、去重、记录 provenance/conflict，然后执行 Stage 1；
3. 后续如用户要求，再由 `research-run-weekly` 接管 enrich/comps/redevelop/score/report。

这样可以避免在已经运行的 CLI 进程里重复启动 Twisted reactor，也使 crawl 失败不会破坏已有标准化数据。

### 3.3 Planned files and reuse boundary

计划新增或扩展以下组件：

- `src/northshore_watchdog/research/crawling/__init__.py`：crawl package；
- `src/northshore_watchdog/research/crawling/runner.py`：Scrapy process、settings、job directory、manifest lifecycle；
- `src/northshore_watchdog/research/crawling/spider.py`：search/detail request graph、pagination、source coverage；
- `src/northshore_watchdog/research/crawling/artifacts.py`：raw HTML/screenshot/manifest append writer；
- `src/northshore_watchdog/research/crawling/policies.py`：domain allowlist、robots、rate/concurrency、request caps、challenge detection；
- `src/northshore_watchdog/research/crawl_source.py` 或现有 source adapter 文件：把 crawl manifest 接入 `SourceRunResult`，复用现有 acquisition pipeline；
- `config/research-full-market.yaml`：全市场 entrypoints、filter、crawl limits 和 source policy；
- `data/research/sources/full_market_source_registry.yaml`：source provenance、authorization/evidence status；
- `data/research/crawl_jobs/`：每个 source/run 独立 JOBDIR 和 manifest，不覆盖旧运行；
- CLI：增加 `research-crawl`，并增加从 manifest 运行 acquire 的显式参数。

现有 `PropertyRecord`、`SourceSnapshot`、`SourceRunResult`、`PublicFeedSource` parser、`acquire_from_sources()`、`scan_stage1()`、SQLite history 和 report template 应优先复用。新代码不得复制一套地址、listing history、conflict 或评分逻辑。

## 4. Source-specific crawl behavior

### 4.1 Zolo

Zolo 搜索页使用同域 canonical route 和 `rel=next`/next-page link 继续分页。Spider 必须：

1. 从配置的 municipality route 开始；
2. 只接受同域、同搜索意图的 canonical next URL；
3. 记录 page URL、canonical URL、HTTP status、页面 SHA-256、card count、next URL；
4. 同一 URL 或同一页面 hash 重复出现时停止该分支并记为 `REPEATED_PAGE`；
5. 没有 next link 且页面成功解析时，标记该 route `COVERAGE_COMPLETE`；
6. 页面没有 next link 但搜索结果数量明显与页面内容矛盾时，标记 `PARTIAL`，不得假称抓完；
7. 搜索卡片先收集 listing/detail URL，再根据 Stage 1 cheap fields 决定详情页请求。

Zolo 详情页解析需要补齐当前 parser 缺少的字段：property type、bed/bath、finished sqft、lot size、year built、tax、MLS number、neighbourhood 和 listing description；每个字段保留原始文本和 source URL。

### 4.2 Realtor.ca

Realtor.ca 当前首屏能够通过浏览器得到 SSR listing cards 和总数信息，但当前观察到的 next control 会改变 hash/页面状态而不一定更换 listing set；直接伪造 `CurrentPage` 请求还会遇到 403。实现必须采用验证式分页：

1. 先采集首屏 SSR page；
2. 优先使用可见的 pagination control/selector 进行浏览器交互；
3. 每次翻页后比较 URL、当前页标记、listing ID/MLS 集合和 HTML hash；
4. listing set 没有变化、页面标记没有推进、出现 challenge 或请求被阻断时，停止翻页并记录 `PAGINATION_NOT_ADVANCED`/`BLOCKED_POLICY`；
5. 只有每页 listing set 实际变化且自然达到末页时，才标记 `COVERAGE_COMPLETE`；
6. 在 Realtor 分页行为稳定前，报告必须显示 `PARTIAL` 和 coverage gap，不得把首屏结果称作 Realtor full-market。

不得调用从页面 JS 逆向得到的未公开 API endpoint，不得使用 stealth、CAPTCHA bypass、未授权代理或登录态绕过访问限制。若公开浏览器路径不能稳定覆盖，保留可审计的部分结果，并将 MLS/DDF/IDX feed 作为后续授权方案。

### 4.3 Detail-page policy

搜索页采用高召回策略，详情页采用本地 cheap filter 降低请求量：

- 明确不属于 house、明显超出价格上限、明显少于 3 beds 的卡片，可以不抓详情并记录 skip reason；
- 价格、bed/bath、面积、lot、ownership、property type 任一关键字段 unknown，或卡片看起来符合范围时，抓详情页；
- 详情页仍然不能确认产权、PID、approved use、zoning、assessment、school、sold history；这些由官方/半人工 enrichment 阶段处理；
- 详情页失败不删除搜索卡片，保留 `NEED_VERIFICATION`，并记录失败状态和重试信息。

## 5. Crawl artifact and data flow

每个 crawl run 使用独立目录，例如：

```text
data/research/crawl_jobs/<source>/<run_id>/
    scrapy-jobdir/
    artifacts/
        manifest.jsonl
        summary.json
        raw/search/*.html
        raw/detail/*.html
        screenshots/entry-and-errors/*.png
```

Manifest 每行至少包含：

- `run_id`, `source_name`, `source_registry_id`;
- `request_kind` (`SEARCH`, `DETAIL`);
- requested/final/canonical URL;
- page number or pagination token when available;
- retrieval timestamp, status code, response headers subset;
- raw path, screenshot path if any, content SHA-256;
- parser name/version and parse status;
- card count, extracted row count, next URL;
- `coverage_state`, `skip_reason`, `error_type`, retry count;
- source-level raw result count when visible.

The manifest is append-only for one run and immutable after completion. A new weekly run creates a new directory; existing SQLite `source_snapshots` and `property_observations` continue to preserve normalized historical observations.

Deduplication order：

1. MLS number/listing ID；
2. normalized canonical source URL；
3. normalized address + municipality；
4. if identities disagree, keep both evidence records, mark `data_conflict`, and do not silently merge.

## 6. Stage 1 filtering and routing

采集范围必须比候选范围宽；不要把所有限制塞进 source URL，也不要在 spider 里计算最终评分。顺序如下：

```text
collect all reachable active house cards
    -> normalize cheap fields
    -> deduplicate listing identities
    -> apply configured market bucket and price range
    -> apply current-use minimums
    -> apply title/property-type gate when known
    -> apply lot geometry gate when known
    -> apply road/noise exclusion policy
    -> route unknown/conflicted data to NEED_VERIFICATION
    -> persist all included/excluded observations
```

Stage 1 输出至少区分：

- `A/B/C` 只在评分和 confidence 规则满足后使用；
- `NEED_VERIFICATION`：高潜力但 ownership/PID/lot/zoning/use/price identity 等关键数据未确认；
- `HOUSE_REBUILD_ONLY`：小 lot 或其他不满足标准 duplex/triplex land gate，但可能有 current-use/single-house rebuild value；
- `OUT_OF_SCOPE`：明确不满足当前 house/price/use gate；
- `REJECT`：hard veto 或永久缺陷/冲突达到拒绝条件。

本阶段不因“没有 sold comps”而丢弃房源；assessment comparison 仍可作为低买入线索，但必须与 comparable value、land proxy、永久缺陷和数据 confidence 分开显示。没有 comps 时标注 `LOW_COMP_CONFIDENCE`，不能伪造精确 fair value。

## 7. Resilience and safety controls

初始 policy：

- 每个 domain 初始 concurrency 1；
- 使用 robots 检查、显式 delay 和 AutoThrottle/等价节流；
- 每个 source/run 有硬性 page、detail、request 和 wall-clock cap；达到 cap 的状态是 `PARTIAL_CAP_REACHED`，不是 complete；
- source、run、JOBDIR 隔离，避免并发运行互相污染；
- Scrapy JOBDIR 支持中断后 resume；恢复时保留已抓 URL、dupefilter 和 spider state；
- transient 5xx、timeout、browser crash 可有限重试；challenge/403 不做无限重试；
- entry page、pagination failure、challenge、关键 candidate detail 可以截图；不对每个普通页面默认截图；
- 只允许配置域名和路径，拒绝 spider 被页面链接带到无关域名；
- 所有 stop/skip/failure 都写入 manifest 和 summary。

## 8. Configuration design

`config/research-full-market.yaml` 负责运行参数，不保存隐含逻辑：

- source entrypoints and source-specific selectors;
- allowed domains and route purpose;
- market bucket mapping;
- core/extended price ranges;
- minimum beds/baths/finished sqft;
- standard lot area/frontage/depth thresholds;
- small-lot house-rebuild exception;
- major-road distance/discount rules;
- page/detail/request/time caps;
- concurrency, delay, robots, retry policy;
- screenshot/challenge policy;
- output directories and report path;
- `expected_public_result_count` only as an observability hint, never as a completion assertion.

Source registry continues to hold source identity, role, authorization status, evidence status, access method, reliability notes and retrieval policy. Runtime route settings cannot downgrade trusted registry metadata.

Optional dependency group：

```toml
[project.optional-dependencies]
crawler = [
  "scrapy>=2.11,<3",
  "scrapy-playwright>=0.0.40,<1",
]
```

The existing `browser` extra remains for the direct Playwright adapter and smoke tests. Exact versions may be adjusted to the project Python/Playwright compatibility observed in the implementation environment.

## 9. CLI contract

Planned commands：

```text
northshore-watchdog research-crawl \
  --config config/research-full-market.yaml \
  --sources zolo-full-market,realtor-full-market \
  --run-id <optional>

northshore-watchdog research-acquire \
  --config config/research-full-market.yaml \
  --from-crawl data/research/crawl_jobs/<source>/<run_id>/artifacts/manifest.jsonl
```

`research-crawl` 只采集和产出 manifest；`research-acquire` 负责进入既有 normalization/storage/Stage 1 pipeline。两者必须输出 machine-readable summary，并在终端显示：entrypoints、pages fetched、cards、details、normalized、duplicates、in-range、excluded、need verification、errors、coverage state。

## 10. Tests and acceptance criteria

### 10.1 Automated tests

必须覆盖：

- Zolo search pagination follows next link until natural exhaustion；
- same URL/page hash stops repeated pagination；
- Realtor pagination detects unchanged listing set and returns partial；
- same-domain/canonical URL policy；
- request/page/detail caps and skip reasons；
- pause/resume state and duplicate suppression；
- Zolo search-card field extraction；
- Zolo detail-page field extraction；
- Realtor SSR search-card extraction and challenge detection；
- manifest append/read bridge to `SourceRunResult`/existing storage；
- MLS → URL → address deduplication；
- PID/listing/address conflicts remain conflicts；
- market-specific price ranges；
- minimum 3 beds / 2.5 baths / 1,500 sqft；
- lot 3,000/4,000/6,000 sqft score anchors；
- frontage 33/50 ft and depth 100 ft gates；
- current duplex/strata/leasehold exclusion without losing future scenario fields；
- unknown fields route to `NEED_VERIFICATION`；
- major-road discount rule and permanent-defect score deduction；
- historical source snapshot/watchlist preservation and weekly delta.

### 10.2 Real-data acceptance sequence

实现后先执行可控的 real-data smoke slice：

1. Zolo 四个 municipality routes 各抓最多 2 个搜索页，并抽取若干详情页；
2. Realtor 至少运行一个 configured entrypoint，验证首屏和分页状态；
3. 人工检查 raw HTML、URL、地址、ask、beds/baths、lot、MLS 和详情页对应关系；
4. 通过后再运行 Zolo 全部可达分页；
5. Realtor 只有在 pagination set 确实推进并自然结束时才运行并报告 complete，否则报告 partial；
6. 用真实结果运行 Stage 1，人工抽查进入、排除和 `NEED_VERIFICATION` 的原因。

Phase 2 acceptance requires：

- 目标 entrypoints 全部启动并有 source-level coverage status；
- Zolo 在未遇到错误/上限时能够自然耗尽分页；
- Realtor 不得把未验证的首屏/重复页称为 full coverage；
- 每个抓取页面都有 manifest/raw evidence；
- 运行可以中断后恢复，重复运行不会重复写同一 listing observation；
- Stage 1 能输出真实全扫描的 counts 和 candidate set；
- 任何未知/冲突数据都能回溯到 source URL 和 raw artifact；
- 不启动 Phase 3 Comparable Engine、Phase 4 Redevelopment Engine 的新自动化实现，除非用户另行确认。

## 11. Alternatives and decision

### Direct Playwright only

保留用于 smoke test、详情页验证和少量页面交互，但不作为全市场 durable scheduler：缺少成熟的分页队列、断点续跑、去重、失败统计和大规模 artifact 生命周期管理。

### Scrapy without browser rendering

对 Zolo 的静态分页可能足够，但对 Realtor 当前 SSR/JS pagination 和详情页动态状态不够稳定。

### Scrapy + scrapy-playwright

作为本阶段选择：Scrapy 提供 crawl orchestration，scrapy-playwright 仅在 source 需要浏览器时使用；二者之间以 source-specific parser 和 append-only manifest 解耦。

### Authorized MLS/DDF/IDX feed

如果未来获得合法授权，这是覆盖、稳定性和字段一致性更好的主数据源，可以加入 source registry 并置于优先级第一；当前没有该 feed，因此本阶段不假设其存在。

## 12. Non-goals

本阶段不做：

- MLS/DDF/IDX 完整性声明；
- 登录态、VOW、broker-only 数据获取；
- CAPTCHA、WAF 或 robots 绕过；
- 大规模 sold-history/assessment/zoning/school 自动 enrichment；
- 精确开发许可判断或施工报价；
- dashboard/Web UI；
- 自动出价、联系经纪、外部写操作。
