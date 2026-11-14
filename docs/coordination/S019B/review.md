# 统一存储入口验收

通过纯文本、零工具非交互Claude取得Storage/AsyncStorage候选，返回模型devin/swe-2，effort请求max；Codex审核后接入Client.storage及公开导出。候选不读取新目录，不改变其信任状态。

七项接口以独立HTTPX报文断言核对方法、路径、form/query/Header位置和严格meta.code整数成功规则。只读POST文件搜索允许有限token恢复；所有写操作即使源标幂等也不重放，不自动翻页、解冻、下载或清理前置资源。更新仅发送显式字段，保留Boolean/裸整数/数组/对象的原始data。

新增资源名称、游标、CSV成员及整数子类的诊断脱敏；测试证明真实报文未改变。字符串伪成功码按现有ApiError处理，float/bool属于ProtocolError，测试对错误类别作了精确区分。

业务域仅包含统一存储引擎：5341旧云录制解冻与5342云点播元数据没有被混入。全部操作均在模拟传输执行，没有真实资源变更。
