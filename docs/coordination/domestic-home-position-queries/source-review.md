# 归位点查询来源核对

两份公开帮助页 [查询设备归位点模式](https://open.ys7.com/help/4478) 与 [查询自定义归位点设置状态](https://open.ys7.com/help/4480) 在 2026-11-21 经匿名核对，两页正文的 SHA-256 均与 2026-11-17 归档一致。E3 显示两者均支持托管及子账号，要求 `Config` 权限及设备能力 `support_ptz_homing_point`，4480 明确为通道级授权，4478 未注明粒度。

两页的 E7、E11 给出相同的设备与通道路径模板，方法均为 `POST`。E14 将请求体中的 `accessToken` 和 `key` 都列为必填字符串，4478 固定取 `returnToPoint`，4480 固定取 `preset`。E36 的 curl 仅给出 `accessToken` 请求头，未提供 `key`、查询参数、请求体或其编码。E40 的示例把 `data.valueInfo` 写成对象，字段值带引号。注释中，`returnToPoint` 的“0”和“1”分别表示默认和自定义归位点，`preset` 的“0”和“1”分别表示未设置和已设置自定义归位点。E44 将 `valueInfo` 列为 `string`，未说明编码，无法据此确定解码方式。

现有 [illumination.get_night_vision](../../../src/ezviz_unified/illumination.py) 通过 `GET` 在查询参数中传令牌和 `key`。同一路径不足以确定这两项 POST 的令牌位置、请求编码或返回字符串的编码，两项查询仍待设计与实测。可补充官方完整请求及响应示例，或经单独授权，以预先确定的单一路径和请求形态做一次只读核验。令牌只传一处，不自动尝试其他形态。本次仅核对文档。
