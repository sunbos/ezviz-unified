# 官方组件版本与复用证据（SP028）

核查日期：2026-11-17。输入基线 `641d6cc10010b11a49c65c9d6783f92eefda00d3`，仅整理已审核公开版本元数据；本记录不是第二份维护结果数据库，不改 scope-register/acceptance，不实现 SDK/adapter/引擎。生态复用边界沿用 [official-ecosystem-reuse](2026-11-15-official-ecosystem-reuse.md)。

## 证据分级口径

以下五类事实各自独立、不能互相等同：

1. **仓库 HEAD commit**：观察时刻公开仓库默认分支指针，不代表任何发行版本。
2. **GitHub release/tag**：`latest release` 端点观察；`prerelease=false` 是 GitHub 标志位，不改称 stable，beta 字样原样保留。
3. **package.json 声明**：HEAD 或 tag 处包内声明的 name/version/license，属包自述，不等同 registry 发行物。
4. **registry 元数据**：HTTP 200 取回的公开元数据（resolved_version、git_head、integrity、sha1、license 声明），是 registry 侧的声明，tarball 字节未核验前不算发行物冻结。
5. **tarball 字节核验**：对下载字节核算 sha256/integrity/sha1 并确认包身份；本批 7 项全部通过（历史观测曾仅 1 项通过，属已被后续受控取回取代的旧结论），详见下文。

`license` 字符串与随包 LICENSE 文件只是声明/文件证据，不代表已做兼容性许可决定；`license=null` 不等于无许可。未安装、未执行任一上游包。

## 仓库版本事实表（11 个公开仓库）

「source 指针」指该仓库在已归档资料中的来源定位（region/doc_id/element_index 或仓库内文档行号），属版本资料谱系，不是 API 覆盖声明。

| 仓库 | HEAD commit | GitHub latest release | 包声明（名称@版本，license） | source 指针 |
| --- | --- | --- | --- | --- |
| `Ezviz-OpenBiz/EZUIKit-JavaScript-npm` | [`9c367b2a3fc05844cdfff4d41043df76c8d26d9f`](https://github.com/Ezviz-OpenBiz/EZUIKit-JavaScript-npm/commit/9c367b2a3fc05844cdfff4d41043df76c8d26d9f)（master） | [`v9.0.26`](https://github.com/Ezviz-OpenBiz/EZUIKit-JavaScript-npm/releases/tag/v9.0.26)（2026-11-06，prerelease=false，commit [`1f6f475dd0d050934ff6b698e40fe344cb2fcc8b`](https://github.com/Ezviz-OpenBiz/EZUIKit-JavaScript-npm/commit/1f6f475dd0d050934ff6b698e40fe344cb2fcc8b)，3 assets） | HEAD：`ezuikit-js@9.0.27-beta.2` ISC；release：`ezuikit-js@9.0.26` ISC | domestic 3711 e148；4294 e46 |
| `Ezviz-OpenBiz/ezviz-openplatform-sdk-nodejs` | [`84b629afb6657c97b04c379bb5ed49d7ac90515e`](https://github.com/Ezviz-OpenBiz/ezviz-openplatform-sdk-nodejs/commit/84b629afb6657c97b04c379bb5ed49d7ac90515e)（main） | [`1.0.0-beta.1`](https://github.com/Ezviz-OpenBiz/ezviz-openplatform-sdk-nodejs/releases/tag/1.0.0-beta.1)（2025-02-13，prerelease=false，commit [`84b629afb6657c97b04c379bb5ed49d7ac90515e`](https://github.com/Ezviz-OpenBiz/ezviz-openplatform-sdk-nodejs/commit/84b629afb6657c97b04c379bb5ed49d7ac90515e)，0 assets） | `@ezviz/openplatform-sdk@1.0.0-beta.1` Apache-2.0 | ecosystem-reuse 文档行13 |
| `Ezviz-OpenBiz/ezviz-openplatform-sdk` | [`447e3ade7cbf9ef04f3f68831732d7e071423533`](https://github.com/Ezviz-OpenBiz/ezviz-openplatform-sdk/commit/447e3ade7cbf9ef04f3f68831732d7e071423533)（master） | latest release 端点未找到（未证明无 tag/release；tag 集合端点不可用 INVALID_ARGUMENT） | — | ecosystem-reuse 文档行14 |
| `Ezviz-OpenBiz/OpenCodeSnippets` | [`0ea82bd4b3fbbe8ae0340dd52af97cdf2f17d28a`](https://github.com/Ezviz-OpenBiz/OpenCodeSnippets/commit/0ea82bd4b3fbbe8ae0340dd52af97cdf2f17d28a)（main） | 同上，端点未找到/不可用 | — | ecosystem-reuse 文档行15 |
| `Ezviz-OpenBiz/EZUIKit-MultiScreen` | [`94be7ba25141c9bd2e5aff68a36b0c06e8b6c45a`](https://github.com/Ezviz-OpenBiz/EZUIKit-MultiScreen/commit/94be7ba25141c9bd2e5aff68a36b0c06e8b6c45a)（main） | 同上 | `@ezuikit/multi-screen@0.1.0` MIT（HEAD） | domestic 5109 e31 |
| `Ezviz-OpenBiz/EZUIKit-flv` | [`1e033a4dd8f2bcd38651274f5c3c52f2cc34fcb3`](https://github.com/Ezviz-OpenBiz/EZUIKit-flv/commit/1e033a4dd8f2bcd38651274f5c3c52f2cc34fcb3)（master） | 同上 | `ezuikit-flv@2.1.2-beta.2` license=null（HEAD） | domestic 1852 e25 |
| `Ezviz-OpenBiz/EZUIKit-player-hls` | [`558d8cba74f6cfc4205d88899ce00083ce18ef67`](https://github.com/Ezviz-OpenBiz/EZUIKit-player-hls/commit/558d8cba74f6cfc4205d88899ce00083ce18ef67)（main） | [`v2.0.0`](https://github.com/Ezviz-OpenBiz/EZUIKit-player-hls/releases/tag/v2.0.0)（2026-07-23，prerelease=false，commit [`ae5dd3699d69f6175e8085fc47454ad9e317c558`](https://github.com/Ezviz-OpenBiz/EZUIKit-player-hls/commit/ae5dd3699d69f6175e8085fc47454ad9e317c558)，3 assets） | `@ezuikit/player-hls@2.0.1-beta.3` license=null（HEAD） | domestic 3704 e12 |
| `Ezviz-OpenBiz/EzOtaSDK-Android` | [`601d8ab83ddaa7434d6eedb89d46c49cca8e247f`](https://github.com/Ezviz-OpenBiz/EzOtaSDK-Android/commit/601d8ab83ddaa7434d6eedb89d46c49cca8e247f)（main） | latest release 端点未找到；tag 集合端点不可用 | — | domestic 1715 e119 |
| `Ezviz-OpenBiz/UniEzOpenSDK` | [`3d54cf3dd1fda6118eea8a190e55633af7a9d70a`](https://github.com/Ezviz-OpenBiz/UniEzOpenSDK/commit/3d54cf3dd1fda6118eea8a190e55633af7a9d70a)（main） | [`v1.0.7`](https://github.com/Ezviz-OpenBiz/UniEzOpenSDK/releases/tag/v1.0.7)（2025-09-09，prerelease=false，commit [`a7a8ba795c0604405279884af6cb72e607b5a482`](https://github.com/Ezviz-OpenBiz/UniEzOpenSDK/commit/a7a8ba795c0604405279884af6cb72e607b5a482)，0 assets） | — | domestic 4486 e22 |
| `Ezviz-OpenBiz/WeChat-miniApp` | [`727dc49ca80f17ef0ce6a31e2bf1a94a8cc77c15`](https://github.com/Ezviz-OpenBiz/WeChat-miniApp/commit/727dc49ca80f17ef0ce6a31e2bf1a94a8cc77c15)（main） | latest release 端点未找到；tag 集合端点不可用 | — | domestic 4237 e83 |
| `Ezviz-OpenBiz/ertc-web-demo` | [`bcccc4848b0406b51dc7afb29d712304d4af0266`](https://github.com/Ezviz-OpenBiz/ertc-web-demo/commit/bcccc4848b0406b51dc7afb29d712304d4af0266)（main） | latest release 端点未找到；tag 集合端点不可用 | — | domestic 1874 e171；5406 e23 |

所有 11 仓库均为 public、未 archived；`material_archive_status` 均为 `metadata_observed_only_repository_content_not_frozen_as_source_set`——只观察到元数据，仓库内容未冻结为 source 集。

## 版本口径差异（不得静默合并）

| 主题 | 差异 | 处置 |
| --- | --- | --- |
| `ezuikit-js` | 已冻结门户正文（domestic 4295 e3）声明标题 `v9.0.1`（源文件 SHA-256 `ddbf700818d4ad8fbe700acd0fdc9baba8a864c2c26f018d9afeab99ab50697c`）；GitHub latest release `v9.0.26`；HEAD 包声明 `9.0.27-beta.2` | 三个证据通道、三个版本。不静默替换已冻结门户正文，不把 HEAD 等同 release |
| `@ezuikit/player-hls` | GitHub latest release `v2.0.0` vs HEAD 包声明 `2.0.1-beta.3` | release tag/resolved commit 与开发 HEAD 分别固定 |
| `@ezviz/openplatform-sdk` | tag 拼写 `1.0.0-beta.1`，GitHub `prerelease=false` | tag 拼写与标志位都保留，不改标为 stable 产品发行 |

## registry 元数据（7 包，全部 HTTP 200，仅元数据）

观察时刻 `2026-11-17T00:37:41Z`，7 次请求全部 `metadata_observed`、`tarball_downloaded=false`、`installed=false`。版本与 hash 均为 registry 元数据声明；`latest` 解析结果只是当次观察的钉值，不自动采纳。

| 包 | 请求版本/tag | resolved | git_head | license 声明 | 元数据 SHA-256（raw） |
| --- | --- | --- | --- | --- | --- |
| `ezuikit-js` | `9.0.26` | `9.0.26` | `1f6f475dd0d050934ff6b698e40fe344cb2fcc8b` | ISC | `d4de61fa62527f4b11cac8f60de4eb662f34abadc2f84390f66cd551dee57172` |
| `@ezviz/openplatform-sdk` | `1.0.0-beta.1` | `1.0.0-beta.1` | `84b629afb6657c97b04c379bb5ed49d7ac90515e` | Apache-2.0 | `cb51d9b34574cdbab0f7da3f6c4b9253e611e5fa4c9741d779667a89ad03aed1` |
| `@ezuikit/player-hls` | `2.0.0` | `2.0.0` | `ae5dd3699d69f6175e8085fc47454ad9e317c558` | null | `e2eed18ace2ebd573c8fc7b0e9e8822a770a2ebf035f79a613f425c62504c363` |
| `@ezuikit/multi-screen` | `0.1.0` | `0.1.0` | `1d4448a1ac626e67d039b5dffff740e2f1c132f1` | MIT | `09abda0849c1f772f0f1475cb7de901a826838f828944595cbd15163bb721c71` |
| `ezuikit-flv` | `2.1.2-beta.2` | `2.1.2-beta.2` | `1e033a4dd8f2bcd38651274f5c3c52f2cc34fcb3` | null | `3f95413028508a8341b7d6010bc20c65219c31dd42fb52e93ece9e0d5053f91f` |
| `ertc-web` | `latest` | `2.1.2` | null | ISC | `295e22d192855b8ffe705568612403c4ae33884fe851f262ee329229146652c6` |
| `@ezuikit/utils-logger` | `latest` | `2.1.0` | null | MIT | `8b464ed0be68a1b67855720b0e7c2e45482a10ff3e107fe159cec12666e15df3` |

各条 integrity（sha512）与 sha1 见输入 manifest `data/official-components/2026-11-17/npm-metadata/manifest.json`（SHA-256 `ee8fd597506c7a8f51d82b091e9c79e07dce0f979d0be305d7f260716fbab2b9`）；本表不复制完整摘要串。

registry `git_head` 与对应公开仓库 commit 的关系**逐项**核对如下，不互相推导：

- `ezuikit-js@9.0.26` git_head=`1f6f475dd0d050934ff6b698e40fe344cb2fcc8b` = `v9.0.26` resolved commit（**匹配 release tag**，非 HEAD `9c367b2a…`）。
- `@ezviz/openplatform-sdk@1.0.0-beta.1` git_head=`84b629afb6657c97b04c379bb5ed49d7ac90515e` = 观察到的仓库 HEAD，同时 = tag `1.0.0-beta.1` resolved commit（二者本就同 commit，**匹配**）。
- `@ezuikit/player-hls@2.0.0` git_head=`ae5dd3699d69f6175e8085fc47454ad9e317c558` = `v2.0.0` resolved commit（**匹配 release tag**，非 HEAD `558d8cba…`）。
- `@ezuikit/multi-screen@0.1.0` git_head=`1d4448a1ac626e67d039b5dffff740e2f1c132f1` **不同于**观察到的仓库 HEAD `94be7ba25141c9bd2e5aff68a36b0c06e8b6c45a`（该仓库 latest release 端点未找到，无可比 tag）。
- `ezuikit-flv@2.1.2-beta.2` git_head=`1e033a4dd8f2bcd38651274f5c3c52f2cc34fcb3` = 观察到的仓库 HEAD（**匹配**；该仓库 latest release 端点未找到）。
- `ertc-web@2.1.2` git_head=null，未声明仓库对应关系（**不可比**）。
- `@ezuikit/utils-logger@2.1.0` git_head=null，registry 声明仓库为 `Ezviz-OpenBiz/EZUIKit-player-utils`；git_head 为空即**无 commit 可比**，不能称与仓库 HEAD 一致。

## tarball 字节核验结果

**当前事实：7 个钉版发行物的 archive 字节完整性与 package 身份全部核验通过**——每个完整 artifact 均记录 SHA-256，并分别与原官方 metadata 的 SHA-512 integrity 和 SHA-1 校验值核对一致，包名/版本身份匹配。逐项证据（artifact sha256、字节数、repo 相对归档路径、证据 manifest 及其 SHA-256、成员数、retrieval route）见不可变快照 [`docs/capabilities/official-component-artifacts-2026-11-17.json`](../capabilities/official-component-artifacts-2026-11-17.json)。**未安装、未执行任何上游包；核验只读取 tarball 字节与归档成员清单。**

| 包@版本 | artifact SHA-256 | 字节数 | 归档路径（repo 相对） | retrieval route |
| --- | --- | --- | --- | --- |
| `ezuikit-js@9.0.26` | `3f7275b0fd2d07cc0f02eff7a318ec7fa2cb05c1aae9c84c65ead49a301947b6` | 8874656 | `data/official-components/2026-11-17/npm-mirror-resumed/…947b6.tgz` | npmmirror cache，对照官方 npm 核验 |
| `ezuikit-flv@2.1.2-beta.2` | `4f5d247f1a1c0dbeec439c4fcde1bba5067f5a55d16d9f7368a0b9ca570601fc` | 1192055 | `data/official-components/2026-11-17/npm-flv-final-bounded/…601fc.tgz` | npmmirror cache，对照官方 npm 核验 |
| `@ezuikit/player-hls@2.0.0` | `8f7de1d2c96d310025e4d72a1221176d2584f5c98b6fc858dfd9daccca5087c9` | 1182065 | `data/official-components/2026-11-17/npm-mirror-resumed/…087c9.tgz` | npmmirror cache，对照官方 npm 核验 |
| `ertc-web@2.1.2` | `22b4a0b380511bcbf3148437192a2092c86d195ec32e82eb69d07a41856f53e9` | 522404 | `data/official-components/2026-11-17/npm-mirror-resumed/…f53e9.tgz` | npmmirror cache，对照官方 npm 核验 |
| `@ezuikit/multi-screen@0.1.0` | `abdf162aa0d442240ef67a2dcc0256af99385abda142f06ea511e968bdaa1d80` | 89137 | `data/official-components/2026-11-17/npm-mirror-remaining/…a1d80.tgz` | npmmirror cache，对照官方 npm 核验 |
| `@ezuikit/utils-logger@2.1.0` | `f0bfd5c6ee564c937e6206b32e925f154c02bcd2cffd6424b70ea2273eccf77a` | 28882 | `data/official-components/2026-11-17/npm-mirror-probe/…cf77a.tgz` | npmmirror cache，对照官方 npm 核验 |
| `@ezviz/openplatform-sdk@1.0.0-beta.1` | `509e4ab7107c1996f7dd080deeb10cd573847a4bbec993d3918732eb48486c6b` | 22095 | `data/official-components/2026-11-17/npm-artifacts-bounded/…486c6b.tgz` | official npm 直取 |

镜像只是字节取回通道；身份锚仍是原 `registry.npmjs.org` 元数据（integrity/sha1 逐项比对），镜像本身不证明官方功能或内容真实性。

**取回过程事实（按时间）**：早先两轮官方源观察留下失败记录——`initial_interruption`（`2026-11-17T01:00:28Z`，`ezuikit-js@9.0.26` 首轮响应过慢被协调者中止，per-socket 超时未约束总时长）与 `artifact_observation` bounded 批（`2026-11-17T01:07:23Z`，6 请求/最多 2 并发/每请求 45s 总时限/10s 连接/0 重试/不随重定向/单 artifact 32MiB 上限），该批内 5 个 `download_failed` 均为 `curl 28`（命中每请求 45 秒总时限），`ezuikit-js@9.0.26` 属首轮中止未重试——当时仅 `@ezviz/openplatform-sdk@1.0.0-beta.1` 通过，属历史观测，不再是当前结论。其后经受控镜像取回补齐字节：`npm-mirror-probe` 取回 `@ezuikit/utils-logger@2.1.0`，`npm-mirror-remaining` 取回 `@ezuikit/multi-screen@0.1.0`，`npm-mirror-resumed` 按实测吞吐续取 `@ezuikit/player-hls@2.0.0`、`ertc-web@2.1.2`、`ezuikit-js@9.0.26`，最后一次 header 失败后以有限接续方式在 `npm-flv-final-bounded` 批取回 `ezuikit-flv@2.1.2-beta.2`。此前失败记录（含未核验部分字节 458752/376832 等明细）保留于各批 manifest，不因后续成功而抹去；部分字节从未被核验、不代表可截断使用。

**license 证据（声明与随包文件分开列）**：

| 包@版本 | registry/package.json 声明 license | 随包 LICENSE 文件 |
| --- | --- | --- |
| `ezuikit-js@9.0.26` | ISC | 无随包 LICENSE 文件 |
| `ezuikit-flv@2.1.2-beta.2` | null | `package/LICENSE` sha256 `b2f0e1fecb7d842662d19842e1a3bfc81dcbc7f6423c89f608dd0c975d5b5604`（46513 字节） |
| `@ezuikit/player-hls@2.0.0` | null | `package/LICENSE` sha256 `457b7570faa5833762b4bd9c852985e323cfd832183e0892f1c7824e324e922f`（116 字节） |
| `ertc-web@2.1.2` | ISC | `package/LICENSE` sha256 `7679896a3f270685720de2a10333bf32bce8893d3d827a71cbb35dfa636356df`（34845 字节） |
| `@ezuikit/multi-screen@0.1.0` | MIT | `package/LICENSE` sha256 `c4427834453dcd7bbbf8bd005c9b45931a78ee7163ae8af8b8b7cec92d6cefd0`（1062 字节） |
| `@ezuikit/utils-logger@2.1.0` | MIT | 无随包 LICENSE 文件 |
| `@ezviz/openplatform-sdk@1.0.0-beta.1` | Apache-2.0 | `package/LICENSE` sha256 `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4`（11357 字节） |

`license=null` 不等于无许可（如 `ezuikit-flv`、`@ezuikit/player-hls` 声明为 null 但随包带 LICENSE 文件）；有声明字段也不等于随包带 LICENSE 文件（如 `ezuikit-js`、`@ezuikit/utils-logger`）。以上均为证据输入，非兼容性结论。

## 复用选择的待核定事项

1. **选择前核对**：按具体需求选定 exact 版本后，逐项核对该版本的 README/LICENSE/接口兼容，再定 `reuse_mode`（direct_use/thin_adapter/independent_implementation）；版本变更时旧集成证据不自动继承。
2. **分工边界（待核定，不是已集成）**：浏览器官方组件承接播放器/编解码/RTC；服务端 SDK 负责官方服务端入口。默认不把任何 JS 组件引入 Python SDK 运行依赖；具体媒体/RTC 集成方案与验收仍待定，见 `ACC-MEDIA-OFFICIAL-INTEGRATION-001`。
3. **不自动安装**：不自动安装上游包、不重写媒体/RTC 引擎。tarball 字节与身份未核验通过的版本不得进入依赖声明；本批 7 项核验通过只满足该前置条件，不等于已选定、已集成或已批准运行时采用。
4. **许可**：`license` 字段与随包 LICENSE 文件只是证据输入；ISC/MIT/Apache-2.0/null 各自需按拟用方式做兼容判定后才能复用代码或再分发，null 不等于无许可也不等于禁止。
5. **source 冻结缺口**：related-material 版本补充审查仍缺 34 项下载 binary 的逐项判定等，scope-register 中继续 `required`，不宣称 4051 已覆盖版本事实。
6. **验收边界**：材料审查通过 ≠ 真实集成完成 ≠ 设备/SDK 全部验收。registry/artifact 核验只是证据边界内的版本事实。

## 输入哈希（归档）

| 输入 | SHA-256 |
| --- | --- |
| `.claude/coordination/SP020-related-materials-review.json` | `71358c21e656a5abb12842d34cfa365162ac0bfb2e79eb19b96ec6888625fcce` |
| `data/official-components/2026-11-17/npm-metadata/manifest.json` | `ee8fd597506c7a8f51d82b091e9c79e07dce0f979d0be305d7f260716fbab2b9` |
| `data/official-components/2026-11-17/npm-artifacts/manifest.json` | `c9c19dae3eca6c039a8791a7334632c08c92d3908afea23241b308ba48766e61` |
| `data/official-components/2026-11-17/npm-artifacts-bounded/manifest.json` | `4cfe868c156982e2d9910940af89955c722cdb9a31d6f7c5f7e08209837e2d57` |
| `data/official-components/2026-11-17/npm-mirror-probe/manifest.json` | `28aa406694a4adfaa6cf1dd7dc05a026243f76f71879c04a5afed691aef389b3` |
| `data/official-components/2026-11-17/npm-mirror-remaining/manifest.json` | `d7e8c7700c58c170134ee16e22fabea38624dc01346db0df164db8938dfb6b67` |
| `data/official-components/2026-11-17/npm-mirror-resumed/manifest.json` | `7871845079775f4d057d61ef343757facf9199b23b48f80d2d177f8c5dd46b01` |
| `data/official-components/2026-11-17/npm-flv-final-bounded/manifest.json` | `1294597ff780efa0d76712f52a3a1f10726608689c5819f808e1ec9fb0170824` |

逐 artifact 冻结证据快照：`docs/capabilities/official-component-artifacts-2026-11-17.json`（不可变本批来源证据快照，非第三套维护结果数据库）。

数据归档一律用相对 repo 路径与 SHA；不复制 raw tarball locator、维护者信息或真实设备/账号值。
