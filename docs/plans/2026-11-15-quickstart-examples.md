# 最小只读接入示例实施计划

**目标：** 让使用者从源码安装后，通过国内或海外账号完成同步/异步首分页查询，并能判断常见失败与证据限制。

**架构：** 复用现有 `Client`、`AsyncClient` 和 `RegionConfig`；示例只展示一个设备列表调用，不增加协议、业务框架或依赖。完整诊断继续使用已有 `tools/live/verify_devices.py`。

**技术：** Python 3.12+、现有 HTTPX SDK、pytest 的 MockTransport 离线检查。

## 文件与职责

- `examples/__init__.py`：使 `python -m examples.devices_sync` 可从仓库根运行。
- `examples/_common.py`：读取显式区域/凭据配置；只输出分页数量和固定错误类别，保留数字业务码与HTTP状态。
- `examples/devices_sync.py`、`examples/devices_async.py`：分别演示上下文管理、首分页读取和进程退出状态，不自动分页。
- `docs/getting-started.md`：源码安装、环境配置、两种调用方式、结果解释与排错表；明确 `.env` 不自动加载及未发布PyPI。
- `tests/test_examples.py`：通过真实SDK与MockTransport验证国内/海外路由、空列表、错误响应和脱敏输出。

## 执行顺序

- [x] Codex先写离线行为测试：显式配置、同步/异步、国内/海外、成功/空列表/认证失败、异常结构；断言仅首分页和无真实标识输出。用户随后调整分工，后续测试补充和代码修订由Claude生成。
- [x] Claude Code经Magpie使用`devin/swe-2`、effort=max，工具关闭，只根据筛选的SDK签名生成候选示例与接入说明；不得访问账号或执行网络。
- [x] Codex核对候选，运行`.venv/bin/python -m pytest tests/test_examples.py -q`，检查实际请求域名、参数和调用范围。
- [x] README加入接入入口，AGENTS更新examples目录说明；CI格式检查纳入示例，MANIFEST明确排除examples，保持wheel/sdist边界。
- [x] 运行相关检查和完整回归，构建并校验发行物。本地结果见[D001验收](../coordination/D001/review.md)，提交后的四版本远端状态以Draft PR checks为准。

## 验收边界

示例不是全量盘点或设备诊断工具；`page_start=0,page_size=1`仅证明该分页结果。只有运行示例才联网，导入不读取配置或发请求；错误输出不打印原始响应、完整异常、URL或输入值。海外可显式设置`EZVIZ_AUTH_BASE_URL`，不轮询猜测区域；国内设置该覆盖项时应报配置错误，避免悄悄忽略。

本批使用合成账号和响应离线执行；不新增真实设备调用。已有真实国内与新加坡记录作为独立参考，不把它们算作本批示例实测。用户数据、凭据与上游示例代码不进入候选输入。
