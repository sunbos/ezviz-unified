# 官方 HLS 预览本地演示指南

## 前提

- 本机已安装 [ffmpeg](https://ffmpeg.org) 且启用 `libx264`（可用 `ffmpeg -h encoder=libx264` 确认）。
- 交互式演示不需要云端配置，也不需要 Node.js。
- 准备下列版本的 npm 归档。示例启动时会核对 SHA-256，官方 JavaScript、WASM 和测试视频均不随 Python 包分发。

## 获取归档（可选，由维护者执行）

```bash
npm pack @ezuikit/player-hls@2.0.0 --ignore-scripts
shasum -a 256 ezuikit-player-hls-2.0.0.tgz
# 期望 SHA-256：8f7de1d2c96d310025e4d72a1221176d2584f5c98b6fc858dfd9daccca5087c9
```

本批固定组件版本为 `@ezuikit/player-hls@2.0.0`。分发或部署组件时请核对上游授权。[官方 npm 包](https://www.npmjs.com/package/@ezuikit/player-hls/v/2.0.0)

## 启动本地服务

```bash
.venv/bin/python -m examples.hls_preview --archive /path/to/ezuikit-player-hls-2.0.0.tgz
```

终端会显示绑定在 `127.0.0.1` 的临时地址。打开后点击「播放」，再点击「停止」释放实例。按 Ctrl+C 关闭服务并清理临时文件。示例启动时以合成凭据调用真实 SDK 客户端，两次请求均由 MockTransport 响应。浏览器获得的地址指向本机生成的有限 HLS 片段。详见 [`examples/hls_preview.py`](../examples/hls_preview.py)。

## 后端签发播放地址

应用后端通过真实客户端调用：

```python
from ezviz_unified import PlaybackOptions

resp = client.playback.create(
    serial, PlaybackOptions(channel=1, protocol=2, kind=1)
)
signed_url = resp.data["url"]
```

只把结果中的 URL 提供给已获授权的前端。播放地址本身可能包含访问凭证，应避免将它写入日志；AppKey、AppSecret 和平台 accessToken 留在后端。注意：这是地址签发操作（`read_only=False`），请勿与本地只读读取混淆；真实签发与真实观看需分别获得授权，本示例不发起任何实际调用。请把签名 URL 视作短时能力凭证妥善保护——我们无法承诺厂商侧不存在额外日志记录。

## 前端播放（官方 UMD）

本地页面从已核验的包加载 `dist/style/css.css` 和 `dist/index.umd.js`，由如下构造参数启动播放器：

```javascript
const player = new HlsPlayer({
  container: document.getElementById("player"),
  url: signedUrl,
  staticPath: "/vendor/",
  decoderType: "hard",
  autoPlay: true,
  isEzviz: false,
  disableCollect: true,
  muted: true,
  volume: 0,
});

// 在界面的停止事件或组件卸载时调用。
function stopPreview() {
  try {
    player.pause();
  } finally {
    player.destroy();
  }
}
```

`isEzviz: false` 适用于此处的标准本地播放列表。对另行获准的真实萤石地址，应按该组件的地址类型和上游配置设置该选项。`hard` 指该组件的 hls.js/MSE/`<video>` 路径，具体硬件加速情况取决于浏览器和设备。

软解、RTC、海外版均不在本指南范围内。

## 可选维护验证

```bash
cd tools/media
npm ci --ignore-scripts
node verify_hls.cjs --base http://127.0.0.1:PORT  # 将 PORT 换成服务打印的端口
```

维护检查另需 Node.js（18 或更高版本）和已安装的 Chrome；可用 `--chrome /path/to/chrome` 指定可执行文件。脚本确认画面帧确实前进、覆盖释放路径，并在不创建播放器的前提下检查无效 SDK 回复；不产生持久浏览器配置，回执仅保存版本、帧、请求计数与释放状态。详见 [`tools/media/verify_hls.cjs`](../tools/media/verify_hls.cjs)。

## 边界声明

本指南未声称已在真实授权平台或设备上验证，不覆盖 H.265 与 RTC。本地浏览器验收的版本和结果见[本次回执](validation/2026-11-19-official-hls-preview/checks.json)。
