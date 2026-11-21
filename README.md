# Eraser

**Windows 桌面端视频去水印工具** —— 在画面上框住水印，AI 把它擦掉，画质、帧率、音轨都不动。

从 macOS 项目 [Mac-Remover](https://github.com/Drew1811266/Mac-Remover) 移植而来：界面是原项目的那一套，
去水印算法是原项目的那一套，外壳换成了 Windows 上能跑、能分发、能安装的 Electron + Python 引擎。

> **当前版本：`0.0.1`（开发快照）**
> 功能完整、15 道验收门禁全绿、两个安装包可以正常安装和使用。
> 之所以标 `0.0.1` 而不是 `1.0`，是因为还缺一件事：**没有 mac 版录制的黄金样本来做最终的画质对位**（见[已知限制](#已知限制)）。

---

## 目录

- [它做什么](#它做什么)
- [快速开始](#快速开始)
- [系统要求](#系统要求)
- [架构](#架构)
- [验证到什么程度](#验证到什么程度)
- [已知限制](#已知限制)
- [项目结构](#项目结构)
- [开发](#开发)
- [文档](#文档)
- [许可证与致谢](#许可证与致谢)

---

## 它做什么

### 去水印

- 视频导入后可预览、逐帧拖动定位。
- 在画面上**拖拽框选**水印区域，或直接输入数值；可拖边调整。
- 一段视频可以有**多个标注段**（不同时间段、不同位置），每段独立启停。
- 擦除用 **LaMa** 图像修复模型（ROI 分块推理 + 拼接），只处理框内区域。
- 输出保持原分辨率、原帧率、原音轨；未标注的帧原样直通（不重编码损失画质）。

### AI 放大

两套引擎，按需选：

| 引擎 | 说明 | 安装方式 |
|---|---|---|
| **Real-ESRGAN** | ONNX 推理，2×/4×，随安装包提供 | 开箱即用 |
| **SeedVR2** | 3B 扩散模型，画质更好、更吃显存 | 应用内按需下载运行时 + 权重（约 2.5GB） |

放大参数是完整的，不是只有 2×/4×：

- **目标档位**：1080p / 1440p / 4K —— 自动算缩放比并保持宽高比
- **同分辨率增强**：4× 推理后回缩到原尺寸，用来提升细节而不是变大
- **降噪强度** 0–1（`hqdn3d`，在模型之前生效）
- **保留音轨** 可选
- **场景切分**：rule / hybrid / fallback。切分规则与参考实现逐条对齐——
  <1.2s 的镜头并入相邻段，再并 <2.0s 的段，超长镜头按 4s 切分
- **分段进度**：界面上会显示「当前分段 3/8」

### 界面

Material Design 3，中/英双语，深浅色主题。四个页面：**视频导入 / 水印打标 / 效果预览 / 视频放大**。
引擎日志内嵌在应用里，出问题不用翻目录。

---

## 快速开始

### 安装（推荐）

```
dist/installer/Eraser-Setup-0.1.0.exe        668 MB   CPU 版，任何 Win10/11 x64
dist/installer/Eraser-Setup-GPU-0.1.0.exe   1444 MB   带 CUDA 运行时，NVIDIA 机器快 7 倍
```

双击安装，无需 Python / ffmpeg / CUDA 工具包。详见 [安装与验收](docs/07-install-and-verify.md)。

> 目前**没有代码签名**，Windows SmartScreen 会拦一次，选「更多信息 → 仍要运行」。
> 校验哈希：`release.py` 生成的 SHA-256 在发布页写明。

### 免安装

```
dist/installer/win-unpacked/Eraser.exe
```

和安装版是同一个程序，只是不写注册表、不建快捷方式。安装包做的事只是把这棵目录复制到
`%LOCALAPPDATA%\Programs\Eraser` 并登记卸载项。

### 从源码跑

```bash
git clone https://github.com/Drew1811266/Eraser.git
cd Eraser

python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe scripts/fetch-assets.py      # LaMa / Real-ESRGAN 权重 + ffmpeg

npm --prefix desktop install
npm --prefix desktop run build
npm --prefix desktop start                            # 启动应用
# 或
npm --prefix desktop run dev                          # Vite 热更新 + Electron
```

### 命令行（不开界面也能出片）

```bash
.venv/Scripts/python.exe -m engine.cli info
.venv/Scripts/python.exe -m engine.cli probe  "D:/videos/clip.mp4"
.venv/Scripts/python.exe -m engine.cli process "D:/videos/clip.mp4" --rect 480,20,120,60
```

`--rect` 是 `x,y,宽,高`（视频像素）；`--frames 0:239` 只处理一段；`--output` 指定输出文件。

---

## 系统要求

| 项目 | 最低 | 推荐 |
|---|---|---|
| 系统 | Windows 10 x64 | Windows 11 x64 |
| 内存 | 8 GB | 16 GB |
| 显卡 | 任意（回退 CPU） | NVIDIA，6 GB+ 显存 |
| 磁盘 | 1.6 GB（安装后） | 6 GB（含 SeedVR2 运行时） |

- GPU 加速走 **CUDA**（安装时选 GPU 版）。没有 NVIDIA 卡就是纯 CPU，能跑但慢。
- SeedVR2 需要机器上有 **Python 3.12**（应用会在用户目录建独立虚拟环境，不动系统 Python）。
  没有 3.12 时界面会直接告诉你，而不是装到一半失败。

---

## 架构

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│  Electron 外壳              │        │  Python 引擎                 │
│                             │ stdio  │                              │
│  React UI（原项目界面）     │◄──────►│  LaMa 修复 · Real-ESRGAN     │
│  preload 桥（window.wmr）   │ JSON-  │  SeedVR2 运行时 · ffmpeg     │
│  主进程：窗口/对话框/设置   │  RPC   │  任务/进度/取消              │
└─────────────────────────────┘        └──────────────────────────────┘
                 ▲                                   ▲
                 └────────── contract/ ──────────────┘
                        （单一事实源，代码生成两侧类型）
```

**外壳不实现算法，引擎不实现 UI，两者只通过 `contract/` 通信。**

- `contract/methods.json` + `contract/errors.json` 定义全部方法、参数、错误码；
  `codegen` 生成 `engine/generated/contract.py` 和 `desktop/electron/generated/contract.ts`。
- `scripts/check.sh` 第一道门禁就是「生成物与契约无漂移」——接口不可能两边不一致。
- 引擎是独立进程，崩溃不会带走界面；`tasks` 统一管理进度与协作式取消。

设计取舍记录在 ADR 里：[运行时策略](docs/adr/0001-runtime-strategy.md)、
[为什么用 stdio 而不是本地 HTTP](docs/adr/0002-engine-contract.md)、
[预览播放器怎么办](docs/adr/0003-portable-preview.md)、
[为什么带 GPL 版 ffmpeg](docs/adr/0004-ffmpeg-distribution.md)、
[为什么出两个安装包](docs/adr/0005-packaging-and-gpu.md)、
[超分为什么走 ONNX](docs/adr/0006-realesrgan-via-onnx.md)。

---

## 验证到什么程度

```bash
bash scripts/check.sh          # 快速门禁（约 15 秒）
bash scripts/check.sh --full   # 全部门禁，含真实推理、真实浏览器、真实模型、安装包解包（约 15 分钟）
```

**15 道门禁 / 414 项检查，全部通过：**

| # | 门禁 | 检查项 | 证明什么 |
|---|---|---|---|
| 1 | 契约同步 | — | 生成物与 `contract/` 无漂移 |
| 2 | 引擎单测 | 174 | 平台层、契约、标注、任务、分块拼接、SeedVR2 状态机 |
| 3 | 协议端到端 | 48 | 引擎 ↔ 外壳 JSON-RPC：噪声、崩溃、超时、抽帧、标注、模型下载 |
| 4 | 外壳类型 + 单测 | 25 | IPC/preload 契约、`window.wmr` 适配层、进度换算 |
| 5 | 真实视频管线 | 41 | 合成水印片段 → 真实推理 → 逐帧核对；含竖屏+音轨、取消、坏输入、CJK 路径、并发 |
| 6 | 超分 | 16 | 分辨率、帧数、内容保真、锐度、档位、同尺寸增强、切分、音轨保留 |
| 7 | 真实浏览器 UI | 20 | 移植后的界面在真实 Chromium 里跑完整流程 |
| 8 | 真实 Electron 应用 | 11 | 真窗口、真 preload、真 IPC、真引擎、真出片 |
| 9 | 黄金用例基线 | 18 | 防止画质退化（明确区分 baseline 与 reference） |
| 10 | 场景切分 vs 参考实现 | 9 | 切点与分段与原 Python 实现一致 |
| 11 | 标注归一化 vs 参考实现 | 6 | 12 个边界矩形用例逐条一致 |
| 12 | SeedVR2 驱动 | 13 | 命令拼装、进度解析、取消、出片发现、任务接线（对桩 CLI） |
| 13 | **SeedVR2 真实模型** | 11 | 真实权重出 1920×1080，帧数与音轨完整 |
| 14 | **去水印 vs 参考实现** | 3 | 同素材同标注，逐帧对比 |
| 15 | 安装包 | 19 | 解包两个安装包 → 校验载荷 → 用包里的引擎真实出片 |

### 几个实测数字

| 项目 | 结果 |
|---|---|
| LaMa 单帧（512×512） | CUDA **47 ms** / CPU 657 ms |
| 48 帧去水印 | GPU 9.1 s / CPU 66.7 s |
| Real-ESRGAN 640×360 → 2560×1440 | ≈0.5 s/帧 |
| SeedVR2 4 帧 320×180 → 1920×1080 | 14 s（RTX 5090，Q4_K_M） |
| **去水印输出 vs 参考实现** | **SSIM 0.9947 / PSNR 47.69 dB** |

最后一行是最有意义的一条：把原项目的 Python 实现和本移植版指向同一段素材、同一份标注，
逐帧比对。阈值是 SSIM ≥ 0.98 / PSNR ≥ 40 dB。两者不会逐比特相同——原版用 TorchScript 版 LaMa，
本移植版用 ONNX 版，推理后端不同；这条数据说明的是「擦掉的是同一块像素」，不是「字节一致」。

mac 端黄金样本目前是空缺，`scripts/golden.py` 会把用例标成 `baseline`（只证明没退化）或
`reference`（才证明一致），并在汇总里直说「0 reference case(s)」——不做「全绿」的假象。

---

## 已知限制

诚实列出来，而不是等你踩到：

- **GPU 只有 NVIDIA 走 CUDA**。AMD / Intel 显卡回退 CPU，能跑但慢（没有 DirectML 后端）。
- **SeedVR2 要单独装**（约 2.5GB）+ 需要 Python 3.12。装好之前界面会明确显示缺什么。
- **预览对 MKV / HEVC 有缺口**：部分容器/编码在应用内播不了，但**处理本身支持**——
  出片不受影响，只是预览黑屏。
- **一个标注段一个矩形**。同一时间要擦两处，就加两个标注段。
- **没有自动更新**，升级需要重新安装。
- **没有代码签名**，首次运行会被 SmartScreen 拦一次。
- **mac 端黄金样本缺失**：质量对位目前靠「与原实现逐帧对比」+ 两套逻辑对位，不是靠 mac 出片。
- 只在 640×360 / 1280×720 的素材上做过端到端验证；4K 长片没有实测过。

---

## 项目结构

```
contract/            契约：方法、错误码 + 代码生成器（单一事实源）
engine/              Python 引擎
  rpc/               JSON-RPC 服务端、错误、协议
  methods/           每个方法一个模块（system/annotation/media/process/upscale/model…）
  pipeline/          去水印、超分、SeedVR2、场景切分、标注归一化
  platform/          Windows 适配：路径、ffmpeg、CUDA、看门狗
  generated/         由 contract/ 生成，勿手改
desktop/             Electron 外壳
  electron/          主进程、preload、IPC、wmr 适配层
  renderer/          原项目的 React 界面
  shared/            两侧共享的常量
scripts/             门禁与工具（check.sh、e2e-*、parity-*、release.py）
tests/               引擎单测
docs/                架构、决策（ADR）、测试报告、安装与验收
golden/              黄金用例（输入 + 标注 + 期望输出）
packaging/           PyInstaller spec、模型转换
```

---

## 开发

```bash
npm --prefix desktop run dev        # Vite 热更新
npm --prefix desktop run typecheck  # 两侧类型检查
npm --prefix desktop test           # 外壳单测
.venv/Scripts/python.exe -m unittest discover -s tests -t .   # 引擎单测
bash scripts/check.sh              # 全部快速门禁
```

**改契约的流程**：改 `contract/methods.json` → 跑 `contract/codegen/generate.py` →
两侧类型自动更新 → 第一道门禁会拦住忘记重新生成的情况。

**加方法的流程**：契约里声明 → 引擎里 `server.register` → 外壳 `wmrCore` 映射 →
两边各加测试。声明了但没实现的方法必须返回结构化 `NOT_IMPLEMENTED`，
而不是从列表里消失——界面永远知道「有哪些能力存在、哪些还没到」。

### 在这个沙箱/无桌面环境里跑

Electron 需要交互式桌面会话。没有的话用浏览器挡板跑同一份构建产物 + 真实引擎：

```bash
node scripts/dev-preview.mjs       # http://127.0.0.1:5299/
```

---

## 文档

| 文档 | 回答什么 |
|---|---|
| [01 现状审计](docs/01-as-is-audit.md) | 原项目是什么？哪些是 mac 专属？Windows 上卡在哪？ |
| [02 目标架构](docs/02-target-architecture.md) | 模块怎么分？契约怎么定？ |
| [03 迁移计划](docs/03-migration-plan.md) | 先做什么后做什么？每步怎么验收？ |
| [04 风险登记](docs/04-risk-register.md) | 哪些坑会拖垮计划？ |
| [05 黄金样本手册](docs/05-golden-set-runbook.md) | 怎么录基准？怎么判定「没变差」？ |
| [06 执行计划](docs/06-execution-plan.md) | 任务表与逐项进度 |
| [07 安装与验收](docs/07-install-and-verify.md) | 装哪个包、怎么装、怎么确认真的在用 GPU |
| [08 测试报告](docs/08-test-report.md) | 414 项检查分别测了什么、修了什么、没测到什么 |
| [变更记录](CHANGELOG.md) | 每个版本有什么、验证到什么程度 |

---

## 许可证与致谢

- 本项目基于 [Drew1811266/Mac-Remover](https://github.com/Drew1811266/Mac-Remover)（MIT）移植，
  界面与算法来自该项目，遵循其许可证。
- 图像修复模型 [LaMa](https://github.com/advimman/lama)（Apache-2.0）。
- 超分模型 [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)（BSD-3-Clause）。
- SeedVR2 运行时来自 [numz/ComfyUI-SeedVR2_VideoUpscaler](https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler)，
  权重来自 AInVFX / numz 的发布页，各自遵循其原许可。
- 视频编解码使用 **GPL 版 ffmpeg**，因此分发包按 GPL 处理；构建脚本与来源见
  [ADR-0004](docs/adr/0004-ffmpeg-distribution.md)。

如果这个项目对你有用，去给原项目点个 star。
