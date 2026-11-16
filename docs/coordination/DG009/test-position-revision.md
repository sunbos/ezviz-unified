# DG009R：控制位置fixture必须针对外层call

固定devin/swe-2 effort=max，纯文本零工具。唯一tests/test_sdk_diagnostic_control.py；输出简短JSON {"edits":[{"path":"tests/test_sdk_diagnostic_control.py","old":unique_old,"new":replacement}],"notes":[]}，不改生产源码或断言，不全文件重生成，不调用工具。

静态审查发现测试定位不准确：_POSITION_SENDS对pop/exit_emit期望1次请求，但当前patch会在第一个transport_setup的pop/terminal处就中断，实际还没发HTTP。因此应把这两种位置限定为外层kind=call的client.request作用域（其body返回后HTTP已经发送），保持原次数断言1，不能放宽。

- pop：保存real_pop；callback拿recorder._current_scope()，仅当scope._kind=="call"且scope._entrypoint=="client.request"时raise原marker，否则调用real_pop(token)。
- exit_emit：仅当event.kind==call且entrypoint==client.request且outcome!=started时raise原marker，其余event原样real_append。
- construct/push_mid/enter_emit仍在入口首先中断、预期请求0，保持原逻辑。
- 其余测试不动。已提供整份真实测试，不需要其它文件。当前SDK Client.request嵌套transport_setup/attempt作用域，所以不能以第一个pop模拟外层body结束。
