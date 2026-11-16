# SP016 区域目录协议只读观察

输入：main81b86b4、2026-11-17门户overseas469，content SHA fa599256d11f6097dc4dd429b1b1bc2f93f3431007b327ca2216f03633add4e8；来源e1459/1463/1468/1476。这是Codex协议判断，不是SDK功能验收。

固定GET https://open.ezvizlife.com/api/lapp/area/list，仅一次，无凭据、cookie、请求体、query、重试或redirect；connect/read/write/pool timeout各10秒，总响应解压后最多1MiB。不给设备/账户执行操作，不向其它域尝试。HTTPX trust_env=False，禁环境代理与凭据隐式输入。原始响应只在进程内用于结构判断，不保存或打印。

唯一输出：原checkout .claude/coordination/SP016-area-observation.json，固定字段包括时间、请求数、HTTP status、JSON顶层类型、已知schema键的类型、list长度/field-type概况、结果与限制。未知key仅计数，不输出原始名称/值。错误只固定类别，不输出exception消息或chain。观察失败如实保留，不重复探测。若成功仅支持当次HTTP/结构观察，不声明SDK入口、账号归属、设备或写操作通过。
