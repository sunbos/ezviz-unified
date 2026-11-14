# 受控SDK补丁演练：R001

这是一项合成参数变更：模拟国内 `cn.storage.spaces` 的 `page_size` 上限由50降为20。官方归档仍是50；生产SDK、原始测试与契约登记未改为模拟值，没有调用真实API或发布。

## 结果与证据

- Claude Code通过Magpie明确使用 `devin/swe-2`、effort=max，零工具生成[补丁提案](proposal.json)。模型只接收固定新契约、指定源码和允许范围，未接收Oracle代码或运行结果；[模型记录](model-run.json)保存实际返回模型与用量。
- 协调方独立校验输入与源码哈希、精确替换范围、Python语法和AST修改范围。[差异](proposal.diff)只有目标上限与错误消息两行；提案器仅检查形式和来源，不自行证明正确性。
- 在未修改源码的副本中，独立Oracle产生预期的4项失败、11项通过：原实现仍接受21与50。见[基线结果](baseline-tests.txt)。
- 仅在隔离副本应用原样候选后，1,978项通过，其中既有SDK及离线策略测试1,963项、独立Oracle15项。见[补丁后结果](patched-tests.txt)与[汇总](result.json)。既有测试仅由可信协调方把一处受影响的50正例改为20，未删除测试，其它50上限保持。
- 运行时显式检查SDK来自副本的 `repo/src`。使用独立依赖目录、`-I -S -B`，不处理原项目editable安装或用户site。

## 本机隔离观测

执行前分别对[Oracle](probe-oracle.json)和[SDK源码](probe-sdk.json)运行可信探针：越界合成文件读取、只读源码写入、TCP连接均被EPERM拒绝；临时写入和asyncio正常。策略默认拒绝，只读运行时和副本，仅 `run/` 可写；不继承凭据或模型配置。

首个严格策略在Python启动前于dyld路径终止（SIGABRT，无标准输出）。单变量试验加入 `(allow file-read-data (literal "/"))` 后探针通过：该规则只允许根目录本身，不允许整棵文件系统内容。Apple公开[dyld源码](https://raw.githubusercontent.com/apple-oss-distributions/dyld/main/dyld/DyldProcessConfig.cpp)显示CacheFinder调用ignite；具体本机兼容要求来自上述试验，不能把公开源码视为当前系统构建的完整证明。

## 复核与边界

任务与固定Oracle位于 `evals/maintenance/repair_cases/page_size_limit/`；基线commit、输入哈希、候选及生产源码哈希在 `result.json`。提案检查使用 `harness.patch_proposal`；本机执行使用可选 `harness.macos_sandbox`，不支持时拒绝执行。

本次为一个公开、受控、单文件案例，不是隐藏测试集、复杂修复成功率或对任意恶意代码的隔离证明。未实现官网新契约确认、源登记晋升、自动合入与发布；这些阶段仍需各自验收。
