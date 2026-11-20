# 海外 FLV 本地预览指南

本文说明如何在不接入云端的前提下，用官方 ezuikit-flv UMD 与本地合成流验证 FLV 预览链路。

## 前提条件

- 已在源码仓库配置 SDK 开发虚拟环境 `.venv`
- 已安装 FFmpeg 且含 libx264 编码器
- 已准备指定版本的官方包 `ezuikit-flv-2.1.2-beta.2.tgz`，SHA256 为 `4f5d247f1a1c0dbeec439c4fcde1bba5067f5a55d16d9f7368a0b9ca570601fc`
- 自动验收还需要按 `tools/media/package-lock.json` 安装的 `playwright-core`，以及支持 H.264 MSE 的 Chromium

## 启动演示

在检出根目录运行

```bash
.venv/bin/python -m examples.flv_preview --archive /path/to/ezuikit-flv-2.1.2-beta.2.tgz
```

命令会打印 127.0.0.1 的源地址与端口。在浏览器打开该地址，点击「播放」即可查看合成画面，点击「停止」退出播放。后端实际调用一次海外预览签发方法，认证和业务 HTTP 请求各由 MockTransport 回应一次。页面使用官方 UMD，通过 MSE 把本地生成的 320×180 H.264 视频交给浏览器解码。

播放器实例位于每次播放的同源子页面中。显式停止、自然结束或离开页面时，示例调用官方实例的清理方法并移除子页面，使这次播放的 Worker 随之退出。再次点击「播放」会创建新实例。

## 浏览器验收

若 `tools/media` 依赖已就绪可跳过安装，否则在源码根目录执行

```bash
npm ci --prefix tools/media
```

保持演示服务运行，在另一个终端将 `PORT` 换成服务实际打印的端口后执行

```bash
node tools/media/verify_flv.cjs --base http://127.0.0.1:PORT
```

默认使用 `playwright-core@1.57.0` 配套的完整 Chromium 143.0.7499.4。如果该浏览器尚未安装，可显式运行 `node tools/media/node_modules/playwright-core/cli.js install chromium`。也可用 `--chrome /path/to/chrome` 指定另一个支持 H.264 的可执行文件。本机 Chrome 157 曾在媒体已释放后关闭进程超时。

脚本检查同一视频的解码帧、媒体时间和三种已知颜色，并分别核对显式停止、离页、自然结束时的资源释放。

## 真实后端对接

真实环境由持有密钥的后端调用

```python
from ezviz_unified import PlaybackOptions

response = client.playback.create(serial, PlaybackOptions(channel=1, protocol=4, kind=1))
signed_url = response.data["url"]
```

真实签发会调用平台服务。`data.url` 可能携带访问凭证，仅交给获授权的播放前端。应用密钥留在后端，日志应省略播放地址。[海外资料 534](https://open.ys7.com/help/en/534)

## 边界说明

UMD 静态版本字段为 2.1.2，包元数据为 2.1.2-beta.2。归档由调用方提供，运行时 SDK 不内置官方库。本案例的验收范围是本机浏览器、合成 FLV 和 H.264。真实服务、原生播放、H.265 与 RTC 另行验收。分发官方库前须单独核对适用授权。[官方包版本](https://www.npmjs.com/package/ezuikit-flv/v/2.1.2-beta.2)
