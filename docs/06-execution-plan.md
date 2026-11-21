# 06 · 执行计划（Master Plan）

> 本文件锁定「怎么从当前状态做到可交付的 Windows 应用」，并记录所有已拍板的技术选型。
> 上层文档：`01` 审计 · `02` 架构 · `03` 里程碑 · `04` 风险 · `05` 黄金样本
> 本文件是执行期唯一需要对照的路线图，随进展滚动更新。

---

## 1. 已锁定的技术决策

| # | 决策 | 结论 | 理由 |
|---|---|---|---|
| D1 | 引擎推理后端 | **onnxruntime，不引入 torch** | LaMa 有 208MB 的 ONNX 权重；onnxruntime 安装约 60MB，torch(CUDA) 约 2.5GB。启动快、包体小、无 CUDA/cuDNN 版本地狱 |
| D2 | 去水印模型 | `lama_fp32.onnx`（HuggingFace `Carve/LaMa-ONNX`，208MB） | 已验证可下载；原仓库 `best.ckpt` 只是 134 字节的 LFS 指针，权重实际不在仓库里 |
| D3 | FFmpeg | BtbN `win64-gpl` 构建（**原定 lgpl，因缺 libx264 变更，见 ADR-0004**） | 已验证源可达；`lgpl` 变体无 `libx264`，管线无法写出成品 |
| D4 | Electron 版本 | 45.x（当前 stable 45.4.1） | npm 已验证可达；外壳不装任何原生模块，无 ABI 重建问题 |
| D5 | 前端形态 | React + Vite，参考仓库 `frontend/src` 作为功能基线 | 已有约 200KB 成熟 UI 代码可迁移，不必从零设计 |
| D6 | 打包 | PyInstaller `--onedir` + electron-builder NSIS | 见 `02` §6 |
| D7 | 模型分发 | 主包不含模型；首次使用按需下载 + sha256 校验 | 主包控制在 ~200MB |
| D8 | 超分范围 | v1 仅 Real-ESRGAN（ONNX） | 已确认，见 README |

> **D1 是本次执行中最重要的偏离**：原实现用 torch + TorchScript/SimpleLama。改为 ONNX 后，引擎依赖从 ~2.5GB 降到 ~120MB，且 Windows 上无 GPU 驱动版本耦合。代价是需要验证 ONNX 与原 torch 路径的输出一致性——用 PSNR/SSIM 判定，不追求逐比特。

---

## 2. 完成的定义（Definition of Done）

一个可交付的 Windows 应用，满足：

1. **能装**：NSIS 安装包，双击安装，开始菜单可见，可卸载。
2. **能用**：导入 mp4 → 预览 → 画 ROI → 去水印 → 导出带音轨的 mp4，全程无命令行。
3. **能验**：`scripts/check.sh` 全绿；合成夹具上的端到端处理通过 PSNR 阈值。
4. **能诊断**：出问题有日志文件 + `--diagnose` 诊断包。
5. **能说清**：README 写明支持格式、已知限制、模型下载方式。

不在 DoD 内（明确延后）：SeedVR2、Florence-2 自动识别、批量处理、自动更新、代码签名（无证书，见 §6 风险）。

---

## 3. 没有黄金样本怎么办（M0 的现实处置）

M0 需要 mac 机器，当前不可得。**不因此停工**，改用三级替代验证：

| 级别 | 手段 | 覆盖什么 |
|---|---|---|
| L1 | 合成夹具（ffmpeg 生成的水印视频）+ 已知 ROI | 管线是否跑通、输出是否保留音轨/时长/分辨率 |
| L2 | **自一致性**：同一输入重复运行，输出逐比特一致 | 非确定性 bug（随机种子、并发竞态） |
| L3 | 视觉抽帧：首/中/末帧导出 PNG 人工看 | 接缝、色偏、残影 |

真实质量基线仍需 mac 黄金样本，**在它到位前，M2 的算法改动只做"不改变数值行为"的移植**（改导入、改路径、换推理后端），不做算法调参。这样即使没有黄金样本，也不会把质量改坏。

---

## 4. 里程碑与任务

### M2 · 引擎 Windows 化（当前阶段）

**目标**：`python -m engine.cli process --input a.mp4 --annotations a.wmr.json --output out.mp4` 在 Windows 上产出正确结果。

| # | 任务 | 交付物 | 验收 |
|---|---|---|---|
| M2.1 | 依赖与资产落地 | `requirements.txt`、`vendor/ffmpeg/win32-x86_64/`、`scripts/fetch-assets.py` | ffmpeg -version 可跑；模型 sha256 正确 |
| M2.2 | `platform/ffmpeg.py` | 平台键定位、`.exe`、版本探测、缺失时报 `FFMPEG_MISSING` | 单测：目录存在/不存在/错误架构 |
| M2.3 | `platform/paths.py` | `%LOCALAPPDATA%`、`%TEMP%`、输出目录、长路径 | 单测：不出现 `/tmp`、`~` 硬编码 |
| M2.4 | `platform/processes.py` | 无窗口子进程、超时、协作式取消 | 单测：取消后不留子进程 |
| M2.5 | `platform/files.py` | 原子写、占用重试、启动清理孤儿 | 单测：被占用文件的删除重试 |
| M2.6 | 移植 `annotations.py` | sidecar 读写 + 指纹 | 与 `annotation.normalize` 行为一致 |
| M2.7 | 移植 `video_processor.py` | `pipeline/` 下的探测/抽帧/推理/合成/编码 | 合成夹具端到端产出 |
| M2.8 | 切 ProPainter 死代码 | 396 处引用移除 | 移植后 import 不失败、体积下降 |
| M2.9 | 拆 `process_video` | 6 个阶段模块 | 行为不变（L1+L2 通过） |
| M2.10 | `media.probe` / `process.*` 接契约 | RPC 方法可用 | 端到端脚本通过 |
| M2.11 | CLI | `engine/cli.py` | 无 UI 可完成一次处理 |

**门 G2**：合成夹具上完成一次去水印，输出可播放、时长与音轨一致；重复运行输出一致；`check.sh` 全绿。

### M3 · 可用的 Windows 外壳

| # | 任务 | 验收 |
|---|---|---|
| M3.1 | Vite + React 骨架 | 空白窗口能跑起来 |
| M3.2 | 主进程接线：引擎拉起/就绪/退出回收 | 关窗后无残留进程 |
| M3.3 | 文件导入 + 元数据 + 预览（双通道） | mp4/mkv 都能看 |
| M3.4 | ROI 画布 + 时间轴 + 标注持久化 | 重开视频能恢复 |
| M3.5 | 处理进度 + 取消 + 结果预览 | 进度单调，取消即停 |
| M3.6 | 设置 / 最近文件 / 输出目录 | 重启后保持 |

**门 G3**：干净机器上从安装到导出全流程可用。

### M4 · 资产与分发

`scripts/fetch-assets.py`、模型下载 UI、sha256 校验、安装包体积优化、许可证清单。

### M5 · Real-ESRGAN

ONNX 模型接入，CUDA/CPU 双路径，显存不足降级，质量目视评审。

### M6 · 发布

`--diagnose`、日志、安装包冒烟、文档、灰度与回滚。

---

## 5. 执行顺序（为什么这样排）

```
M2.1 资产 ──┬─► M2.2/M2.3/M2.4/M2.5 平台层（互相独立，可并行）
            └─► M2.6 标注 ─► M2.7 管线移植 ─► M2.8 切死代码 ─► M2.9 拆方法
                                                        └─► M2.10 接契约 ─► M2.11 CLI
M3 在 M2.10 之后才能接真能力；但 M3.1 骨架可与 M2 并行。
```

**先做 M2.1 的原因**：没有 ffmpeg 和模型，后面每一项都无法验证。资产是"可验证性"的前提。

---

## 6. 本阶段的风险处置

| 风险 | 处置 |
|---|---|
| ONNX 与原 torch 输出有差异 | 用 SSIM/PSNR 对比两者在同一帧上的输出；阈值 0.98。若低于阈值，回退为 torch 路径（保留 `engines/inpaint/torch`） |
| 240KB 管线移植引入回归 | 只做机械移植（改导入/路径/后端），不调参；每步跑 L1+L2 |
| 模型下载失败/被墙 | 提供 `scripts/fetch-assets.py` 支持本地文件与镜像 URL |
| 无代码签名证书 | 主程序不签名，文档说明；企业分发另行处理 |
| 无 mac 黄金样本 | 见 §3 |

---

## 7. 进度记录

| 里程碑 | 状态 | 备注 |
|---|---|---|
| M1 契约与骨架 | ✅ 完成 | 72 单测 + 31 端到端检查 |
| M2 引擎 Windows 化 | ✅ 完成 | 真实管线跑通；CUDA 47ms/帧，CPU 657ms/帧；23 项视频检查 |
| M3 外壳 | ✅ 完成 | Electron 主进程 + preload 桥 + React UI；20 项界面单测；真机脚本待桌面会话 |
| M4 资产分发 | ✅ 完成 | PyInstaller onedir + NSIS；CPU 663MB / GPU 1440MB 双包，见 ADR-0005 |
| M5 超分 | ✅ 完成 | Real-ESRGAN 转 ONNX，复用 onnxruntime；7 单测 + 5 项端到端检查，见 ADR-0006 |
| M6 质量与发布 | ✅ 完成 | 8 道门禁全绿，共 256 项检查；安装包与发布说明已产出 |

---

## 8. 环境事实（已验证，2026-11-20）

| 项 | 事实 |
|---|---|
| Python | 3.13.15，pip 可达 PyPI |
| Node | 24.21.0，npm 11.19.0，registry 可达（electron 45.4.1） |
| 磁盘 | D: 剩余 440GB |
| GPU | RTX 5090 D v2（24455MB，驱动 610.88，CUDA 13.3） |
| GPU 推理 | onnxruntime-gpu 1.32 + `nvidia-*` CUDA 13 运行时；LaMa 512×512：CUDA 47ms/帧 vs CPU(8 线程) 657ms/帧 |
| ffmpeg | `vendor/ffmpeg/win32-x86_64`（BtbN **gpl** 构建，见 ADR-0004） |
| Electron | 45.4.1 已安装；**本会话内无法渲染**：连 `about:blank` 都会在 renderer 初始化时崩溃（0x80000003），已用最小复现排除窗口创建、GPU、JIT、sandbox 等成因 |
| 浏览器 | Playwright chromium 可正常渲染；`scripts/e2e-renderer.mjs` 用它加载**构建产物**并接真实引擎，覆盖除 Electron IPC 之外的全部链路 |
| 模型 | 仓库内 `best.ckpt` 仅为 LFS 指针，**权重不在仓库**；改用 HuggingFace ONNX |
| 注意 | `timeout <cmd>` 无法执行 Windows `.cmd`（npm），会返回 126 —— 排查时勿被误导 |
