# 策略标注封版 + 价格事件一致性验收(2026-10-23)

范围:`calibration-annotations-11.json` 41 条人工判断入库验收 +
挂牌价格事件语义核销。**不改** V3s 冻结估值、ROI、候选排名。

## 1. 标注入库验收

- `data/research/annotations/strategy-v2-human-annotations-2026-10-23.json`
- 41 条记录,41 个唯一 property_key,41 个唯一地址
- 决策分布:**KEEP 7 / WATCH 27 / PASS 7**
- KEEP/WATCH/PASS 是人工**策略档位**,不等于买入确认或经济可行裁定;
  与 V3s 计算收益各自保留。

### Tier 对齐与核销

`scripts/apply_human_annotations.py`:标注按 property_key(`mls:Rxxx`)
记录;重挂房源同一地址存在多标注身份。权威规则 = **标注 key 的
MLS 匹配当前挂牌 MLS 优先**,否则取标注文件最新日期。

`tier_change_reconciliation.csv`:6 条有效变更
(旧等级/新等级/理由/标注时间/来源五要素齐全):

| 房源 | 变化 |
|---|---|
| 2413 COLLINGWOOD | WATCH → KEEP |
| 3326 SLOCAN | WATCH → PASS |
| 4940 KILLARNEY | WATCH → KEEP |
| 2608 E 4TH | PASS → WATCH |
| 3120 TURNER | PASS → WATCH |
| 5033 KILLARNEY | PASS → WATCH |

**幂等**:连续两次运行输出逐字节一致(已验证);
TIER_REVIEW 事件至多一条"自动化前 tier → 权威决定",
零净值变化不记事件。曾发生的同地址多 key 翻转噪音
(3305 WORTHINGTON、4265 SLOCAN、4561 NANAIMO、4225 JOHN、
777 E PENDER、2216 E 41ST)已清理,终态与当前 MLS 身份一致。

## 2. 价格事件核销(`price_events.py`)

一条经济价格变化只计一次;换 MLS/扫描缺席是**机制属性**,
不再与 PRICE_UP/DROP 重复计数。规范层写入
`entry.price_change_events`;原始 `events` 日志保留不改(血缘)。

| 房源 | 链 | 标签 |
|---|---|---|
| 3184 E 8TH | R3167057→R3171659 | `RELISTED_HIGHER` +$160,000 |
| 3326 SLOCAN | 同 R3166617,缺 10-05/10-14 两扫 | `REAPPEARED_WITH_HIGHER_ASK` +$150,000 |
| 4940 KILLARNEY | R3045627→R3173056 | `RELISTED_LOWER` −$300,000 |
| 614 E 19TH | 缺 10-05 扫描→低价回归;再同挂降 | `REAPPEARED_WITH_LOWER_ASK` −$100,000 + `PRICE_DROP` −$100,000 |

**重要口径修正**:614 E 19TH 不是"同 MLS 两次降价"——
10-05 扫描缺席属实(全库扫描日历 09-05/09-20/10-05/10-14/10-20
交叉验证),10-14 回归时价格已降。正确链 = 重现低挂 + 一次真实
同挂降价。累计 −$200,000 不变,机制更准确。

**REAPPEARED 语义**:扫描缺席再出现 = 挂牌行为异常;
可能原因含暂时下架、采集可见性、平台过滤——**不证明撤牌,
不断言 DOM 重置**,不当作市场涨价/跌价信号。

### 全库审计(152 条有事件房源,`price_event_chain_audit.csv`)

| 最近事件 | 数量 |
|---|---|
| REAPPEARED_WITH_SAME_ASK | 101 |
| RELISTED_LOWER | 20 |
| PRICE_DROP(同挂真实降价) | 12 |
| REAPPEARED_WITH_LOWER_ASK | 12 |
| RELISTED_SAME | 5 |
| RELISTED_HIGHER | 1 |
| REAPPEARED_WITH_HIGHER_ASK | 1 |

注意:**101 条同价重现**提示采集层存在可见性波动——
真实价格方向信号集中在 ~45 条 delta≠0 事件上。
列表徽标只显示含经济变化的条目,纯可见性异常进审计表。

### 一致性

列表徽标 / watchlist `price_change_events` / `latest_asking`
三者同源(均由 `build_event_chain` + sightings 派生)。
`render_v3s_shadow.py` 已接入 all_scans;徽标语义:
`重挂`=RELISTED、`重现`=REAPPEARED、`要价`=同挂改价、
`变动?`=缺 MLS 不可判定。

## 3. 测试

`scripts/test_price_events.py`:**25 PASS / 0 FAIL**
- 合成:F1 重挂降价/F2 同挂涨价/F3 重挂涨价/F4 两次连降保中间/
  F5 多次重挂+F6 首尾同价/F7 缺MLS/F8 乱序纠正/F9 缺扫描→REAPPEARED/
  F10 无缺口→PRICE_INCREASE/F11 换MLS标签
- 真实回归:4 案例标签断言 + 152 行全量审计生成

## 4. 不变量

- V3s `valuation-v3s-shadow-2026-10-22.json` sha256 前缀
  `8ed4af1d6b7c8ece` —— 本轮未动(见 manifest)
- 原始 `events` 日志、历史标注文件均未删改
- 本地卡组/索引(localhost:8766)为表现层,不入库
