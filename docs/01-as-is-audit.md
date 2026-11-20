# 01 · 现状审计：Mac-Remover 到底是什么

> 审计对象：`github.com/Drew1811266/Mac-Remover`，主线提交 `fe02ca7`
> 审计方式：全量文件树 + 运行时关键源码逐文件通读 + 平台耦合全仓扫描
> 结论先行：**这不是一个「mac 版 Python 应用」，而是一次停在半路的架构迁移。**

---

## 1. 最重要的认知修正

你记忆中的项目形态是「Python + pywebview，只能在 mac 上跑」。实际情况是仓库里同时躺着**两套并行实现**，且新实现只完成了约 10% 的能力：

| | Python 实现（`src/`） | Electron 实现（`frontend/`） |
|---|---|---|
| 状态 | **功能完整**，是当前唯一可用的引擎 | **骨架完成，引擎被掏空** |
| UI | pywebview 加载 React 构建产物 | Electron 加载同一套 React 源码 |
| 桥接 | `api.py` js_api，**69 个方法** | preload + IPC，**32 个通道** |
| 去水印 | LaMa (PyTorch)，**4966 行**流水线 | LaMa ONNX，约 400 行 |
| 超分 | Real-ESRGAN + SeedVR2，独立 py312 venv | **全部硬编码 `implemented:false`，直接返回失败** |
| 预览 | AVFoundation 原生播放器（mac 专属）+ OpenCV 帧服务 | 帧服务（`preview.ts`） |
| 打包 | （未完成） | electron-builder，mac 只配了 dmg |

这意味着：**「把 mac 版移植到 Windows」的真实命题是「把一次失败的重写收尾，并补齐 Windows 运行时」。** 直接照 README 的说法开工会走错方向。

---

## 2. 代码规模与重心

```
4966 行  src/core/video_processor.py     ← 去水印主流水线（含大量死代码）
2640 行  src/gui/api.py                  ← 前后端契约层
1644 行  src/core/upscale_processor.py   ← 超分编排（分片、内存策略、profile）
 282 行  src/core/remover.py             ← LaMa 封装
 193 行  src/config.py                   ← 配置单例
```

Electron 侧全部 TypeScript 合计约 **100 KB 源码**（`frontend/electron/services/*`），体量约为 Python 引擎的 **1/5**，且只覆盖 LaMa 一条路径。

**架构含义**：算法价值几乎全部集中在 Python 侧。任何「用 TypeScript 重写一遍」的方案，等于重写 4966 行经过调参的计算机视觉逻辑——这是本项目最大的成本陷阱。

---

## 3. macOS 耦合点清单（全仓扫描，共 8 处 DARWIN + 8 处 pyobjc + 11 处 pywebview）

### 3.1 真正硬绑定的只有 GUI 预览层

| 位置 | 耦合内容 | 性质 |
|---|---|---|
| `src/gui/native_player.py:17-21` | `AVFoundation` / `AVKit` / `AppKit` / `CoreMedia` 导入 | **硬绑定，Windows 无法运行** |
| `src/gui/native_player.py:90,112` | `AVPlayer.playerWithURL_`、`AVPlayerView` | 原生播放窗口实现 |
| `src/gui/main_thread_dispatch.py` | AppKit 主线程派发（原生播放器/对话框） | 半绑定 |
| `src/gui/window.py:213` | `gui='webkitgtk' if os.name != 'darwin' else None` | **写反了**：Windows 会走 Linux 的 webkitgtk 分支 |
| `src/gui/api.py` | `darwin` 分支、AVPlayer 状态查询、主线程判断 | 集中在原生播放器/对话框 |

### 3.2 GPU 后端假设（影响性能，不影响能否启动）

| 位置 | 内容 |
|---|---|
| `src/utils/device.py:43-48` | 设备优先级 `mps → cuda → cpu`。**这个顺序在 Windows 上天然正确**（mps 不可用自动落到 cuda/cpu） |
| `src/utils/device.py:67-108` | `_get_mps_info()` 读 Metal 统一内存，Windows 无意义 |
| `src/core/seedvr_runtime.py` | `PYTORCH_MPS_HIGH_WATERMARK_RATIO` / `_LOW_` 环境变量、`device=mps` 日志文案 |
| `src/core/remover.py:79-81` | MPS + TorchScript 不稳定 → 回退 CPU（Windows 上该分支永不命中，无害） |
| `src/utils/memory_cleanup.py` | `release_unified_memory`（Metal 统一内存） |

### 3.3 路径与 OS 语义假设

| 位置 | 问题 |
|---|---|
| `src/config.py:76-89` | 配置目录回退链含 `Path("/tmp")` |
| `src/config.py:49` | 默认输出目录 `~/Downloads/WatermarkRemover` |
| `src/core/remover.py:66` | 模型路径 `~/.cache/torch/hub/checkpoints/big-lama.pt` |
| `frontend/electron/services/processor.ts` | `process.env.HOME` 兜底输出目录；`kill('SIGTERM')` 停止任务 |
| 多处 | `chmod` / `os.access(X_OK)` / `signals` 在 Windows 语义不同或无效 |

---

## 4. Windows 阻断项（按严重度排序）

> 这些是**会直接导致 Windows 版跑不起来或产不出结果**的问题，不是体验优化项。

| # | 阻断项 | 证据 | 影响 |
|---|---|---|---|
| **B1** | 内置 FFmpeg 只有 mac 二进制 | `vendor/ffmpeg/` 下仅 `darwin-arm64`、`darwin-x86_64` | Windows 上找不到 ffmpeg/ffprobe，**所有视频读写失效** |
| **B2** | 平台键推导在 Windows 上必然落空 | `ffmpeg_runtime.py:33-37` 产出 `windows-x86_64`；目录名却是 `win32-x86_64` 风格，且文件名缺 `.exe`；`os.access(X_OK)` 在 Windows 恒真 | 即使放了二进制也定位不到，静默回退 PATH |
| **B3** | `models/runtime/*/venv` 是 darwin 二进制 | 仓库中提交了 `python3.12-darwin` 的 `.so`、`PIL/*.so` 等 | 超分运行时**不可复用**，必须为 Windows 重建 |
| **B4** | Electron 侧无 Native 预编译产物 | `frontend/native/prebuilds/` 只有 `.gitkeep`；`nativeCore.ts` 期望 `<platform>-<arch>/wmr_native.node` | 超分能力检测失败；且 `nativeModuleName()` 忽略 arch 参数（代码缺陷） |
| **B5** | 打包后无 Node 可执行文件 | `processor.ts` 的 `resolveProcessorNodeBinary()` 返回 `'node'` | 打包版找不到子进程解释器，必须改为 `ELECTRON_RUN_AS_NODE` |
| **B6** | 运行时 `pip install` 兜底 | `remover.py:90-96` `subprocess.check_call(['pip','install',...])` | 应用内动态装包，Windows 无 Python 环境下必然失败，且是安全红线 |
| **B7** | `frontend/package.json` 的 win 配置不完整 | 只有 `win.target: ["nsis"]`；无 `icon.ico`，无签名配置，`extraResources` 只带 mac 认识的 ffmpeg 布局 | 打出的安装包无图标、无签名、缺资源 |
| **B8** | 原生播放器无 Windows 实现 | 见 §3.1 | 「原始/处理后对比预览」功能直接缺失 |

---

## 5. 技术债与重构靶点（与平台无关，但会放大移植成本）

| 债务 | 量化 | 处置建议 |
|---|---|---|
| **ProPainter 死代码** | `video_processor.py` 中 **396 处**引用；`models/third_party/ProPainter` 已被删除 | **优先切除**。这是移植期最大的不确定性来源，必须在动算法前清除 |
| **巨型方法** | `process_video()` 横跨约 `2801–4866` 行，单方法约 2000 行 | 按阶段拆分为 `probe → extract → infer → stabilize → encode → mux` |
| **契约双份实现** | `api.py` 69 方法 vs `ipcHandlers.ts` 32 通道，命名与返回结构靠人工对齐 | 抽出**单一契约源**，双端代码生成 |
| **模型 ID 分叉** | Python `SUPPORTED_MODEL_IDS = ("lama_roi",)`（含 `sttn_roi` 别名）；TS 侧 5 个模型 ID，4 个 blocked | 统一为一份 `models.json` 目录清单 |
| **仓库膨胀** | 提交了 `models/runtime/*/venv`（数千文件）、模型权重、双份 ffmpeg | 迁移期就把「代码仓库」与「资产仓库」分离，见 §6 |
| **Git LFS 失配** | README 自述 LFS 管 `*.gguf` 等，但 venv 内 `.so` 大量入库 | 需重定 LFS 规则并历史瘦身（可选） |

---

## 6. 资产现状（决定安装包体积与分发方式）

| 资产 | 位置 | 体积量级 | Windows 可用性 |
|---|---|---|---|
| FFmpeg/FFprobe | `vendor/ffmpeg/darwin-*` | 数十 MB | ❌ 需替换 |
| LaMa 权重 | `models/big-lama`（TorchScript `.pt`）+ `~/.cache` 兜底 | 数百 MB | ⚠️ 可用，但需重新导出为 ONNX 或走 CUDA torch |
| Florence-2 | `models/florence2-base` | 数百 MB | ⚠️ 依赖 transformers/torch，可行 |
| Real-ESRGAN | `models/runtime/realesrgan-py312/` | GB 级（含 venv） | ❌ venv 不可用 |
| SeedVR2 3B GGUF | `models/runtime/seedvr-py312/models/SEEDVR2/` | 单文件 2–3.4 GB | ❌ runner 不存在 |

> **体积警示**：若把 torch(CUDA) + 上述全部模型打进一个安装包，体积会落在 **4–8 GB**。这决定了「一体化安装包」不可行，必须采用**瘦主程序 + 按需下载模型**（仓库里已有 `modelManager` + manifest 的雏形，方向是对的，只是缺 manifest 服务端与资产）。

---

## 7. 审计结论

1. **可移植性比预期好得多**：核心算法层几乎没有平台分支，GPU 探测顺序天然兼容 Windows。真正的 mac 绑定集中在**原生预览播放器**（一个文件）和**打包/资产**（二进制层面）。
2. **不可移植的不是代码，是二进制资产**：venv、ffmpeg、prebuilds 三样东西在 Windows 上必须全部重建，这是工期主体。
3. **最大的风险不是「移植」，而是「半途重写」**：继续沿 Electron 纯 TS 重写路线，等于用 1/5 的代码量去追 4966 行调参逻辑；而仓库里 4 个超分模型全部 blocked 正是这条路走不通的证据。
4. **因此目标架构的第一原则是：把 Python 引擎当作「参考实现」保留下来，用一层稳定契约把它和 Electron 外壳解耦，然后按模块逐个替换。** 详见 `02-target-architecture.md` 与 `adr/0001-runtime-strategy.md`。

---

## 附：本次审计留下的本地证据

`.reference/up/` 下镜像了 23 个关键源码文件（含 `video_processor.py`、`api.py`、`upscale_processor.py`、`device.py`、`ffmpeg_runtime.py`、`native_player.py`、`window.py`、`ipcHandlers.ts`、`preload.ts` 等），作为后续移植的对照基线。该目录已在 `.gitignore` 中排除。
