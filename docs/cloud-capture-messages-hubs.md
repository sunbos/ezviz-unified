# 云抽帧、消息订阅与HUB子设备指南（仅国内）

> 面向SDK使用者；全部8个业务入口（云抽帧3+消息2+HUB3，sync/async同名成对）仅在 `RegionConfig.china()` 下可用，海外调用在请求发出前抛 `ValueError`。三个域均为新增接口，未做真实调用验证，不能宣称已实测或发布；验收证据以[验收登记](validation/sdk-product-acceptance.json)与[能力登记](capabilities/README.md)中对应 `candidate:domestic:*` 行为准。

## 配置与示例

示例在源码checkout内运行（安装wheel不会带 `examples/` 目录）：

```sh
python -m examples.cloud_capture --help
python -m examples.message_subscriptions --help
python -m examples.hub_discovery --help
```

先在安全的本地环境中配置下方列出的变量（值留在本地环境，不写进命令行、脚本、日志或 stdout）；随后只需三条 `python -m` 命令：

```sh
python -m examples.cloud_capture --mode sync
python -m examples.hub_discovery --mode sync
python -m examples.message_subscriptions --mode sync --execute
```

- 前两条是只读查询，在已授权账号下运行；`--mode` 可换 `async`。
- `message_subscriptions` 必须显式带 `--execute` 才发请求；其 `consume` 会在服务端自动提交上一批已读消息（at-least-once，属于副作用动作），仅限已获授权的账号执行。不带 `--execute` 时不发任何请求。

确切配置项（仅列变量名，不要打印或记录其值；不要在命令行内联密钥或示例账号/设备 ID）：

- 通用：`EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET`、`EZVIZ_REGION`（须为 `domestic`）、`EZVIZ_AUTH_BASE_URL`（国内拒绝）
- 云抽帧：`EZVIZ_CLOUD_TASK_ID`
- 消息：`EZVIZ_MESSAGE_GROUP`（取值 `group1`..`group5`）
- HUB：`EZVIZ_HUB_SERIAL`、`EZVIZ_SUBDEVICE_SERIAL`

## client.cloud_capture（抽帧任务）

`create_interval_task` 按时间窗+间隔抽帧，`create_timing_task` 按显式时间点列表抽帧——两者是互斥选项，不要串联调用。`task` 是只读查询，路径为 `/api/v3/open/cloud/task/{taskId}`：

```python
task = client.cloud_capture.task(task_id)            # taskId 需是 str
task.data.get("taskStatus")                          # 0完成/3结束是不同终态
```

- 创建是显式写动作：可能产生计费、需要账号对设备和存储（`projectId`/`resultSpaceId` 至少传一个）有授权。文档1388（interval）明示非幂等，1389（timing）未作幂等声明；SDK对两者都不自动重放，网络失败重发前请自行核对服务端状态。
- interval 与 timing 的同名参数校验不同：interval 的 `projectId` 限 ASCII `[A-Za-z0-9_]{1,32}`、`resultSpaceId >= 1`，timing 仅要求非空/整数；`devProto` 在 interval 可多取 `jt808`，timing 只有 `""`/`gb28181`；`frameModel=2` 在文档1388/5324间表述冲突，SDK按文档化取值放行、不回退0/1。
- `task` 是默认的只读查询命令（CLI也只调用它）；`taskId` 保存在本地变量，`tag_id=task_id` 是官方指南建议的跨域关联用法，不是SDK字段推导；`search_files` 选择器优先级为 `file_node_ids` > `tag_id` > `space_ids`（服务端解析，非AND过滤），文件节点 `fileNodeId` 需人工检查返回页确认文件身份后再传入 `download_urls`。
- 查询成功不证明媒体已生成或可播放；`None` 参数一律不发送。

## client.message_subscriptions（消息队列）

```python
created = client.message_subscriptions.create_consumer("group1")
consumer_id = created.data["consumerId"]
batch = client.message_subscriptions.consume(consumer_id, offset=None)
```

- 官方文档已标注消息服务不再更新（接口仍可用）；`create_consumer` 与 `consume` 仍是有副作用的显式动作：`consume` 在服务端自动提交上一批已读消息（at-least-once），失败的重投策略由服务端决定，SDK不会自动重放或补提交。
- 没有单独的最终确认调用，示例结果里 `final_commit_confirmed` 恒为 `False`；末批是否被服务端确认无SDK侧证据。
- `examples.message_subscriptions` 的 `run_sync`/`run_async` 程序接口逐批把原始 `ApiResponse` 交给 `handler`，完成后才发下一次 `consume`，正常结束返回 `MessageRunResult`；`run_async` 接受并等待返回awaitable的回调（须解析为 `None`），`run_sync` 拒绝协程函数/返回awaitable的handler，两种模式都拒绝生成器/异步生成器handler。
- `consumerId` 是不透明字符串，只存局部变量使用；`group` 固定五选一，不自动选择。

## client.hubs（HUB子设备，三个POST表单只读查询）

```python
listed = client.hubs.list_subdevices(hub_serial, version=None)      # 父序列号
info = client.hubs.subdevice_info(subdevice_serial)                 # 子序列号
chans = client.hubs.channels(subdevice_serial)                      # 子序列号
```

- 身份严格区分：`list_subdevices` 发父 `deviceSerial`（可选 `version`），另两个发子 `deviceSerial`；SDK不从父列表推断子序列号、不改写/拼接序列号。
- 三个端点都是POST表单提交（不是GET）的只读查询；`list_subdevices` 的 `version` 与 `subdevice_info`/`channels` 需要的子设备 `Get`/`dev` 权限等最小授权声明由调用方按账号配置自行满足，SDK不代为声明或检查。
- 序列号须为非空UTF-8且不含小写ASCII字母，SDK不代转大写。
- 示例按 list→info→channels 至多三次调用、显式给两个序列号；`identity_consistency` 只比对返回的 `deviceSerial`，不证明硬件配对正确。

## 示例输出与边界

- `cloud_capture --mode sync|async`：查询 `EZVIZ_CLOUD_TASK_ID` 一次，只打印 `{status, mode, logical_calls, task_state}` 白名单摘要。
- `message_subscriptions --mode ... --execute`：创建一消费者、消费一批，打印 `schema_version/outcome/stage/batches_received/messages_observed/final_commit_confirmed` 等固定字段。
- `hub_discovery --mode sync|async`：打印 `subdevice_count/channel_count/identity_consistency`。
- 所有示例CLI只输出上列白名单字段，原始SDK响应由程序调用方持有；凭据、`task_id`、`consumer_id`、序列号留在局部变量，不写入日志或stdout。
- 错误边界：消息示例参数/配置错退出码2、执行失败1、中断130；`cloud_capture`/`hub_discovery` 参数与配置错合并为类别化失败、退出码1。原始字段名与平台错误码见源码校验与诊断脱敏说明。
