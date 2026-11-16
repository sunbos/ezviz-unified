# DG006B 候选已应用

状态：submitted，等待Claude单命令定向测试；未声称通过。

来源链：DG006B完整候选 → DG006BR 5个精确替换 → DG006BR2 3个精确替换 → DG006BR3 两处相同终态过滤修订。所有轮次均devin/swe-2、effort=max、tools=[]、tool_calls=0，已核验各metadata。Codex只审查、指出具体错误与机械应用，未手写测试实现。

BR2保留except对象到独立局部引用，并按call/client.request筛选业务失败；BR3补齐排除started的终态条件，保留唯一failed与unknown断言。原H04-H06覆盖未削减。

应用文件tests/test_sdk_diagnostic_behaviour.py；AST可解析，候选最终SHA256：b2e1a733f426c1d6b5897bfdb410f3f6f7a546c7694ec741229c150cc87792a7
