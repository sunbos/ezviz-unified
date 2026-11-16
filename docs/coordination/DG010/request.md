# DG010：入门文档输出说明校正

固定devin/swe-2 effort=max，零工具，仅文本候选。唯一docs/getting-started.md。输出JSON围栏 {"edits":[{"path":"docs/getting-started.md","old":唯一原段落,"new":替换段落}],"notes":[]}。不执行、不调用工具、不改其它段落或文件。

当前'本地诊断示例'段落仍说失败stderr是一行脱敏JSON，已过时。实际两个示例：stdout始终单一schema_version=1报告JSON；stderr仅固定纯文本提示，无异常message/traceback；退出0=成功、1=失败（包括未预期BaseException/SystemExit）、130=KeyboardInterrupt/CancelledError。SDK/capture异常原样传播不变，转换发生在示例CLI的run_cli边界。该示例CLI未真实联网，不能把tools/live验收等同CLI实测。

仅把下面完整段落校正为准确简洁中文，保留报告隐私说明、docs/diagnostics.md链接和CLI未联网边界。不要加入已完成实测/发布声明。
