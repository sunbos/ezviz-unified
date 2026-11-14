# 来源与独立交接

本产品采用独立发行名`ezviz-unified`与导入名`ezviz_unified`，不提供旧utils兼容层。SDK、测试、文档工具和维护harness从[原开发提交6f9a3ea](https://github.com/sunbos/ezviz-openapi-utils/tree/6f9a3eaf8147434f67304a3b3d314492c1c5f846)筛选交接，保留MIT许可证与署名。

新仓库重新建立产品历史，不导入旧utils实现、旧发行标签或方法迁移清单。旧项目历史保留在[已归档仓库](https://github.com/sunbos/ezviz-openapi-utils)，原开发PR已关闭且未合入旧utils主线。候选稿、终端日志和个人会话不进入新仓库。

## 必要证据

- `contracts/`保留官方来源URL、地区、正文hash、协议差异及验证状态。
- `docs/validation/reviews/`保留契约登记与研究说明实际引用的20份原始审核记录，内容不改写；其中历史数量描述对应当时批次，不是当前覆盖率。
- `docs/validation/live/`保留脱敏真实调用结果。国内与新加坡仅认证/空列表成功，设备能力未验证。
- 文档数据集保持本地存放并被Git忽略；来源为官方门户和其明确引用的静态资料，采集与校验方法见[采集与校验说明](../tools/documents/README.md)。

交接不会把源缺项改成完整内容，也不把离线评测当真实设备验证。运行SDK只依赖HTTPX；旧仓库、原始文档数据、模型服务均不是运行依赖。独立仓库的安装、测试、静态登记、合成评测和发行检查单独执行，验证记录与代码提交对应。

## 交接验收

独立环境通过1743项测试、11个子测试、31个SDK文件的mypy、92文件Ruff、165契约/1352次模拟执行，wheel/sdist干净安装及sdist中的1481项SDK测试均通过。[初始公开代码的四版本CI](https://github.com/sunbos/ezviz-unified/actions/runs/43586451222)对应提交c7ca421；这些是交接时的批次数据，后续以当前checks为准。

新仓库已公开，旧仓库已归档。该步骤未发布PyPI或调用设备接口；后续继续离线开发，真实设备能力保持未验证。
