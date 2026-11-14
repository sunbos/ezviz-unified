# 快速开始：设备列表只读示例

SDK 尚未发布 PyPI，请在仓库根目录用 Python 3.12+ 从源码安装：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e .
```

## 环境变量

示例只读取下列变量，`.env` 不会自动加载；变量缺失、空白或非法时会在发请求前以 `config_error` 退出：

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

本批示例尚未做真实联网验收。仓库离线测试范围：`tests/test_examples.py` 用 `MockTransport` 覆盖同步/异步、国内/海外的成功、空列表、异常载荷、认证失败、配置校验与脱敏输出；由主导方运行后记录结果，不据此宣称真实网络已验收。
