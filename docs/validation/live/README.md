# 真实联调记录索引

按观察时间与区域汇总已授权账号的真实只读验收。默认`pytest`离线运行，本索引不构成全部API的真实覆盖，也不新增写操作授权。日期为观察时间，不声称设备目前仍在线；早期空列表与失败记录保留为历史，不代表最新全部状态。

| 观察日期（UTC） | 区域 | 验证范围 | 结论要点 | 记录 |
| --- | --- | --- | --- | --- |
| 2026-11-13 | 国内 | 认证、普通设备列表 | 认证成功、列表0条，仅限当次令牌可见范围；当时未完成海外联调 | [README](2026-11-14/README.md) |
| 2026-11-14 | 国内 | 单设备基础只读 | 普通列表本页1条（page_size=1，非总数）、托管本页0条；信息、通道、状态查询成功（默认通道1） | [README](2026-11-14-domestic-device/README.md) |
| 2026-11-14 | 国内 | 单设备只读诊断 | 能力集与版本查询成功，不证明对讲、布防或升级可用；存储状态与容量未执行 | [README](2026-11-14-domestic-diagnostics/README.md) |
| 2026-11-14 | 新加坡 | 认证、设备列表 | 默认入口业务码10017未通过；显式`auth_base_url`认证成功、列表为空，仅作该账号证据 | [README](2026-11-14-overseas/README.md) |
| 2026-11-14 | 国内 | 单设备只读检查模式 | tools/live `--inspection`对首台设备固定五项只读逻辑调用（信息、通道、状态、能力、版本，通道1）均返回策略内成功；单次同步样本，无全字段结构校验、健康推断或写/存储/媒体操作 | [README](2026-11-15-domestic-inspection/README.md) |
| 2026-11-15 | 新加坡 | 匿名公开时区目录（HTTP + SDK sync/async） | 无凭据 POST `/api/lapp/timezone/list` 三次 HTTP 200 且业务码 200、各返回 105 项；SDK 匿名 `settings.list_timezones` sync/async 各一次成功；仅覆盖该集群公开目录，不证明设备端点或硬件 | [singapore.json](2026-11-15-public-timezones/singapore.json) |
| 2026-11-16 | 国内 | 诊断 v1 单设备只读验收 | 首页 `page_size=1` 首台普通设备、通道 1；`devices.list` + `inspect_device` 固定五项共 6 个业务逻辑调用、认证另 1 次 HTTP 合计 7 次，响应策略全部成功；诊断报告 64 事件、`dropped_events=0`、`recording_errors=0`；仅单次同步样本，不代表异步/CLI/其他型号 | [README](2026-11-16-diagnostics-v1/README.md) |
| 2026-11-16 | 海外（公开 origin 单次） | 匿名公开国家/地区目录 HTTP 结构观察 | 对 `open.ezvizlife.com` 单次匿名 `GET /api/lapp/area/list`：HTTP 200、`code "200"`、`data` 为 240 个 object（每项 `id`/`telephoneCode` int、`name`/`region` string，未知 key 0）；仅保存字段结构与计数、不存原始取值。这是来源协议结构观察，不是 SDK 方法或设备验收 | [README](2026-11-17-public-area-directory/README.md) |

各批次均未执行写操作、设备控制、媒体获取或存储介质变更；结论只针对当次账号、设备与入口，不含密钥或设备身份细节。检查模式批次观察时刻为UTC 2026-11-14 23:12:31（Asia/Shanghai 2026-11-15 07:12:31）；记录目录`2026-11-15-domestic-inspection`按本地Asia/Shanghai日期命名，故目录日期较表中UTC观察日期晚一天。新加坡公开时区批次观察时刻为UTC 2026-11-15 04:23–04:57（含SDK两次调用）；诊断 v1 批次观察时刻为Asia/Shanghai 2026-11-16 21:24:44（UTC 2026-11-16 13:24:44）。海外公开国家/地区目录批次观察时刻为UTC 2026-11-16 23:24:13（Asia/Shanghai 2026-11-17 07:24:13，记录目录按本地日期命名）；其为对公开 origin 的一次匿名 GET，raw HTTP 不是 SDK 入口、不构成设备或 SDK 功能验收。本索引不构成全部 API 的真实覆盖。
