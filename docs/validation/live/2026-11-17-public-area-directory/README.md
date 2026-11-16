# 海外公开国家/地区目录 HTTP 结构观察（SP016）

- 时间：UTC 2026-11-16 23:24:13（Asia/Shanghai 2026-11-17 07:24:13）。
- 任务：Codex 的协议判断单（[SP016 request](../../../coordination/SP016/request.md)）；输入 main `81b86b4` 与门户 overseas doc 469（content SHA `fa599256d11f6097dc4dd429b1b1bc2f93f3431007b327ca2216f03633add4e8`）。**这是来源协议观察，不是 SDK 功能验收。**
- 请求：固定一次 `GET https://open.ezvizlife.com/api/lapp/area/list`；无凭据、无 query、无 body、无重试、不跟随 redirect；connect/read/write/pool timeout 各 10 秒，解压后响应上限 1 MiB；HTTPX `trust_env=False`，禁环境代理与隐式凭据。
- 结果：HTTP 200，`code` 为字符串 `"200"`（`known_success_code`）；`data` 为 **240 个 object**，每项 `id`、`telephoneCode` 为 int，`name`、`region` 为 string；未知 key 计数 0；耗时 1.029 秒。
- 记录纪律：只保存白名单的 HTTP/业务成功状态、字段结构与计数（[raw-http.json](raw-http.json)）；`code "200"` / HTTP 200 属已报告的成功状态。原始响应仅在进程内用于结构判断，**未保存**原始响应体，也未保存任何国家/地区字段的原始取值。

## 边界

- 该目录用于国家/地区查询；本次响应**未返回 `apiDomain`/`authDomain`**，不推断集群或账号归属；`open.ezvizlife.com` 只是当次文档声明的 origin。
- 仅一次匿名 HTTP 结构观察：不证明 SDK 方法可用、不代表设备或任何新功能完成；不承诺端点持续可达或数据仍然有效。
- 该能力已列入[海外审查候选](../../../capabilities/overseas-review-candidates.md)（`area/list`，SDK 入口仅 proposal）；今后 SDK 验收需在源码与签名冻结后对 sync/async 分别执行，本记录不提供不存在的 SDK 方法示例。

## 证据

- 结构化观察：[raw-http.json](raw-http.json)
- 派单与固定边界：[SP016 request](../../../coordination/SP016/request.md)
- 相关批次：[来源基础验收](../../2026-11-17-sdk-product-foundation/README.md)、[live 索引](../README.md)
