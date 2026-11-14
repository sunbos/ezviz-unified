# 官方来源验收

七页原始响应与正文hash先由Codex核对，再由零工具Claude复核参数表、方法、路径、token位置、响应和约束。返回模型devin/swe-2，effort请求max；参数表与独立提取结果逐项一致。

候选有一条错误意见：声称5234 curl缺少fileNodeIds。Codex实际解析源query，确认accessToken、fileNodeIds、expireSeconds均存在，已剔除该意见；没有照单接受候选完成声明。

保留三项来源问题：bizTypeList缺少多值编码说明；spaceIds的CSV为集合参数风格推导；更新表格默认值与FAQ省略保持原值并存。源中的跨接口差异不一律标为错误，400等代码也不擅自当作HTTP状态。

contracts-review.json记录来源hash和处理边界；source-parameter-review.json保留直接从归档提取的字段证据。新契约只表示文档审核与离线验证，不表示真实接口可用或自动发布许可。
