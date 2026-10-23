# Newbuild Discovery V2.1 报告

基线 29653ce;V1.0.3 档案/V3s 冻结。
2026 cutoff=2026-07-08。匿名=HMAC-SHA256(私有密钥)。

## P0-B 2022 基线恢复
- prev↔prior_current:{'na': 215532, 'match': 655495, 'diff': 378}
- 接入 2022 基线的 pid:213782
- 首年=2022 的 jump 地块:10521
- diff 例外行(取快照 current 为准):378
- 128 召回(STRONG+MOD):without2022=84 → with2022=**87**

## 四级门(全 5,386,独占桶)
{'ALL': 5386, 'NOT_DETECTED': 4396, 'EV_CONSTRUCTION': 957, 'EV_AGE': 291, 'EV_COMPLETION': 128, 'SALE_SALE_SOURCE_CONFIRMED': 4967, 'DETECTED': 740, 'LIKELY_NEW_AT_SALE': 132, 'PRICING_COMPARABLE': 111, 'SALE_SALE_SINGLE_SOURCE': 416, 'SALE_SALE_CONFLICT': 3, 'VERIFIED_NEW_AT_SALE': 7}

## P3 第一组:128 基线
- 全部 128 成交时竣工证据闭合(CONFIRMED):128
- PRICING_COMPARABLE 保留:111(V2 严格口径同数)
- VERIFIED 未升 PRICING:7
  (缺 fa/销售口径/scope 证据)
- LIKELY 未升 VERIFIED:
  10
  (单源成交证据,缺第二来源)
- sale_kind 分布:{'RESALE': 67, 'FIRST_SALE': 44, 'UNKNOWN': 17}

## P3 第二组:新增发现
- V2 口径 pre-sale 匹配行:951
- 时序候选行→唯一成交/产权/项目:
  1235/589/581/581
- 候选五类:{'UNRESOLVED_TEMPORAL_ORDER': 927, 'CONFIRMED_NEW_HOUSE_SALE': 132, 'PROBABLE_NEW_HOUSE_SALE': 103, 'CONSTRUCTION_STAGE_SALE': 73}
- 52 REVIEW 结案:{'CONSTRUCTION_STAGE_SALE': 39, 'PROBABLE_NEW_HOUSE_SALE': 13}(39 在建期转让+13 疑似)
- 336 老房突变归属:{'OLD_HOUSE_OR_LAND_SALE': 322, 'PROBABLE_NEW_HOUSE_SALE': 16, 'UNRESOLVED_TEMPORAL_ORDER': 21}
  (主体=成交后买家重建 OLD,322/359;PROBABLE 16)
- 259 UNKNOWN:{'NOT_NEWBUILD_SALE': 255, 'OLD_HOUSE_OR_LAND_SALE': 3, 'UNRESOLVED_TEMPORAL_ORDER': 1}
- 2022 基线新识别 jump 地块:10521
- 血统重核:{'CONFIRMED': 103, 'CANDIDATE_WEAK': 31, 'SPLIT_CANDIDATE': 121}(CONFIRMED 103;
  SPLIT 121 全部保留候选不合并;WEAK 31)
- VERIFIED_CUM 中普查外新增:0
- PRICING 中普查外新增:0

## P3 第三组:全市缺口
- jump 地块:10878;命中唯一成交:4370
- 无成交匹配(≠未售):7763
- 需 MLS/GVR 或 BCA 交易明细核实:
  PROBABLE 534 行 +
  STAGE 76 行 +
  UNRESOLVED 2397 行
- 年×区已证明细:house_newbuild_verified_by_year_region.csv
- 2026-07-08 后:税务快照与成交均截断,需 2027 快照
  补当年施工价值显化。

## 反查命中产品分布(中性名,非误分)
{'DUPLEX_WHOLE': 918, 'HOUSE': 2532, 'MULTIPLEX_WHOLE': 88, 'DUPLEX_HALF': 331, 'CONDO': 1853, 'UNKNOWN': 511, 'OTHER_RESIDENTIAL': 225, 'MULTIPLEX_UNIT': 47, 'TOWNHOUSE': 697}

## 盲区
- 2022 基线仅 prev 字段单源,无 2022 全年快照;
- 评估滞后:jump_year≠竣工时点;
- 许可签发≠竣工;许可/房龄双缺的 STRONG 行停 PROBABLE;
- 一对多血统保留图结构不自动合并;
- 无 pid 行无法入血统/轨迹。
