# 云录制 1.0 / 云点播 VOD 实施冻结计划（SP122）

> 执行分工：Claude Code 经 Magpie 固定 `devin/swe-2`、effort=max，按本文件的唯一文件范围实现；Codex 冻结合同、审查和验收。本文件本身不是实现或实测通过证据。

**Goal：** 在 SDK 中新增云录制 1.0 文件操作（解冻、详情、官方下载地址）与云点播 VOD 文件列表共四个国内入口，独立 facade、复用既有请求/诊断/脱敏底座。

**输入：** 提交 `e71f8dc15a6e1f0a682c9908cde56d7f501d73b2`，scope_revision 输入 `2026-11-18-r10-webhook-local-acceptance`。Codex 依用户 Goal 架构职责批准本批离线实现；真实资源执行不在授权内。全产品范围不收缩；本批不是全 Goal 完成。

**Architecture：** 独立 `cloud_recording_v1` / `vod` facade，复用 `Client`/`AsyncClient.request`、`Operation`、`ResponsePolicy`、`ApiResponse`、diagnostic scope、既有认证重放/异常/脱敏。不增加依赖/DTO/后台任务/自动分页/轮询/下载；不改现有 storage/cloud_recording 2.0/HLS/transport/harness driver。

## 1. 来源事实

官方 URL 统一 `https://open.ys7.com/help/{doc_id}`；sha256 仅指归档 HTML 内容，不是运行时数据匿名化。

| doc_id | sha256 | 对应契约 |
| --- | --- | --- |
| 5341 | `d0b6a93e3d2c9402d35e1ae4295b52ad938f96f1f69084e797866e0707cdbe85` | unfreeze 主源 |
| 1380 | `2704bbb84f3a6bb4b12e2a80c58604d901bdf7a2680c8530e657c8242c8a21d0` | unfreeze 辅源（fileId 上限 128） |
| 1375 | `7a09f9bdf9e901131d51d2e1abd810fd6c849bb8f7e570f6c17da02e1ab0bd6e` | file_info 主源 |
| 1377 | `dc16cf7660def0688bb7acaff11dd5e1db002b4599f4ae366d84c744276ecd7a` | download_urls 主源（expireSeconds/128） |
| 5342 | `12e5fe8a0d7b1896d17ce37296c9f1c0e209d7cafd3b4721d51e6013e5448271` | vod.files 主源 |
| 4187 | `3c0b4c0352b5734d93da68c227c2f28e593ccd79b216635a57fdd4a6f821e0ea` | fileNodeIds CSV 辅源 |
| 4444 | `1c1dce9247e41a2c0046fefc2c308712baebc98d30db21bff85f4e8071b77990` | 另一 POST 分页端点（明确不混入） |

## 2. 冻结公共接口

```python
class CloudRecordingV1 / AsyncCloudRecordingV1:  # client.cloud_recording_v1
    def unfreeze_file(self, project_id: str, file_id: str) -> ApiResponse: ...
    def file_info(self, project_id: str, file_id: str) -> ApiResponse: ...
    def download_urls(
        self,
        project_id: str,
        file_id: str,
        *,
        content_type: str | None = None,
        expire_seconds: int | None = None,
    ) -> ApiResponse: ...

class Vod / AsyncVod:  # client.vod
    def files(
        self,
        file_node_ids: Sequence[str] | None = None,
        *,
        space_id: int | None = None,
        space_name: str | None = None,
    ) -> ApiResponse: ...
```

异步方法原样 `async def`；校验在 `await` 执行体开始后、认证前完成，不构造新的预 await 包装。

## 3. 正式契约（四项仅 domestic）

scope 先地区后参数，`Operation.regions` 也限定 `domestic`。响应 policy 均为 `meta.code` / `meta.message` / `data`，`success_values=(200,)`。只使用已有响应解析：不增加 data 类型强制/DTO，不改变现有 code 标量归一或异常分类语义。`ApiResponse` 返回原对象/raw/data，保留额外未知字段，不补 requiredness/缺省。

| # | operation_id | Operation.name | method/path | token_location | read_only | body |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `cn.cloud_recording_v1.unfreeze_file` | `cloud_recording_v1.unfreeze_file` | POST `/api/open/cloud/v1/file/unfreeze` | form | False | form_urlencoded flat body `accessToken`/`projectId`/`fileId` |
| 2 | `cn.cloud_recording_v1.file_info` | `cloud_recording_v1.file_info` | GET `/api/open/cloud/v1/file` | query | True | 无 body，不注入 Content-Type |
| 3 | `cn.cloud_recording_v1.download_urls` | `cloud_recording_v1.download_urls` | GET `/api/service/cloudrecord/file/official/download` | header | True | 无 body，不注入 Content-Type；query `projectId`/`fileId` 可选 `contentType`/`expireSeconds` |
| 4 | `cn.vod.files` | `vod.files` | GET `/api/service/open/vod/files` | header | True | 无 body，不注入 Content-Type；query 可选 `fileNodeIds`/`spaceId`/`spaceName` |

逐项要点：

1. **unfreeze_file**（主 5341，辅 1380）：源声明幂等但 SDK 写操作不重放。仅 HTTP 成功且业务 meta.code=200 的响应证明触发已被受理，不证明解冻完成；HTTP 200 本身可能携带业务失败，不能单独据此推导。异步完成状态 unknown，通常 1–5 分钟不是 SLA。
2. **file_info**（主 1375）：`fileSize` 单位 bytes，`duration` 单位 seconds，`expireTime` 为 String 但格式/时区 unknown；无 `unfreezeTime` 字段定义，不从 `status=0`/`storageType=2`/`meta.code=200` 推断可下载。
3. **download_urls**（主 1377）：企业版明确要求；归档文件需要已解冻；SDK 不探测或准备资源。旧 download 路由不推荐但未证实删除，不宣称已覆盖该变体。
4. **vod.files**（主 5342，辅 4187）：`fileNodeIds` wire 采用 4187 明确 CSV。至少一个选择条件。`spaceId` 服务端最高优先级但 SDK 原样发送所有供给项，不推导其它组合优先级。5342 明确不支持托管/子账号，SDK 不猜账号身份。`data` 为 ARRAY：不 flatten 为 `data.result`，不混入 4444 另一 POST 分页端点、3974 HLS 端点或统一存储契约。

## 4. 参数验证（下列 SDK 选择均已标注，不能写成官方事实）

- 复用 `storage_models._text`/`_integer` 纯机械 helpers；VOD 复用 `_node_ids(limit=50)`，不复制规则、不改 storage_models。
- 所有新用户文本须 UTF-8 可编码且非空白；异常 message 只含参数名/规则，不含输入值。UTF-8 校验为本批 SDK 防御选择，不改其它入口。
- unfreeze/download 的 `project_id`：ASCII `[A-Za-z0-9_]{1,32}`。官方 `\w` 引擎未定义，ASCII 是 SDK 选择并披露可能拒绝服务端支持的 Unicode。
- file_info 的 `project_id`：仅非空白 UTF-8 string，不沿用 32 或字符集上限。
- 三项 `file_id`：常规 ASCII word `1..32`（字符集解释为 SDK 选择），或 `E1` 前缀 opaque 且总长度 ≤128；`E1` 本身允许（无 min33）；`E1` 后可含 `$` 等，不套 ordinary word 字符限制。max128 为来源事实（1380/1377）；保留不透明值，不 normalize/strip。
- download `expire_seconds`：`60..604800` 含端点区间与官方默认 7200 均为来源事实，默认由服务端承担；SDK 选择为 None 省略及 int 非 bool 校验。`content_type`：None 省略，否则非空白 UTF-8 str 原样 query 透传；五种列举值不是明确排他集合，不封闭 enum、不注入默认、不当 HTTP header——但文档未声明排他不等于官方保证任意 contentType 均可服务端成功。
- VOD `file_node_ids`：None 省略；非 string/bytes 的 Sequence，`1..50` 项，每项非空白 UTF-8 str，拒 comma（SDK CSV 无转义设计选择），在 auth 前快照，顺序/重复保持。无 ID 长度/字符集猜测。`space_id`：None 省略，int 非 bool，允许 0/负数（无官方正数约束）。`space_name`：None 省略，否则非空白 UTF-8 str，不套 32/CJK 字符集限制。三个均 None 拒绝。

## 5. 隐私与脱敏

- 最小增加 `errors._SENSITIVE_KEYS` 的 `projectId`/`fileId`，让现有 transport 自动收集并遮蔽已知值；不改 transport 算法。VOD 字段已有覆盖（`fileNodeIds`/`spaceId`/`spaceName` 在册）。
- 测试长 synthetic sentinel 请求 ID/token 在 error repr/str、diagnostic JSON、SDK 自动日志不可见，wire 保留。返回 `mediaURL` 只在 data/raw 供调用方使用；`ApiResponse` repr/诊断不可含；不保证任意自由服务器错误 message 里的未知 URL 已被通用脱敏。
- ID==10002 单独验证原 code 分类及读重放不变，不做短串盲扫误判。不得 hash 标识伪匿名化。

## 6. 冻结验收

每入口 sync+async 精确 method/path/位置/content-type/body/None 省略/服务端默认不注入；读仅 existing invalid-token 一次刷新重放、写不重放，普通网络/业务/HTTP 错误不重放；正常/空 data/未知字段/metadata 原样、返回及异常 identity；所有无效类型/边界/海外/UTF-8 前置失败，provider/factory/HTTP 均 0；关闭、async 取消、超时传播；diagnostic on/off 结果与 wire/请求次数顺序一致，facade scope 覆盖 validation/auth/transport/business/cancel/closed。

新入口登记：`business_entrypoints` 133→137，方法体 266→274；`Operation.name` 及 `cn.operation_id` 白名单均加，exact 计数断言更新不删除。四条 registry/binding/wire case 后置集成 204→208（CN 137→141，OS 67 不变）。GET registry `body_encoding=unknown`（无 none 枚举），form token parameter `location=body`。`offline_status` 在真实测试前必须 `pending`/`not_verified`，不预写 `passed`；`live_status=not_verified`。

**本批真实 live 全部未执行：** file_info/download/vod 需已授权现成资源及账号权限，禁止为验收创建/上传/下载媒体；unfreeze 为写操作，需具体文件、次数、费用范围/恢复限制授权，不执行。官方 `unfreezeTime`/expire 单位/错误层级/ID regex dialect 等缺项明确保留。

## 7. 文件权限与派单

本任务（接线/诊断/脱敏/文档）唯一可写文件：

- `src/ezviz_unified/client.py`（无条件 `self.cloud_recording_v1=...`、`self.vod=...`，sync/async）
- `src/ezviz_unified/__init__.py`（导出 `CloudRecordingV1`/`AsyncCloudRecordingV1`/`Vod`/`AsyncVod`）
- `src/ezviz_unified/errors.py`（`_SENSITIVE_KEYS` 追加 `projectId`/`fileId`）
- `tests/test_sdk_transport.py`（现有参数化 ID 回归加两键）
- `src/ezviz_unified/_diagnostic_registry.py`（四 entrypoint + 八 operation name）
- `docs/diagnostics-v1-coverage.json`（同步四 entrypoint + 八 operation name）
- `tests/test_harness_diagnostic_coverage.py`（精确 133→137 断言）
- `docs/diagnostics.md`、`README.md`（数量/入口/边界更新）
- `docs/plans/2026-11-18-cloud-recording-v1-vod.md`（本文件）
- `docs/research/2026-11-18-cloud-recording-v1-and-vod.md`（研究记录）

A/B 业务模块（`cloud_recording_v1.py`、`vod.py`）与其测试属独立派单，不在本文件权限内；契约/matrix/binding/wire case 的后置集成亦单独派单。不修改现有 HLS/storage/2.0 模块，不广泛重写 README 或历史证据。

## 8. 授权命令与执行记录

本任务唯一授权的 Bash 命令（原样执行）：

```sh
.venv/bin/python -m pytest -q --tb=short tests/test_sdk_transport.py
.venv/bin/python -m ruff check src/ezviz_unified/client.py src/ezviz_unified/__init__.py src/ezviz_unified/errors.py src/ezviz_unified/_diagnostic_registry.py tests/test_sdk_transport.py tests/test_harness_diagnostic_coverage.py
.venv/bin/python -m ruff format src/ezviz_unified/client.py src/ezviz_unified/__init__.py src/ezviz_unified/errors.py src/ezviz_unified/_diagnostic_registry.py tests/test_sdk_transport.py tests/test_harness_diagnostic_coverage.py
```

执行结果：见本文件末尾「执行记录」节；工具未执行的项不写作 passed。

常规测试交给 Claude；Codex 只做关键审查与验收，不以模型自述算通过。代码完成后定向回归、全部已登记 wire、诊断审计、全量 pytest、Ruff/mypy、构建安装、PR/main CI 按新提交验收。全 Goal 继续包含其它能力和真实验收/PyPI 门槛。

## 执行记录

- 接线（client 无条件 facade、`__init__` 导出、`_SENSITIVE_KEYS` 追加 `projectId`/`fileId`、registry 与 coverage JSON 133→137/八 opnames、harness 断言 133→137）、`diagnostics.md`/`README.md` 边界更新与本文件、`docs/research/2026-11-18-cloud-recording-v1-and-vod.md` 已写入。
- 已执行授权命令：`pytest -q --tb=short tests/test_sdk_transport.py` → 59 passed（共享 transport 脱敏回归，不等同四个新入口验收通过）；`ruff check` 与 `ruff format`（上述文件清单）→ all checks passed / 6 files unchanged。
- 未执行项保持 pending：四条 registry/binding/wire case 的后置集成（204→208）、全量 pytest、mypy、构建安装、A/B 模块自身的验收测试与全部真实 live（本批不授权）。
