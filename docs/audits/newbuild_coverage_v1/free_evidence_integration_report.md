# Free Evidence Integration V1 — 报告

日期 2026-10-23。基线 `2d2751e`。全部离线复核,零新增网站请求。
**12 条候选全部不入正式价格池**(待人工交易核验);V3s 零改动。

## 1. 829 身份核销(P0a)

| identity | n | 含义 |
|---|---|---|
| IDENTITY_SINGLE_CANDIDATE | 805 | 地址唯一候选;comps 无 pid 可对照 |
| IDENTITY_PID_CONFIRMED | 4 | bca_pid == comp pid 双证 |
| OUT_OF_SCOPE_MUNICIPALITY | 20 | Burnaby 记录,身份无碍仅出范围 |
| IDENTITY_UNRESOLVED | 0 | — |

**限制(诚实)**:回填未存档返回地址文本,故"唯一候选"是主证据;
sales_history 在详情页解析中未捕获 → **成交侧无法离线核对**。

## 2. 12 条新建候选漏斗(P1)

```
12 NEW_CANDIDATE
 ├─ EXCLUDE_MUNICIPALITY ×2(Burnaby)
 ├─ EXCLUDE_STRATA_UNIT ×3(1- 前缀,comps 标 HOUSE_SUSPECT_STRATA)
 └─ 7 条 Vancouver 非 strata HOUSE
      └─ 全部 ELIGIBLE_PENDING_MANUAL_SALE_CHECK
         (yb+面积有;交易与 BCA 记录比对缺 sales_history)
```

**净增有效可比成交 = 0(自动);上限 7(人工核验后)。**
不可把 HOUSE 217 改成 224——候选还需交易真实性一步。
小面积 flag:3126 2ND E(454sf)、774 27TH E(572sf)、
20 26TH E(668sf)laneway 嫌疑,逐案核产品身份。

## 3. 24 冲突 + 面积(P2)

- 24 条 TEMPORAL_CONFLICT 保持 `QUARANTINED_NOT_TEARDOWN`
  (售<yb,缺拆除证据,禁自动定性)
- 面积回填 821 条:二楼 406 / 地下室 773 / 单元前缀 5;
  均先入属性完善层,过身份验证后方可入估值

## 4. MPX 离线复核(P3)

340 项目 × folio/plan/arcgis/comps 离线关联:

| category | n |
|---|---|
| MATCHED_NO_SALE | 239(folio/同街 strata 存在,无单元成交) |
| NO_MATCH_IN_CURRENT_DATA | 101 |
| CONFIRMED_SALE | **0** |

**限定结论**:MPX 单元成交"当前捕获数据零确认"仍成立;
但措辞改为 NO_MATCH_IN_CURRENT_DATA——不排除存在未捕获交易。

## 5. 覆盖前后

| scope | before | after(条件) |
|---|---|---|
| HOUSE usable | 217 | 217(+7 候选待人工) |
| yb 确认率 | 50 | **+822**(786旧+12候选+24冲突) |
| MPX 单元成交 | 0 | 0 |

## 6. 最终回答

- **829 解决多少属性问题**:822/829(99.2%)身份+属性补齐;
  786 是旧房核销,24 是拆地候选,真实新建候选仅 12
- **实际净增可比成交**:0 自动 / ≤7 人工后
- **免费证据还能支撑什么**:HOUSE/DUPLEX 现有池(217/389)
  继续支撑区域基准;MPX 需逐项目人工收集开发商披露
