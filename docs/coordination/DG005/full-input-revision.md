# DG005T2：完整文本输入，禁止工具

上一会话尝试Read/Bash，但本模式没有任何工具，真实结果均No such tool，未读到文件；其候选不验收。此轮纠正任务定义：下面提供两个目标文件完整文本和真实诊断模块diagnostic_report.py（不是设备查询diagnostics.py），所有所需接口/常量都在输入中。仅处理文本并输出edits，不尝试Read/Bash/Task/任何工具，不访问工作区，不执行或声称测试通过。无需定位其它文件；若认为还有缺输入，直接在notes说明，不猜、不工具调用。固定devin/swe-2 effort=max。
