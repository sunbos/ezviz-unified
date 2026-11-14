# 单设备只读检查 Implementation Plan

**Goal:** 让调用方用一个明确入口收集已知设备的基础信息、通道、状态、能力与版本，并获得逐项结果与可分享的脱敏摘要。

**Architecture:** 独立SDK编排函数调用既有门面，不新增端点、transport或批量调度器。返回保留原ApiResponse/异常对象的值对象；默认repr和public_summary不泄漏设备标识、载荷或异常原文。

**Tech Stack:** Python 3.12+、现有HTTPX客户端、dataclasses、pytest/MockTransport、Ruff/mypy。

## 已定接口

- `inspect_device(client: Client, device_serial: str, *, channel: int = 1) -> DeviceInspection`。
- `async inspect_device_async(client: AsyncClient, device_serial: str, *, channel: int = 1) -> DeviceInspection`。
- 固定顺序：`devices.get(serial)`、`devices.channels(serial)`、`devices.status(serial, channel=channel)`、`diagnostics.capabilities(serial, channel=channel)`、`diagnostics.version(serial)`。均为已有两区域只读接口。
- 首次调用前验证serial非空白str、国内不含小写ASCII字母、channel严格int非bool；不转换原值，不新增厂商未声明的channel范围。
- `InspectionResult(operation: str, response: ApiResponse | None = None, error: EzvizError | None = None)`：冻结dataclass；response/error恰有其一，否则固定ValueError；二者repr=False；`succeeded`属性表示response非None。
- `DeviceInspection(region: str, device_serial: str, channel: int, results: tuple[InspectionResult, ...])`：冻结dataclass；device_serial repr=False；`all_succeeded`为非空results且每项成功。原始载荷保留引用，不承诺深度不可变或原子快照。
- `public_summary()`返回全新dict：`region`、`all_succeeded`、`results`；每行operation与status=ok/failed，失败另含error_type、合法code/http_status。code仅非bool int或纯ASCII数字str，http_status仅非bool int；非法字段省略。不返回设备序列号、channel、原始response/error、异常message、URL或凭据。
- 仅捕获EzvizError作为逐项失败继续；其中AuthenticationError直接传播并停止后续调用，保持原异常身份。ValueError、程序错误、取消和KeyboardInterrupt传播。按SDK异常分类处理，不猜测其它业务码一定是认证错误。
- 既有SDK仍负责token、地区、超时与重试。这是最多五次顺序逻辑调用；认证与SDK既有重试可能增加实际HTTP次数。函数不额外重试、不分页、不选择设备、不关闭借用client，不启后台工作。
- 导出四个新名称：InspectionResult、DeviceInspection、inspect_device、inspect_device_async。不要给Client再挂一个门面。

## 分工与验收

- [x] D002A：Claude实现 `src/ezviz_unified/inspection.py` 与根导出；严格mypy、无新运行依赖。
- [x] D002B：独立Claude生成 `tests/test_sdk_inspection.py`，不接收实现；覆盖两区域同步/异步真实MockTransport成功、部分失败、fatal认证与取消、调用顺序/参数、无额外列表/写操作、借用生命周期、非法输入前置、未知响应字段保留、repr/摘要脱敏与新容器。
- [x] D002C：示例和中文使用说明在函数接口固定后生成；显式序列号来自env，不打印该值，不自动扫描账号。
- [ ] Codex检查候选、先运行独立测试观察缺实现失败，再集成并做回归、类型/格式/发行边界与CI检查。

## 决策与边界

R004建议批量snapshot门面；本设计缩为单设备函数，避免无界Iterable、额外client门面和“原子快照”暗示。保留复用价值：逐项结果、统一错误边界、取消语义与安全摘要可直接给业务代码使用。相较示例复制，不要求每个调用方重做五次调用的异常处理；相较健康评分，不推断未知设备状态。

本批不改变原端点语义，不引入App认证、不做存储查询或设备写操作。离线验证与真实设备验收分别记录；当前真实账号信息不提供给Claude。

## 本地验收记录

实现、测试与示例由Claude生成并按审查意见修订；52项SDK测试与98项示例测试通过。全库2827项测试及11项子测试、36文件mypy、126文件Ruff/格式检查通过；wheel/sdist元数据与干净安装通过，源码包2015项SDK测试通过。远端CI及后续真实设备验收另列，不把离线结果当成联网证明。
