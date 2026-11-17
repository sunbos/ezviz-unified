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

## SDK 实测证据（2026-11-17，独立于上方 SP016 记录）

上方 SP016 是一次性 raw HTTP 结构观察，不是 SDK 测试，也不被继承为 live 成功。`ACC-OS-PUBLIC-AREAS-SDK-001` 的真实 SDK 验收在实现完成后单独执行，证据为 [sdk.json](sdk.json)（报告 SHA `88cce20d448f196d6b9cfd7986e12186173a656ff53b2a3c9a7abfbf2bac7adc`）：

- 时刻：UTC 2026-11-17T02:52:27.820528；SDK code commit `58e7881a3e7aee4c3ffc431b5016b019cefb447c`，scope_revision `2026-11-17-r1`，cohort `PUBLIC-AREAS-001`。
- 调用：匿名 `Client` 与 `AsyncClient` 各一次 `settings.list_areas()`，固定 `https://open.ezvizlife.com`（文档声明 origin，无 cluster 推导），合计 2 HTTP / 2 逻辑调用；无凭据、不跟 redirect、不重试、不访问设备，每阶段 timeout 10s、解压响应上限 1 MiB，client/transport 均正确关闭。
- 结果：两 mode 均 HTTP 200 + 业务码 `"200"`；`data` 为 240 项 list，全部 240 项 `id:int`/`name:str`/`region:str`/`telephoneCode:int` 类型匹配；identity 编码、原始响应字节每 mode 16442 字节；46 项输入 pin 与 code58 Git 版本一致。
- 记录纪律：回执只保存计数、字段类型聚合、状态、来源与代码 hash，不含原始字段值；240 为当次观察值而非固定目录大小或保证。
- `does_not_verify` 明确：海外账号设备、集群路由、物理硬件、POST 变体、压缩响应路径、平台消息接入与发布均不在本次证据范围内；仅 GET + identity 协商被实测。
