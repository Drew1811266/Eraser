# 02 · 目标架构：Eraser on Windows

> 目标：Windows 10/11 x64 上交付一款与 mac 版**功能等价**的视频去水印桌面应用，并在交付后可持续演进。
> 原则：**契约先行、引擎可替换、平台差异收敛到一层。**

---

## 1. 设计目标与非目标

### 目标
- **G1 功能等价**：导入 → 标注 ROI → LaMa 去水印 → 保留音轨 → 导出；设置、最近文件、标注 sidecar 全部对齐。
- **G2 可演进**：引擎实现（Python / ONNX / 原生）能在不改 UI 的前提下替换。
- **G3 可诊断**：出问题能拿到结构化日志与可复现输入，而不是"用户说不行"。
- **G4 可分发**：单个已签名安装包，首次可离线完成去水印（超分模型按需下载）。

### 非目标（首版明确不做）
- 不做在线服务/云端推理。
- 不做 mac/linux 回归（保留但不阻塞 Windows 交付）。
- 不做多用户/工程管理/批量队列（二期）。
- 不重写算法本身。

---

## 2. 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│  Desktop Shell  (Electron 42 · TypeScript)                   │
│                                                              │
│  Renderer (React 18 + zustand)                                │
│    ├─ 时间轴 / ROI 画布 / 对比预览 (<video> + Canvas)          │
│    └─ 只认 window.eraser.*（preload 暴露的类型化 API）         │
│                                                              │
│  Main Process                                                 │
│    ├─ 窗口/菜单/对话框/文件关联/单实例锁                        │
│    ├─ PreviewService   帧服务（按需解码 → JPEG dataURL）       │
│    ├─ WorkspaceService 标注 sidecar 读写、设置持久化            │
│    └─ EngineClient  ────────────┐                             │
└─────────────────────────────────┼────────────────────────────┘
                                  │  JSON-RPC 2.0 / stdio
                                  │  （新行分隔，无 TCP 端口）
┌─────────────────────────────────▼────────────────────────────┐
│  Engine  (Python 3.12 · 可冻结为 onedir)                      │
│                                                              │
│  rpc/server.py     方法注册 / 参数校验 / 进度事件 / 取消        │
│  pipeline/         probe → extract → inpaint → stabilize →    │
│                    encode → mux（由巨型 process_video 拆分）    │
│  engines/          inpaint: lama-torch | lama-onnx            │
│                    upscale: realesrgan | seedvr2              │
│  platform/         ← 唯一的 OS 差异层                          │
│     paths · ffmpeg · device · processes · files · logging     │
│  assets/           manifest 校验 / 下载 / 安装（sha256）        │
└──────────────────────────────────────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
   vendor/ffmpeg/win32-x64/   models/ (用户数据目录)     native/ (可选)
   ffmpeg.exe ffprobe.exe     big-lama.onnx 等            wmr_native.node
```

**关键点：外壳不实现任何算法，引擎不实现任何 UI。** 两者唯一的耦合面是 `contract/`。

---

## 3. 模块边界与职责

| 模块 | 位置 | 职责 | 禁止做的事 |
|---|---|---|---|
| `desktop/` | Electron + React | 交互、渲染、文件对话框、进度展示 | 直接调用 ffmpeg、直接读模型、拼命令行 |
| `contract/` | JSON Schema + 生成代码 | 方法签名、参数/返回结构、错误码 | 依赖任一端实现 |
| `engine/` | Python | 视频处理、模型推理、资产校验 | 弹窗、读 UI 设置、访问 Electron API |
| `assets/` | 构建产物 | ffmpeg、模型、manifest | 提交进 git |
| `vendor/` | 第三方二进制 | 仅在构建时进入安装包 | 运行时从网络获取可执行文件 |

### 3.1 契约层（单一事实源）

```
contract/
  methods.json          # 方法名、参数 schema、返回 schema、是否长任务
  errors.json           # 错误码枚举 + 用户可读文案 key
  models.json           # 模型清单（id/引擎/文件名/尺寸/sha256/许可证）
  codegen/              # → TS 类型 + Python dataclass/pydantic 模型
```

生成物**提交进仓库**，CI 校验「生成物与源一致」，避免两端手工漂移（当前 `api.py` 69 方法 vs IPC 32 通道的漂移就是反面教材）。

### 3.2 为什么是 stdio 而不是 localhost HTTP

| 方案 | 结论 |
|---|---|
| **stdio（选定）** | 无端口占用、**不触发 Windows Defender 防火墙弹窗**、生命周期天然跟随父进程、无需鉴权 |
| localhost HTTP | 首次运行必弹防火墙授权；需处理端口冲突与本地鉴权 |
| 原生 FFI (napi) | 崩溃即拖垮整个应用；GIL/线程模型复杂；无法隔离 torch |

stdio 的代价是需要自己实现「请求-响应-事件」三路复用，但这在 JSON-RPC 上是标准做法。

### 3.3 进程模型

- **引擎单进程 + 线程池**：LaMa 推理是 CPU/GPU 密集，用固定线程数（默认 `min(4, cores-1)`）批处理帧。
- **超分走独立 worker 子进程**：SeedVR2 显存波动大、易 OOM，隔离进程可在崩溃后只失败该任务。仓库已有 `upscale_processor` + worker 形态，保留该设计。
- **取消语义**：统一用 `CancellationToken`（协作式检查点），**不依赖信号**。Windows 没有 SIGTERM 语义，`child.kill()` 是 TerminateProcess，会留下半成品文件。
- **崩溃恢复**：引擎启动时扫描 `work/` 下的孤儿临时目录并清理。

---

## 4. 平台抽象层（`engine/platform/`）——移植的核心工作量

把全部 OS 差异收敛到 6 个模块，**其余代码一律不得出现 `sys.platform` / `os.name` / 硬编码路径**。

| 模块 | 必须解决的问题 | 现有缺陷 |
|---|---|---|
| `paths.py` | 用户数据目录、缓存、临时、日志、默认输出目录 | `config.py` 用 `/tmp` 与 `~/Downloads`；`remover.py` 用 `~/.cache/torch` |
| `ffmpeg.py` | 平台键、`.exe` 后缀、可执行校验、版本探测、缓存 | `ffmpeg_runtime.py` 的 `_platform_key()` 在 Windows 产出错误键且不带 `.exe` |
| `device.py` | CUDA 可用性、显存查询、FP16 判定、CPU 回退、显存水位 | MPS 分支与 `PYTORCH_MPS_*` 需与 CUDA 分支并列抽象 |
| `processes.py` | 子进程启动/终止/超时/隐藏控制台窗口 | `SIGTERM`、`CREATE_NO_WINDOW` |
| `files.py` | 原子写、重命名、长路径（`\\?\`）、删除被占用文件重试 | Windows 上「打开着的文件不可删」会让预览缓存清理与模型下载 rename 失败 |
| `logging.py` | 轮转日志、崩溃转储、诊断打包 | 需新增 |

### 平台键统一规范（唯一权威）

```
win32-x64      ← macOS/MacIntel 之外的新增
win32-arm64    （预留）
darwin-arm64
darwin-x86_64
linux-x86_64
```

Electron 侧 `getRuntimePlatformKey()` 已经采用这套命名，**Python 侧必须对齐它**，而不是相反。同时修正 `nativeCore.ts` 忽略 arch 的缺陷。

---

## 5. 数据契约

### 5.1 标注 sidecar（保持向后兼容）

沿用 `*.wmr.json`，字段与 Python `annotations.py` 一致：文件指纹（SHA1）+ 视频元信息 + `segments[]`（`rect`、`start_frame`、`end_frame`、`enabled`、`expand_px`）。

**必须做的加固**：把「规范化」逻辑（裁剪越界坐标、帧区间排序、缺省值填充）从两端各写一遍，改为**契约层定义 + 双端共享测试向量**。现有 `functional:smoke` 已覆盖边界 ROI 越界场景，直接复用为跨语言一致性测试。

### 5.2 进度事件

```ts
type ProgressEvent = {
  task_id: string;
  phase: 'probe'|'extract'|'infer'|'stabilize'|'encode'|'mux'|'finalize';
  progress: number;        // 0..1，单调递增
  processed: number;
  total: number;
  eta_seconds: number | null;
  message: string;
};
```
约束：**单调不减**、`task_id` 全程一致、终态必发一次 `finalize`。仓库已有 `progress_estimator.py`，保留其 ETA 平滑算法。

### 5.3 错误模型

```json
{ "code": "ENGINE_MODEL_MISSING", "message_key": "err.model_missing",
  "detail": {"model_id": "lama_torch"}, "retryable": false }
```
- 面向用户的**只有 `message_key`**，i18n 文案在渲染进程。
- 堆栈只进日志，不出现在 UI 与 IPC。

---

## 6. 部署与打包

```
Eraser-Setup-0.1.0-x64.exe
├─ resources/app.asar              Electron 主进程 + 渲染进程
├─ resources/engine/               PyInstaller onedir（python312.dll + torch + 代码）
├─ resources/vendor/ffmpeg/win32-x64/ffmpeg.exe | ffprobe.exe
└─ （模型不内置，首次使用按需下载）
```

| 决策 | 选择 | 理由 |
|---|---|---|
| Python 分发 | `--onedir`（非 onefile） | onefile 每次启动解压 1–3 GB，首启可达数十秒，且高频被杀软误报 |
| 安装形态 | NSIS，per-user 安装 | 免 UAC 提权，降低企业环境阻力 |
| 体积控制 | torch 仅 CPU 版内置；CUDA 版作为可选组件下载 | 基础包可控在 600 MB 内；N 卡用户再拉 CUDA 包 |
| 签名 | Authenticode（EV 或 OV） | 无签名必触发 SmartScreen 拦截，直接影响转化 |
| 自动更新 | electron-updater + 签名 feed | 二期 |

> **torch 体积是硬约束**：`torch` CPU wheel 解压后约 300 MB，CUDA wheel 约 2.5 GB。这就是「CUDA 作为可选项」而非内置的原因。

---

## 7. 性能预算（验收基线）

以 `1080p / 60s / 单个 320×120 ROI` 为基准片：

| 场景 | 目标 | 备注 |
|---|---|---|
| 冷启动到可交互 | ≤ 3 s | 引擎懒启动，UI 先出 |
| 首帧预览 | ≤ 500 ms | 帧服务按需解码 |
| LaMa 去水印（RTX 3060, CUDA） | ≤ 90 s | 约 25 fps 有效吞吐 |
| LaMa 去水印（8 核 CPU） | ≤ 8 min | 需明确提示用户 |
| 峰值内存 | ≤ 4 GB | 帧按批落盘，不整片入内存 |
| 长片（30 min）不崩溃 | 必须 | 分片 + 断点续跑 |

**架构含义**：必须保留「提取帧到磁盘 → 分批推理 → 重新编码」的流水线（现有设计），而不是「整片解码进内存」。这一点不要为了"简洁"改掉。

---

## 8. 安全与合规

- 渲染进程：`contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`（现为 `false`，需收紧）、严格 CSP、禁止加载远程内容。
- 引擎：不监听任何端口；只接受父进程 stdio；文件操作限制在用户选择目录 + 应用数据目录。
- 资产：所有模型下载必须 **sha256 校验 + manifest 签名**，失败即拒绝（现有 `modelManager` 已校验 sha256，需补 manifest 签名）。
- 许可：Big-LaMa 权重（Apache-2.0）、Real-ESRGAN（BSD-3）、SeedVR2（需逐一确认）、FFmpeg（LGPL/GPL，取决于编译选项）。**LGPL 版本的 ffmpeg 需附源码获取途径**，这是发布前的法务门槛。

---

## 9. 与现有仓库的对应关系

| 现有资产 | 在 Eraser 中的归宿 |
|---|---|
| `src/core/*.py` | 迁入 `engine/`，切 ProPainter、拆 `process_video` |
| `src/gui/api.py` | **废弃**，其 69 个方法收敛为 `contract/methods.json` 的方法子集 |
| `src/gui/native_player.py` | **废弃**，由 Chromium `<video>` + 帧服务替代（见 ADR-0003） |
| `src/gui/templates/dist` | 迁入 `desktop/` 的构建产物 |
| `frontend/electron/*` | 保留骨架，`processor.ts` 改为 `EngineClient` 调用 |
| `frontend/electron/services/inpaintOnnx.ts` | **保留为 `lama-onnx` 引擎的参考实现**（后续可选迁移） |
| `models/` | 移出 git，改为资产仓库 + manifest |
| `vendor/ffmpeg/darwin-*` | 保留但不进入 Windows 包；新增 `win32-x64` |
