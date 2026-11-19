# 官方 HLS 预览本地演示指南

## 前提

- 本机需安装 FFmpeg；默认 H.264 使用 `libx264`，H.265 使用 `libx265`。可用 `ffmpeg -hide_banner -encoders` 检查。[FFmpeg](https://ffmpeg.org)
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

## H.265 软件解码

在仓库根目录选择 `h265` 启动同一个示例：

```bash
.venv/bin/python -m examples.hls_preview --archive /path/to/ezuikit-player-hls-2.0.0.tgz --codec h265
```

示例向真实 SDK 的模拟地址签发传输发送 `protocol=2`、`type=1`、`supportH265=1`、`containerFormat=0`。前两项选择 HLS 预览，后两项要求 H.265 和 TS 封装。播放列表指向本机生成的无音轨、8-bit YUV420P 合成视频。省略 `--codec` 时保留原有 H.264 行为。[国内地址签发协议](https://open.ys7.com/help/1414)

页面为该清单选择官方 `decoderType: "soft"`，浏览器需支持 WebGL 和 Web Audio。`decoder.worker.js` 与 `decoder.wasm` 从同一 `staticPath` 加载。播放时保持页面可见，白色参考带旁的彩色区会按红、绿、蓝循环。点击停止后，软解 worker 的异步退出需要单独观察。

## 可选维护验证

```bash
cd tools/media
npm ci --ignore-scripts
node verify_hls.cjs --base http://127.0.0.1:PORT  # 将 PORT 换成服务打印的端口
# 若服务以 --codec h265 启动，改用下面这一条
node verify_hls.cjs --base http://127.0.0.1:PORT --codec h265
```

维护检查另需 Node.js（18 或更高版本）和已安装的 Chrome；可用 `--chrome /path/to/chrome` 指定可执行文件。脚本确认画面帧确实前进、覆盖释放路径，并在不创建播放器的前提下检查无效 SDK 回复；不产生持久浏览器配置，回执仅保存版本、帧、请求计数与释放状态。详见 [`tools/media/verify_hls.cjs`](../tools/media/verify_hls.cjs)。

## 边界声明

H.265 维护验证还需要 `ffprobe` 检查实际分片。回执分别记录解码资源、可见 Canvas 的颜色顺序与媒体时间，以及停止后 worker 的退出。本地结果只涵盖本指南的合成样本；真实设备、远端流、10-bit、fMP4、RTC 与跨浏览器兼容性仍待单独验证。见 [H.264 回执](validation/2026-11-19-official-hls-preview/checks.json) 和 [H.265 回执](validation/2026-11-19-official-hevc-preview/checks.json)。
