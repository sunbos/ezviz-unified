# 快速开始：设备列表只读示例

SDK 尚未发布 PyPI，请在仓库根目录用 Python 3.12+ 从源码安装：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
```

## 环境变量

设备列表与单设备检查示例都读取下列变量，`.env` 不会自动加载；变量缺失、空白或非法时会在发请求前以 `config_error` 退出（单设备检查额外的目标变量见下文）：

- `EZVIZ_APP_KEY`、`EZVIZ_APP_SECRET`：必填，原样传入 SDK。
- `EZVIZ_REGION`：仅 `domestic` 或 `overseas`。
- `EZVIZ_AUTH_BASE_URL`：仅海外可选，用于显式覆盖认证域名。只要该项被设置就必须有效：为空、纯空白或非法 URL 都会被拒绝；国内只要设置该项（包括空值）同样拒绝，不会被悄悄忽略。海外不设置时使用 SDK 默认域名 `open.ezvizlife.com`。

国内（从海外切回时需先清除覆盖项，已设置的 `EZVIZ_AUTH_BASE_URL` 会被拒绝而非静默忽略）：

```bash
export EZVIZ_REGION=domestic
unset EZVIZ_AUTH_BASE_URL
```

海外（新加坡账号实测可用入口如下，其他海外区域以官方文档为准，该入口不代表所有区域通用）：

```bash
export EZVIZ_REGION=overseas
export EZVIZ_AUTH_BASE_URL=https://isgpopen.ezvizlife.com
```

## 运行

凭据不要写进命令行参数。环境变量已导出时，在仓库根目录直接运行：

```bash
.venv/bin/python -m examples.devices_sync
.venv/bin/python -m examples.devices_async
```

没有现成环境变量且在有 TTY 的交互终端中，可用下面的多行临时启动器（在仓库根目录运行，示范 sync；跑异步把模块名改成 `examples.devices_async` 即可）。凭据经 getpass 输入，只进入当前进程环境，不回显、不出现在 shell 命令参数里；非交互场景（无 TTY）getpass 不可用，应改用预先导出的环境变量或 secret 注入：

```bash
.venv/bin/python - <<'PY'
import getpass
import os
import runpy

os.environ["EZVIZ_APP_KEY"] = getpass.getpass("EZVIZ_APP_KEY: ")
os.environ["EZVIZ_APP_SECRET"] = getpass.getpass("EZVIZ_APP_SECRET: ")
runpy.run_module("examples.devices_sync", run_name="__main__")
PY
```

启动器自身不设置 `EZVIZ_REGION` 等变量，需先按上文导出。导入示例模块不读配置、不联网。`main()` 只发起一个“读第一页设备列表”的逻辑调用（`page_start=0, page_size=1`，不自动分页）；SDK 可能按已声明规则自动刷新 token 并重试，不要假设全程只有一次认证加一次业务请求。

## 单设备检查

`examples.inspection_sync` 与 `examples.inspection_async` 对一台已知设备按固定顺序发起五次只读逻辑调用：设备信息、通道、状态、能力、版本。除上文变量外还需：

- `EZVIZ_DEVICE_SERIAL`：必填，原样传入 SDK，不打印、不出现在任何输出中；国内要求不含 ASCII 小写字母，示例不会代为转大写。
- `EZVIZ_CHANNEL`：可选，默认 `1`；一旦设置必须是 ASCII 有符号十进制整数（`2`、`+1`、`-1` 合法；空白、`true`、`1.0`、全角数字等均拒绝）。示例不发明通道取值范围，越界与否由设备侧判定。

在仓库根目录运行：

```bash
.venv/bin/python -m examples.inspection_sync
.venv/bin/python -m examples.inspection_async
```

五个调用是顺序的非原子观察，逐项结果之间不保证同一时刻快照；某项失败会被记录并继续，认证级错误（`AuthenticationError`）立即中断剩余调用。这是五个逻辑调用而非固定五次 HTTP 请求：SDK 的 token 刷新与 10002 重发可能增加实际请求数。示例自身不重试、不列设备、不做媒体操作、不自动猜测区域。

stdout 只输出一行 `public_summary()` JSON：`region`、`all_succeeded` 与逐项 `operation`/`status`（失败项另含 `error_type` 及合法 `code`/`http_status`），不含序列号、通道、响应载荷或异常原文。退出码：`0` 全部成功；`2` 部分失败（摘要照常输出到 stdout）；`1` 配置错误或 SDK 级致命错误（stderr 输出上文同款脱敏失败 JSON）。该示例 CLI 本身未做真实联网验收；其调用的只读检查助手已于UTC 2026-11-14 23:12:31（Asia/Shanghai 2026-11-15 07:12:31）经 tools/live 检查模式在国内单设备完成一次真实验收（同步、通道1，记录见 `docs/validation/live/2026-11-15-domestic-inspection/`），不代表示例模块或异步、海外路径已实测。

## 结果与排错

成功：stdout 一行 JSON。`returned_items` 是本页条数而非设备总数；`0` 只表示该分页为空，不能证明账号没有设备，也不能排除设备在其他分页、区域或托管范围之外。HTTP 200 也不等于业务成功：响应体中的业务错误码仍按失败处理。

失败：stderr 一行 JSON（`status=failed`）。`error_type` 为 `config_error`、SDK 异常类名或 `unexpected_response`；输出不含异常原文、响应体、域名或凭据，业务 `code` 仅在纯 ASCII 数字时保留，`http_status` 仅在取到整数时输出（`TransportError` 等传输层错误可能没有，不保证所有错误都带 `http_status`）。

按类别排查：

- `config_error` + `field`：配置问题。对应变量缺失、空白、非法，或该区域不允许该覆盖项；修正后重跑。
- `AuthenticationError`：认证失败（HTTP 401 或 `code=10002`）。核对 key/secret 与 `EZVIZ_REGION` 是否匹配。
- `code=10017`：通常归类为 `ApiError`，是认证端返回的业务失败而非认证异常；核对 key 及账号区域。
- `code=10002`：token 失效相关错误，不是“通用错误”。SDK 可能已按规则刷新 token 并重试；若持续失败，先核对令牌来源、账号与区域是否一致，不要直接当成权限问题。
- `PermissionDeniedError`：权限问题。核对账号对接口和设备的授权范围。
- `ProtocolError`：协议/上游问题，响应不符合协议约定；重试或核对 SDK 版本。
- `TransportError`：网络问题，连接、超时等传输层失败。
- `unexpected_response`：返回结构不是预期的设备列表；核对 SDK 版本。

SDK 内置的 `ProtocolError`、`TransportError` 都继承 `EzvizError`，会被示例捕获并脱敏输出；配置错误与 SDK 错误之外的真实程序错误不会被吞掉，会原样抛出。

## 本地诊断示例

`examples.diagnostics_sync` 与 `examples.diagnostics_async` 演示 `DiagnosticRecorder`：recorder 显式传入 `Client`/`AsyncClient` 的 `diagnostic_recorder`，`recorder.capture()` 作用域覆盖配置校验、客户端构造与一次只读首页查询（`page_start=0, page_size=1`），`finally` 中把 `recorder.to_json()` 打印到 stdout。环境变量与设备列表示例相同；示例不自动写文件、不上传，保存报告需显式重定向：

```bash
.venv/bin/python -m examples.diagnostics_sync > report.json
.venv/bin/python -m examples.diagnostics_async > report.json
```

成功与失败 stdout 都输出同一份诊断报告 JSON（`schema_version=1`）；stderr 仅输出固定纯文本提示，不含异常 message 或 traceback。退出码 `0` 成功、`1` 失败（含未预期的 `BaseException`/`SystemExit`）、`130` 表示 `KeyboardInterrupt`/`CancelledError`；上述转换发生在示例 CLI 的 `run_cli` 边界，SDK 与 capture 异常本身原样传播。报告不含凭据、token、设备序列号、URL、异常原文或自由文本；字段白名单、覆盖与未覆盖范围见[本地诊断说明](diagnostics.md)。该示例 CLI 尚未做真实联网验收。

## 匿名公开目录

公开时区目录与上文设备示例不同，不需要`EZVIZ_APP_KEY`/`EZVIZ_APP_SECRET`；设备查询、设备设置和单设备检查仍必须认证。最小同步示例（无凭据、显式新加坡业务地址）：

```python
from ezviz_unified import Client, RegionConfig

with Client(
    anonymous=True,
    region=RegionConfig.overseas(api_base_url="https://isgpopen.ezvizlife.com"),
) as client:
    response = client.settings.list_timezones()
    zones = response.data
```

`anonymous=True`时不允许应用凭据、token provider或auth_transport；`api_base_url`必须显式给出，SDK不猜测地域。匿名客户端调用需认证接口会在请求前抛出`ValueError`。`language`仅接受`"ENGLISH"`/`"SIMPCN"`，省略由服务端默认。该目录目前只有新加坡地址经SDK真实查询验证（返回105项，见[记录](validation/live/2026-11-15-public-timezones/singapore.json)），其他海外区域未验证；字段约束见[海外设备设置与公开目录](research/2026-11-15-overseas-settings.md)。

公开国家/地区目录`client.settings.list_areas()`需要独立的匿名客户端，文档固定地址为`https://open.ezvizlife.com`，与上方时区示例无关：

```python
from ezviz_unified import Client, RegionConfig

with Client(
    anonymous=True,
    region=RegionConfig.overseas(api_base_url="https://open.ezvizlife.com"),
) as client:
    response = client.settings.list_areas()
    areas = response.data
```

每次调用恰好一次GET `/api/lapp/area/list`，无query、无body、无token；`.data`为原始目录数组，文档字段为`id`/`name`/`region`/`telephoneCode`，额外字段与空数组原样保留，SDK不推导cluster/`apiDomain`/`authDomain`。源同时声明POST但其形状未文档化，SDK只实现GET。该方法已于UTC 2026-11-17经同步/异步各一次匿名调用在上述固定`open.ezvizlife.com`地址完成真实验收（均HTTP 200、业务码"200"、data为240项数组且四字段类型匹配，identity编码、客户端正确关闭；回执见[SDK实测记录](validation/live/2026-11-17-public-area-directory/sdk.json)）。该验收仅限固定origin的GET：其他海外origin、POST变体、压缩响应路径、集群路由与任何设备行为均未实测，240是当次观察值而非固定目录大小；细节见[公开国家/地区目录](research/2026-11-17-public-areas.md)。

CLI示例（固定`open.ezvizlife.com`，无凭据、无env读取、无文件写入）：

```bash
.venv/bin/python -m examples.public_areas          # sync
.venv/bin/python -m examples.public_areas --async  # async
```

## 联调与参考

深度联调（源码脚本，按路径运行，不是已安装命令）：

```bash
.venv/bin/python tools/live/verify_devices.py --serve --include-diagnostics
```

- [仓库 README](../README.md)
- [官方生态复用调研](research/2026-11-15-official-ecosystem-reuse.md)
- [国内实测：同步单设备](validation/live/2026-11-14-domestic-device/README.md)
- [海外实测：新加坡同步](validation/live/2026-11-14-overseas/README.md)
- [tools/live 说明](../tools/live/README.md)

## 验证状态

本批示例尚未做真实联网验收（只读检查助手经 tools/live 在国内单设备实测过一次，观察时刻为UTC 2026-11-14（Asia/Shanghai 2026-11-15），该路径不经过示例 CLI，记录见 `docs/validation/live/2026-11-15-domestic-inspection/`）。匿名公开目录示例同理：`examples/public_areas` 的 CLI 路径未单独联网验收，但其调用的 `settings.list_areas` 方法本身已于 2026-11-17 在固定 `open.ezvizlife.com` 经 sync/async 各一次真实验收（见上文与 `docs/validation/live/2026-11-17-public-area-directory/sdk.json`）。仓库离线测试范围：`tests/test_examples.py` 用 `MockTransport` 覆盖同步/异步、国内/海外的成功、空列表、异常载荷、认证失败、配置校验与脱敏输出；`tests/test_examples_inspection.py` 离线覆盖单设备检查示例的两个区域、逐项失败、认证中断、配置前置校验与脱敏输出；`tests/test_examples_diagnostics.py` 离线覆盖诊断示例的报告结构、失败仍导出报告、应用异常捕获、脱敏与不自动写文件。由主导方运行后记录结果，不据此宣称真实网络已验收。
