# SP001B：官方公开资料刷新

输入版本main81b86b462825739db8f059a418d5de235db88c87，使用现有采集器；固定Claude Code via Magpie devin/swe-2 effort=max。唯一执行命令：`.venv/bin/python tools/documents/reference_collectors/domestic_sdk.py --output data/official-docs/2026-11-17-sdk-product-reference/domestic --seeds data/official-docs/2026-11-14/discovery/sdk-seeds.json --workers 4 --refresh`。唯一可写采集目录：`data/official-docs/2026-11-17-sdk-product-reference/domestic`（含自身checkpoint/manifest/raw）；不得覆盖旧快照、源码、配置或用户文件。仅允许访问采集器已有白名单内的官方文档/静态HTML，禁止业务API、设备、登录、密钥、浏览器、执行所获HTML/脚本。采集不等于完整官方能力或实机验证。

验收：真实工具结果；catalog/manifest、哈希、状态计数、队列闭包；缺页与失败保留。静态种子先沿用已核对入口，本轮新门户完成后须比较最新入口再决定是否扩充，不据旧种子宣称完整覆盖。时间上限由现有采集器请求timeout/页面cap和Bash600000ms控制，超时按中断保留并核查实际进程，不擅自重启。
