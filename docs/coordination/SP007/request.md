# SP007 消息资料扩展审查记录（不实现）
输入004a795/main81b86b4和新主门户2026-11-17-sdk-product-main已完整性校验。固定devin/swe-2 effort=max。唯一写docs/research/2026-11-17-message-scope-review.md；无Bash、无网络、禁读data与凭据。Read既有docs/research/2026-11-15-message-ingestion.md和webhook-helpers.md、official-ecosystem-reuse.md、src/ezviz_unified/webhooks.py及产品plan/acceptance。根据以下已由Codex从本地来源审查的安全事实写中文记录，不凭空扩展事实，不修改旧文件。
真实资料事实（源URL固定https://open.ys7.com/help/<id>，海外help/en/2392）：
5127：套餐/范围element22，不保证顺序element23。
5128：4–7公网POST前提，10已有官方标准库http.server Python quickstart，AST审查不读取Signature/t且打印sorted_json；28–29需实际事件触发。因此本轮增量应是SDK验签/有界接收/安全回执，不是首创Python Webhook。
5129：element1/5开通影响：开通及修改消息类型等待2小时；同手机号萤石云视频App会停止设备消息提醒，关闭消息服务不会自动恢复，需客服/工单。14–34配置，签名secret最多64字节英文字母数字（服务配置约束，不能收紧通用helper）。194–226控制台消息测试会暂停原回调、临时开全部类型，关闭恢复原类型；平台模拟消息仅平台到接收器，不等于真实设备事件链。
5130：1–4超时/重试/持续失败降级；541–559实际成功ACK=HTTP200+原messageId；561–586 HMAC-SHA1及毫秒Timestamp，566/567明确单位。包含ys.alarm/ys.calling等字段，可能含设备密码/身份/IP/媒体地址，禁止记录原body。主消息文档2秒超时；5328却写通常3次/一般5秒，不抹平冲突。
2392海外：6需联系官方开通（这里只记录，禁止发送邮件）；11–12正文JSON但Content-Type:text/plain；72 ACK；80–95 HTTPS/回调/类型/可选签名/重试1–3次配置。不能照搬CN开通流程，t单位本篇未核定。
566：21–36开通、68–78头、117ACK、132超时。5328：8–14前提、94幂等、97–99超时次数冲突。ERTC/直播/智居另有4913/4070/4427权限，不自动算本次完成。
下面SHA全部是pages/<region>/<id>.source.json文件字节SHA，不是content_sha256，元素均BeautifulSoup(html_content,'html.parser').find_all(True)0起始：
CN5129 a4201850af2ea84c67e1ab1a31180ee1bfa114acbe41447f712a804089cebc8c
CN5130 406c87030686913521c0d13636eb21288e177ddbcc5c6ce3cafc8603b5d90355
CN5127 5c5c1733502442023f7eaca2182055ed69ce408cd15989394f8c3a2f30a91886
CN5128 8c391b22e2f57282954f84763b89688e43e33de7b7d080aaf88c5ed335878037
OS2392 04355e5bd34b4d8873bb56fa8fcd3db1720e793aa3d3495e2097f5a2d482b6e2
CN566 94150e6988d4ff2ddb237d8469d71466ddb606667a69f68f0b51bf59e936c2e1
CN5328 c4f230592a418e78c6fa9f89c0db1bdd6741b8a0c4edc49f80d6eb629c858043
独立离线签名核对（已执行算术，非平台实测）：5130 elements574正文、577key、580t、583方程签名、586HTTP例。HMAC-SHA1(key,(body+t).encode)使用展示正文原字节、去外层换行、HTTP展示body均false；json.loads展示正文再json.dumps(ensure_ascii=False,separators=(',',':'))得到的minified派生bytes=true。HTTP例Signature/t与方程例一致。只可说“minified派生的离线向量匹配”，官方展示字节仍有矛盾；线上必须验原始收到bytes，不得先parse/minify。不能把旧六篇未查到示例继续泛化到全部文档；海外单位仍未核定。
建议架构是设计选择尚未实现：保留SDKpure helpers；仓库级独立接收示例，loopback默认、固定POST路径、显式独立secret、有界body/连接/时长/次数；read rawbytes→验证签名→解析→明确定义受理→实际200+messageId。缺/坏签名、非法JSON、超限返回固定错误且不记录原文。有界messageId去重（成功重复仍ACK不重复处理，同ID不同body判据待冻结），不承诺跨重启/exactly-once。国内profile可显式毫秒时窗，容错/缓存容量等设计数值尚未冻结不能编造；海外不猜单位。安全回执仅固定枚举/数量/时延/代码来源版本，不含secret/messageId/设备/body/mediaURL。不得建设公网托管服务、自动隧道、开通平台、下载媒体、设备联动。
真实验收必须具体确认服务/套餐/类型/原回调/App影响/恢复方案后授权，受控HTTPS入口、独立secret本地输入、目标类型和trigger、重试失败注入次数窗口。平台模拟与真实设备事件分开报告，本地模拟不代替平台。用户已确认海外无绑定设备，消息配置本人不清楚；不得称消息已开通或已实测。国内只读授权不覆盖消息配置操作。
文档写可审查scope与尚待冻结/资源清单、来源表、旧结论修正、建议验收分层，不扩大为通用框架，不声称实现/测试完成。源码未变，所以不需运行tests。
