# Free Source Usage Review — 各源许可边界核查

日期 2026-10-23。用途前提:个人购房/重建决策,非商业。

## 各源许可状态

| 源 | 访问方式 | 许可判断 | 本项目用法 |
|---|---|---|---|
| BCA ArcGIS 公共图层(AFP_*/MapServer) | 页面自带 gistoken,公开 query | 公共地图服务自带 token;批量拉取属灰色——已采快照保留作审计,**不再扩大抓取** | 仅复用既有快照 |
| BCA Assessment Search 详页(PropertyInformation) | 逐户查询,个人免费 | **ToS 明确限制自动抓取/合并重组**;本次 829 回填属一次性技术验证(已完成、已存档),**后续默认停止自动采集** | 存量结果作审计层;新增需求走人工查询队列 |
| CoV 官方开放数据(permits/tax/parcel) | 开放许可(CC-BY 类) | ✅ 明确允许下载处理 | 常规输入 |
| REALTOR api2 | 公开挂牌接口 | 仅要价数据;在售列表用途 | watchlist 来源 |
| Zolo/REW/Zealty/HouseSigma | 页面含 sold 展示 | 403/登录墙 + ToS 禁抓取 | **仅人工逐案核查**,不入管线 |
| LTSA | 法定登记 | 按条计费 | 抽样,需逐案批准 |
| MLS/GVR sold 导出 | 授权数据 | 需经纪协议 | 降级为可选补充 |

## 合规决定(本轮生效)

1. **冻结 BCA 网站端点的自动化扩展**:829 已完成结果存档为
   `bca_yb_backfill_results.csv`(本地);后续 yb/面积需求改为
   **人工查询队列**(`manual_review_queue`),用户手动查后贴回。
2. BCA arcgis 快照只增量复用(月度官方刷新时手工触发);
   不再新增字段维度抓取。
3. `bca_yb_backfill.py` 保留在仓库但标注 **one-shot audit tool,
   frozen**;测试不依赖它联网。
4. 收费源(LTSA/MLS/BCA Data Advice)保留 BLOCKED 待批状态。

## 替代维护路径(合规)

| 需求 | 合规通道 |
|---|---|
| 新成交 | arcgis 层月度快照增量(官方层更新即重跑) |
| yb/面积补齐 | 人工查询队列 + 用户粘贴结果;或 BCA 正式开放数据集(目前无) |
| MPX 单元 | 开发商公开页人工收集(逐项目) |
| 法定产权 | LTSA 按案(批准制) |
