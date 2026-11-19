# 基础文生图

手机远程改参数，电脑上的 ComfyUI 按固定工作流出图，图片收回手机图库。

这个仓库只包含手机控制端和本机网关。ComfyUI 本体、模型、自定义节点不在这里。

## 数据怎么走

```
手机浏览器或安卓壳
        |  HTTPS（樱花 TCP 隧道 + 自动 HTTPS）
        v
本机网关  127.0.0.1:8787     只绑本机，不要改成 0.0.0.0
        |  HTTP / WebSocket，仅本机
        v
ComfyUI   127.0.0.1:8188     不要把 8188 暴露到公网
```

手机和电脑可以不在同一个局域网。公网入口是樱花隧道，不是 ComfyUI 自己的端口。隧道类型是 TCP，远端自动 HTTPS。网页和接口同源，手机不另填 API 地址。

登录密码就是隧道文件里的访问密码。网关比对密码后发 Bearer token，会话写在本机 `data/sessions.json`。

## 目录

| 路径 | 作用 |
| --- | --- |
| `gateway/app.py` | 登录、拼工作流、提交 ComfyUI、收回图片 |
| `gateway/__main__.py` | 用 uvicorn 启动网关 |
| `web/` | 手机页面。三块：LoRA、采样和分辨率、底模和提示词 |
| `android/` | 安卓 WebView 壳，打包成可安装的 APK |
| `启动网关.bat` | 建虚拟环境、装依赖、启动网关 |
| `打包安卓.bat` | 打 debug APK，复制到项目根目录 |
| `内网穿透隧道信息.txt` | 本机私有文件，不进 git |

运行时数据在 `data/`：任务 `jobs.json`、登录会话 `sessions.json`、图片 `images/`。手机图库另外存在浏览器或 WebView 的 IndexedDB（库名 `comfy-phone`，表 `shots`）。换浏览器或换成 APK 后，图库是新的，旧图不会跟着过去。

## 本机准备

1. ComfyUI 已在 `127.0.0.1:8188` 运行。
2. 已安装并能被 ComfyUI 加载：
   - 底模在 `models/diffusion_models/`，手机上的底模列表来自这里
   - CLIP：`text_encoders/qwen3vl_4b_fp8_scaled.safetensors`，类型 `krea2`
   - VAE：`vae/qwen_image_vae.safetensors`
   - LoRA 在 `models/loras/`
   - 自定义节点 ComfyUI_Comfyroll，类名是 `CR LoRA Stack` 和 `CR Apply LoRA Stack`
3. 项目根目录有 `内网穿透隧道信息.txt`。网关启动时读它，缺访问密码会直接退出。格式用全角冒号：

```text
端口：8787
访问密码：这里填登录密码
隧道：节点域名:远程端口
```

网关只认这三行的键：`端口`、`访问密码`、`隧道`。手机打开的地址是 `https://` 加上 `隧道` 那一行。证书由 frpc 自动生成，浏览器会警告；安卓壳第一次会询问是否只信任这个主机。

## 启动

双击 `启动网关.bat`，或：

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m gateway
```

依赖：FastAPI、uvicorn、httpx、websockets。Python 3.12 可用。网关只监听 `127.0.0.1`，端口来自隧道文件，默认 8787。

手机浏览器直接打开隧道的 HTTPS 地址。要装成应用时，运行 `打包安卓.bat`。需要 JDK 17、Android SDK 的 `platforms;android-34` 和 `build-tools;34.0.0`。脚本优先用 `%LOCALAPPDATA%\Android\gradle-8.9`。安装包是 debug 签名，手机需要允许未知来源。默认服务器写在 `android/app/src/main/res/values/strings.xml` 的 `default_server_url`。应用右上角可以改服务器地址。

## 手机能改什么

- 提示词。原文送进 `CLIPTextEncode`，网关不调用提示词增强。
- 底模。只换 `UNETLoader` 的 `unet_name`。CLIP 和 VAE 固定，手机上不能选。
- 最多 9 个 LoRA。每行有开关、文件、模型权重、CLIP 权重。关掉的行不送进工作流。
- 采样器、调度器、步数（1–40）、CFG（1–12）、种子。种子留空则网关随机。界面默认 8 步、CFG 1、`euler`、`simple`。
- 分辨率。八个比例，按 1.0 百万像素、对齐 8 计算。1:1 大约 1024×1024。

界面上 LoRA 滑条是 -4 到 4、步长 0.01。接口允许 -10 到 10。

图库只收这个手机拿到的成图。任务约每秒轮询一次 `GET /api/jobs/{id}`。WebSocket 只存在于网关和本机 ComfyUI 之间。

## 工作流

运行时的图由 `build_workflow()` 现拼，不读取 ComfyUI 里保存的 `基础文生图.json`。那份桌面工作流是参照，不是网关加载的文件。桌面稿用 `er_sde`、16:9、4 百万像素、对齐 32。手机端默认仍是 `euler` / `simple`、1.0 百万像素、对齐 8。不要把两边悄悄合成，除非明确要改默认值。

节点：

| id | 类 | 作用 |
| --- | --- | --- |
| 4 | UNETLoader | 底模，`weight_dtype` 为 `default` |
| 11 | CLIPLoader | 固定 CLIP |
| 12 | VAELoader | 固定 VAE |
| 49 | ResolutionSelector | `megapixels=1.0`，`multiple=8` |
| 5 | EmptyLatentImage | 宽高来自 49，batch 1 |
| 6 | CLIPTextEncode | 手机上的提示词 |
| 13 | ConditioningZeroOut | 负面条件。没有负面提示词框 |
| 3 | KSampler | `denoise` 固定为 1 |
| 8 | VAEDecode | |
| 9 | SaveImage | 文件名前缀 `phone` |
| lora0… | CR LoRA Stack | 每 3 个开启的 LoRA 一组，组与组用 `lora_stack` 串起来 |
| 79 | CR Apply LoRA Stack | 把栈套到模型和 CLIP 上。没有开启的 LoRA 时不创建 79，采样器直接用节点 4 和 11 |

空出来的栈槽位写成 `switch=Off`、`lora_name=None`。ComfyUI 会跳过它们。

## 接口

除登录和健康检查外，都要 `Authorization: Bearer <token>`。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/health` | 网关是否在，以及是否连得上 ComfyUI |
| POST | `/api/login` | `{ "password": "..." }`，成功返回 `{ "token" }` |
| GET | `/api/options` | 底模、LoRA、采样器、调度器、比例和默认值 |
| POST | `/api/generate` | 见下方 |
| GET | `/api/jobs` | 最近任务，最多保留约 80 条 |
| GET | `/api/jobs/{id}` | 单条状态。排队中的任务若已不在 ComfyUI 队列里，会标成错误 |
| GET | `/api/jobs/{id}/image` | 成图文件 |

`POST /api/generate` 的主体：

```json
{
  "prompt": "原文提示词",
  "unet": "diffusion_models 里的文件名",
  "aspect_ratio": "1:1 (Square)",
  "steps": 8,
  "cfg": 1,
  "sampler_name": "euler",
  "scheduler": "simple",
  "seed": null,
  "loras": [
    { "enabled": true, "name": "文件名.safetensors", "model_weight": 0.8, "clip_weight": 0.8 }
  ]
}
```

`aspect_ratio` 用 ComfyUI 的英文 id，例如 `16:9 (Widescreen)`。`loras` 最多 9 条。开启但文件不在电脑上的 LoRA 会被拒绝。

任务状态：`queued`、`running`、`done`、`error`。网关重启时，还在排队或运行的任务会记成「网关重启时这张图还没跑完」。

## 给后续修改的约束

- 网关继续只绑 `127.0.0.1`。不要把 ComfyUI 的 8188 映射到公网。
- 不要把 `内网穿透隧道信息.txt`、`data/`、`.venv/`、APK、`android/local.properties` 提交进 git。访问密码不要写进源码或这篇说明。
- 提示词保持原文。负面条件保持 `ConditioningZeroOut`。
- 多个 LoRA 继续走 CR 栈，不要改回单个 `LoraLoaderModelOnly`，除非同时改手机界面。
- 安卓壳只加载网关页面。证书放行只针对用户填写的那一个主机。
- 页面是深色工具界面：底 `#0F172A`，卡片 `#1B2336`，强调色 `#22C55E`。用系统中文字体。手机栏最宽约 480px。

## 不在这个仓库里的东西

- ComfyUI 程序、模型、用户工作流 JSON
- 隧道访问密码和真实隧道地址文件
- 已生成的图片、登录会话、虚拟环境、APK
