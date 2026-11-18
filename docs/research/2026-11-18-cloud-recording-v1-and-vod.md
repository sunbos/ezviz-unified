# 云录制 1.0 文件操作与云点播 VOD 契约

本批依据 2026-11-18 冻结计划（[SP122](../plans/2026-11-18-cloud-recording-v1-vod.md)）审核云录制 1.0 三个文件接口与云点播一个列表接口，同步入口为 `client.cloud_recording_v1` 与 `client.vod`，异步同名。全部仅国内有据，未完成真实调用验证。

| 方法 | 官方文档 | 请求与主要规则 |
| --- | --- | --- |
| `cloud_recording_v1.unfreeze_file` | [5341](https://open.ys7.com/help/5341)、[1380](https://open.ys7.com/help/1380) | POST 表单；projectId/fileId/accessToken 同处 body；写操作不重放 |
| `cloud_recording_v1.file_info` | [1375](https://open.ys7.com/help/1375) | GET query；projectId/fileId/accessToken 同处 query；只读 |
| `cloud_recording_v1.download_urls` | [1377](https://open.ys7.com/help/1377) | GET；accessToken 在 Header，projectId/fileId 在 query，可选 contentType/expireSeconds；只读 |
| `vod.files` | [5342](https://open.ys7.com/help/5342)、[4187](https://open.ys7.com/help/4187) | GET；accessToken 在 Header；fileNodeIds/spaceId/spaceName 任选至少一项；只读 |

四项共享 `meta.code`/`meta.message`/`data` 信封与 `meta.code=200` 成功值。`vod.files` 的 `fileNodeIds` wire 取 4187 明确 CSV 形态；`data` 为 ARRAY，不 flatten 为 `data.result`。

## 使用示例（占位，未经实测）

下列示例仅演示入口形状，不代表已对这些资源做过真实调用。`SYNTHETIC_PROJECT`/`SYNTHETIC_FILE`/`SYNTHETIC_NODE_*` 是合法但明显合成的占位值：它们的格式可通过客户端校验，但**必须**替换为本人已授权访问的现成 projectId/fileId/fileNodeId 才能真实成功。环境变量仅示例如何从本地读取凭据，示例不打印其值、不自动执行 unfreeze、不打印 mediaURL/raw。

```python
import os
from ezviz_unified import Client, RegionConfig

with Client(
    os.environ["EZVIZ_APP_KEY"],
    os.environ["EZVIZ_APP_SECRET"],
    region=RegionConfig.china(),
) as client:
    info = client.cloud_recording_v1.file_info(
        "SYNTHETIC_PROJECT", "SYNTHETIC_FILE"
    )
    urls = client.cloud_recording_v1.download_urls(
        "SYNTHETIC_PROJECT", "SYNTHETIC_FILE", expire_seconds=3600
    )
    vod_page = client.vod.files(
        file_node_ids=["SYNTHETIC_NODE_A", "SYNTHETIC_NODE_B"]
    )
```

异步示例（两只读入口，`asyncio.run` 入口）：

```python
import asyncio
import os
from ezviz_unified import AsyncClient, RegionConfig


async def main() -> None:
    async with AsyncClient(
        os.environ["EZVIZ_APP_KEY"],
        os.environ["EZVIZ_APP_SECRET"],
        region=RegionConfig.china(),
    ) as client:
        info = await client.cloud_recording_v1.file_info(
            "SYNTHETIC_PROJECT", "SYNTHETIC_FILE"
        )
        vod_page = await client.vod.files(
            file_node_ids=["SYNTHETIC_NODE_A", "SYNTHETIC_NODE_B"]
        )


asyncio.run(main())
```

## 来源事实与 SDK 选择

**来源事实**（官方文档明示）：

- 5341 声明 unfreeze 幂等；异步解冻完成时间通常 1–5 分钟且不构成 SLA。SDK 不因幂等声明而自动重放（写操作）；仅 HTTP 成功且业务 meta.code=200 的响应证明触发已被受理，不证明解冻完成——HTTP 200 本身可能携带业务失败，不能单独据此推导。
- 1375 返回 `fileSize`（bytes）、`duration`（seconds）、`expireTime`（String，格式/时区 unknown）；无 `unfreezeTime` 字段，不从 `status=0`/`storageType=2`/`meta.code=200` 推断可下载。
- 1377 要求企业版，归档文件需已解冻；`file_id` 上限 128、`expireSeconds` 区间 `60..604800`、服务端默认 7200 均为来源事实（上限另见 1380）。旧 download 路由不推荐但未证实删除，本接口不宣称覆盖该变体；SDK 不代替调用方探测或准备资源。
- 5342/4187：`fileNodeIds` wire 采用 4187 明确的 CSV 形态；至少一个选择条件；`spaceId` 服务端优先级最高；5342 明确不支持托管/子账号。`data` 为 ARRAY。
- 4444 是另一个 POST 分页端点，3974 是 HLS 端点，统一存储契约是第三套；三者均不混入 `vod.files`。

**SDK 选择**（非官方明示事实）：

- unfreeze/download 的 `project_id` 限定 ASCII `[A-Za-z0-9_]{1,32}`（对官方 `\w` 正则引擎的解释，可能拒绝服务端支持的 Unicode）。
- `file_id` 接受常规 ASCII word `1..32`（字符集解释为 SDK 选择），或 `E1` 前缀 opaque 沿用来源 max128；不透明值不 normalize/strip。
- 所有新文本参数要求 UTF-8 可编码、非空白，`space_id`/`expire_seconds` 要求 int 非 bool——均为本批防御选择。
- `content_type` 为非空白 UTF-8 str 原样 query 透传，五种列举值不封闭；文档未声明排他不等于官方保证任意 contentType 均可服务端成功。
- `vod.files` 的 `file_node_ids` 拒 comma（CSV 无转义的 SDK 设计选择）、`space_id` 允许 0/负数（无官方正数约束）、`space_name` 不套 32/CJK 限制；SDK 原样发送全部供给项，不推导其它组合优先级，不猜账号身份。

## 响应字段摘要（原样保留，未强制 DTO）

下表仅整理官方文档中已声明的字段名与类型；`requiredness`/partial result/URL 实际过期行为均 unknown，`ApiResponse` 原样返回、不强制结构、不补缺省。

| 入口 | 字段 | 来源声明类型/单位 | 备注 |
| --- | --- | --- | --- |
| `file_info` | `fileSize` | bytes | — |
| `file_info` | `duration` | seconds | — |
| `file_info` | `expireTime` | String | 格式与时区 unknown |
| `download_urls` | `data.expire` | Long | 单位 unknown |
| `download_urls` | `data.urls` | 数组 | 官方示例元素为 string，但未声明强制同构 |
| `vod.files` | `data` | ARRAY | 顶层即为数组，不 flatten |
| `vod.files` | `folderNode` | String | — |
| `vod.files` | `fileName` | String | — |
| `vod.files` | `fileType`/`fileChildType` | 类型未声明 | — |
| `vod.files` | `fileSize`/`totalSize` | Int | 单位 unknown |
| `vod.files` | `storageType` | Int | 取值 1/2 |
| `vod.files` | `uploadStatus` | Int | 取值 0/1/2 |
| `vod.files` | `createTime`/`expireTime` | 毫秒时间戳 | — |
| `vod.files` | `coverPic`/`coverPicUrl` | String | — |
| `vod.files` | `urls` | Object map | — |

5342 与 4187 的错误码表各自独立且不完整，不把这些差异映射成 SDK 的 HTTP 层规则。

## 权限与隐私

`unfreeze_file` 为写操作，真实调用需具体文件、次数与费用范围/恢复限制授权，本批未执行；`file_info`/`download_urls`/`vod.files` 需已授权现成资源与账号权限，禁止为验收创建/上传/下载媒体。`projectId`/`fileId` 已加入脱敏键集合；返回的 mediaURL 仅在 `data`/`raw` 供调用方使用，`ApiResponse` repr/诊断不含，但不保证任意服务器自由文本中的未知 URL 被通用脱敏覆盖。ID==10002 单独验证原 code 分类及读重放不变。

## 官方生态复用依据

按[官方生态复用边界](2026-11-15-official-ecosystem-reuse.md)的选择顺序核查：播放/编解码由官方 EZUIKit 等组件承担；本批只提供服务端文件元数据与下载地址获取，属于官方仓库未提供的 Python 服务端薄封装，不重复造播放或下载器。**在已核查材料范围内**——Node SDK 仅导出 Auth、Java 仓库当前无全量 REST 源码——未发现覆盖本四项接口的现成 Python 生态组件；该结论不排除生态其它角落存在实现，仅指本次审核过的官方与常见第三方来源。故按既有 `Client.request`/`Operation`/`ResponsePolicy` 底座独立实现，不新增运行时依赖。

## 验证状态

四个入口当前为**已实现、离线验收已通过、live 未验证**：定向脱敏回归已扩展 `projectId`/`fileId`，诊断登记 133→137 入口与相应 operation 白名单已更新。registry/binding/wire case 后置集成与离线一致性已在 commit `b251b11` 上通过——证据见 [../validation/2026-11-18-cloud-files/checks.json](../validation/2026-11-18-cloud-files/checks.json)（说明 [README](../validation/2026-11-18-cloud-files/README.md)）：全量 5448+35 subtests、新 lint、mypy 45、wire 208 contracts/1696 executions、诊断 137 入口 sync/async body 274（诊断覆盖 pytest 278）、cloud-wire 190、Vod 145、云 behaviour+表单 312、构建/产物/Twine/干净 wheel+sdist 安装均过，sdist 3371 passed 10.39s（均为已执行测试单元/已登记契约，非覆盖率）。

四条 OFFLINE required case（`5341`/`1375`/`1377`/`5342`）`recorded_evidence`+`passed`。注意区分两类状态：**四条 LIVE required case** 的状态是 `awaiting_resource_confirmation`/`awaiting_authorization`（未执行，非 `not_verified`）；而四个入口在 `sdk-bindings.json` 中的 **`binding.live_status` 为 `not_verified`**——`not_verified` 是 binding 的 live_status，不等于 case 状态。`file_info`/`download_urls`/`vod.files` 缺已授权现成资源与权限确认，`unfreeze_file` 为写操作需单独授权。保留失败回归（测试夹具修正与 oversize parser 初始化 bug）不作废；本批未发真实云请求。不宣称已覆盖旧 download 路由变体，不推广到海外或托管/子账号。
