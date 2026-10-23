# Northshore Property Watchdog — Agent 持久规则

> 本文件记录用户明确要求的工作规则。任何报告页/审读卡/渲染产物生成时必须先读此文件。

## 审读卡 / 报告页生成(强制)

1. **自动预填**: 卡组必须携带 auto annotations(`build_card_context(auto=…)`),
   不允许生成无 auto 预填的卡。
2. **价格必须更新**: 卡片/列表展示的 Asking 必须是 watchlist `latest_asking`;
   冻结旧卡必须注入价格变动横幅(`scripts/refresh_stale_cards.py`,
   横幅放 `</header>` 之后,不得插在 header flex 内)。展示价与 ROI 输入价
   不一致是不可接受缺陷。
3. **JSON 导入导出**: 索引页必须有「导出全部」与「导入」;卡片必须有
   「导出本套 JSON」。专审卡组另需内嵌 `PRICE_REVIEW` 数据 +
   「导出审查数据 JSON」按钮(见 `render_price_review_cards.py`)。
4. **风格一致**: 复用 `build_calibration_packet` / `strategy_v2_calibration`
   的卡片模板与样式,不得自造风格;链接 Realtor.ca 只作辅助不替换卡片。

## 服务与链接

- **禁止临时端口**: 不要随手 `python -m http.server` 开端口发链接,
  也不要把不相关的监听端口当文件服务(如 :18789 是 OpenClaw
  WebSocket 网关,SPA 回退造成假 200,务必核对返回内容)。
- 常驻文件服务: **`python -m http.server 8766 --bind 127.0.0.1`**
  从仓库根 serve 全树(含 .json)。新产物链接格式:
  `http://localhost:8766/reports/calibration/<dir>/index.html`
- 若 8766 未运行: `cd 仓库根 && python -m http.server 8766 --bind 127.0.0.1 &`

## V3s / 估值边界(不可违反)

- V3s 生产估值、成本档、法规规则、DUPLEX stable 瀑布、已有 ROI **零修改**;
  一切修正走 shadow/补丁文件 + 人工批准,零回写。
- 检出价格异常 ≠ 自动调价;`PASS` 公式正确 ≠ 估值可信;
  `LOW_EVIDENCE` 独立成档,不与错误混算。
- 影子估值写 `reports/…/shadow_impact.csv` 等产物,待批。
- 房源下架/方案移除归 `OUT_OF_SCOPE`,不算 `RESOLVED`。

## 数据与证据

- 一手来源优先:REALTOR.ca/MLS 合规导出 > BCA/ArcGIS > 官方开放数据;
  禁用绕反爬。
- GIS parcel 面积 ≠ 法定地块;无 PID/面宽的 arcgis 记录不进 lot 匹配池。
- `gst_basis`、`new_first_sale` vs `resale`、strata/非 strata、
  单元价 vs 整栋价口径分列,不混池。
- 成交数据新鲜度必须标注截点(当前成交截 2026-07-08 附近);
  历史均价不得声称是当前现价。
- `data/research/raw/`、`data/research/normalized/`、`reports/` 在
  .gitignore:受限/大体量数据留本地,仓库只交代码+文档+聚合清单。

## 验证与交付

- 每次给可复跑命令 + 自动化断言;幂等、守恒、去重要有测试。
- GitHub: `xiwei9871/Vancouver-housing-market-research`;
  工作分支推远端,PR 用 gh(未登录时给出手动建 PR 链接)。
- 最终回复须含:分支/commit SHA/报告路径/覆盖汇总/复现命令/测试结果/
  未解决缺口。
