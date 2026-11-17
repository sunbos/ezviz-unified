# 国内设备语音能力审查候选（SP029）

本文件把 5 篇已核定的国内官方来源（`OPEN API/云接入/设备能力/设备语音能力`）逐场景审查整合为可审查能力候选。**这 5 篇只是本批审查输入，不是国内总量或 API 总数**；batch_1_A 其余 321 篇继续 `required_review`。机器可读清单见同目录 [`domestic-voice-review-candidates.json`](domestic-voice-review-candidates.json)。

## 边界

- 输入：SP027 安全投影（SHA 固定正文 + 数字 element 指针）、三路 Claude 候选（SP027A/B/C）、Codex 补证交叉核对；不含真实 locator、凭据或原始样例值。
- 唯一写文件：`docs/capabilities/domestic-voice-review-candidates.json` 与本文；未改 `src/`、`contracts/`、`evals/`、`tests/`、scope-register 或验收文件。
- 五个候选全部 `candidate:`，SDK facade/入口名仅为 proposal，待 Codex 统一冻结；本任务不授予 G0/G1，未执行任何 wire/参数/响应/重试/诊断自测或 live 调用。
- source 与 observation 均 `in_review`、`whole_source_reviewed=false`；example 仅经 Codex 安全结构核对（方法/路径模板/query 名集合/body 与 Content-Type 存在性），实例值未提供，不等同 live 验证。

## 事实层（官方来源确认）

| 候选 | 方法/路径 | 鉴权 | 请求参数（位置/必填） | 响应 |
| --- | --- | --- | --- | --- |
| `candidate:domestic:1259:d0` | GET `/api/route/voice/v3/devices/voices` | query token | accessToken、deviceSerial（query/Y） | meta + 顶层 `voiceInfos[]`（voiceId/voiceName/voiceUrl/status 0-2/time） |
| `candidate:domestic:1263:d0` | POST 同 path | query token | +voiceName、voiceUrl（query/Y） | meta + 顶层 `voiceInfo.voiceId` |
| `candidate:domestic:1273:d0` | PUT 同 path | query token | +voiceId(Int)、voiceName、voiceUrl（query/Y） | 仅 meta；**仅适用 CSEPMR511**（e3 原文限定本篇） |
| `candidate:domestic:1277:d0` | PUT `/api/route/alarm/v3/devices/{deviceSerial}/alarm/sound` | query token | serial 在 **path**；enable、soundType（query/Y，枚举 0-1 / 0-3）、voiceId（query/N，`soundType=3`时有效） | 仅 meta；错误表 200 + 111001–111011 |
| `candidate:domestic:1281:d0` | DELETE 同 voices path | query token | +voiceId(Int)、voiceName、voiceUrl（query/Y） | 仅 meta |

- 四篇 voices 共享同一 path、四个不同 HTTP 方法 → 四个独立 operation，不按 path 合并；1277 在独立 alarm 路由、serial 在 path。
- `meta.code` 文档 Int、成功值 200；`meta.moreInfo` 字段表 Object、示例 null —— 两者分别记录，不强制任一侧；无 `data` 层，不补造。
- 错误表仅 1277 含失败码：111005 原文仅“语音文件时长获取”（未完句，不补“失败”）；111011“通道号不能为空”但请求表无 channel —— 错误覆盖范围与显式参数的关系未解释，保留原文、不补 channel、不从错误码反推格式/时长限制。
- 五篇参数表均未声明 body 参数，且 Codex 核对五个请求示例均未显式带 body/Content-Type → **params-only、不额外设业务 Content-Type 是有交叉证据的 SDK 选择，不是官方永久禁止 body 的规范**。

## SDK 选择层（proposal，非事实）

- 统一 `device_voice` facade 提案：`list / create / update_name / delete / set_alarm_sound`（对应 `cn.voice.*`、`cn.alarm.prompt_sound.set` 仅为建议名）；与既有 `cn.otap.*`、lapp form 接口分域。
- accessToken 保持在 query，复用现有 query-token 传输（`src/ezviz_unified/operations.py:15`、`src/ezviz_unified/client.py:183`）；不为外观统一迁 header。
- voiceUrl 按不透明 locator 透传：不下载、不上传、不转码、不校验可达性、不落日志值；不给缺省值。
- 1273/1281 的必填 voiceName/voiceUrl 全部保留：不做 `rename(deviceSerial, voiceId, voiceName)` 或 `delete(deviceSerial, voiceId)` 精简签名。
- 1277：enable/soundType 建模为文档枚举 0/1 与 0/1/2/3；voiceId 保持 optional 并注明 `soundType=3` 时有效，不发明 `required_if`。
- 写操作（1263/1273/1277/1281）不自动重试/重放；不内置对 1259 的轮询编排；同步状态、配置状态与实际播放/告警效果分开验收。
- `voiceInfos[].status` 仅表示平台/设备语音同步状态（0/1/2），不得解释为播放质量或设备健康证明；`time` 单位未声明，原值透传不换算。
- 本域是服务端语音资源与告警配置；不构建播放器、转码器、上传存储或媒体引擎，不合并 Wi-Fi 提示音、麦克风、海外 chime/audition、门锁人脸语音提示开关。

## 未知与保留疑义

- **1273**：voiceUrl 在“改名”中的真实作用未解释 —— 不承诺仅元数据变化、非破坏性、可逆（Codex SUP-01 已修正三路原稿的 non-destructive 断言）。CSEPMR511 限定仅本篇，不传播给 1281 或其他 source；双 membership 不产生 OTAP 第二能力。
- **1263**：meta.code=200 + voiceInfo.voiceId 返回平台业务成功码与资源标识 —— 不写成“仅受理”、也不证明设备同步或播放成功；配额/费用/槽位未声明，记未知而非已知副作用。
- **1281**：删除范围（设备/账号库）、同步时序、被告警配置引用时行为、幂等与未找到响应、恢复语义全部未知；破坏性写，不等同可逆状态变化。
- **1259**：time 单位、分页/上限/排序未声明。
- **1277**：enable=0 时其余参数意义、是否即时发声、告警真实触发条件、voiceId 合法来源（1259/1263 仅为安全线索）均未知。
- 五篇均未声明最低权限、白名单、托管/子账户与型号范围（1273 除外）—— 记 `null` + 理由，不默认不限，也不因此自动否决全部 wrapper 设计。
- meta.moreInfo 的内部结构未投影；1263/1273/1281 错误表只列成功行，未知失败码仍可能出现，平台错误处理保留。

## 缺口与下一步

1. **SDK 缺口**：五个能力均 `implementation=missing`、registry 无绑定；`device_voice` facade 签名、参数位置与 `meta`（Int 200）/`voiceInfos`/`voiceInfo`/meta-only 三类响应映射需 Codex 冻结后再派实现 —— DTO 复用必须保留各自层级，不得暗造 `data`。
2. **验收设计**：offline 层按 `ACC-DOMAIN-OFFLINE-PER-NEW-001` 模板逐能力建 wire/参数/响应/重试/诊断 case；写操作挂 `ACC-WRITE-OPERATIONS-001` 类受控设计；1259 需新建 bounded domestic read 实例，不继承已有 inspection live 成功。
3. **live 阻塞**：所有读/写场景 `not_verified`/`not_authorized` —— 需国内账号 + 绑定设备（1273 须 CSEPMR511）、voiceUrl 自有托管资源、明确授权、次数/预算与恢复/还原方案；本文件不提供任何授权。
4. **未来案例建议**：create→list 状态联动（需先裁定 1263 与 1259 status 的关系证据）、delete 前置状态捕获 + 恢复演练、1277 `soundType=3` + 合法 voiceId 的条件场景；各自作为独立待裁定场景，不自动串联。

## 链接

- 机器可读清单：[`domestic-voice-review-candidates.json`](domestic-voice-review-candidates.json)
- 设计依据：`docs/plans/2026-11-17-capability-scope-register.md`
- 既有契约对照：`contracts/reviewed-transport-contracts.json`、`contracts/sdk-bindings.json`
- 验收政策：`docs/validation/sdk-product-acceptance.md` / `.json`（`ACC-DOMAIN-OFFLINE-PER-NEW-001`、`ACC-WRITE-OPERATIONS-001`）
- 海外对照布局：[`overseas-review-candidates.json`](overseas-review-candidates.json)
