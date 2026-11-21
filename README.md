# Eraser

Windows 桌面端视频去水印工具。基于既有 mac 项目 [Mac-Remover](https://github.com/Drew1811266/Mac-Remover) 的移植与重构。

> 当前阶段：**M2（引擎 Windows 化）与 M3（桌面外壳）已完成，可在 Windows 上跑通「打开视频 → 框选 → 擦除 → 出片」全流程。**

---

## 一句话架构

**Electron 外壳（React UI）+ Python 引擎（保留原算法，stdio JSON-RPC 解耦）+ 契约层（单一事实源）。**

外壳不实现算法，引擎不实现 UI，两者只通过 `contract/` 通信。这样既保住了已有的 4966 行调参逻辑，又让后续逐个替换引擎成为可能。

---

## 文档导航

| 文档 | 回答什么问题 |
|---|---|
| [01 · 现状审计](docs/01-as-is-audit.md) | 原项目到底是什么？哪些是 mac 专属？Windows 上会卡在哪？ |
| [02 · 目标架构](docs/02-target-architecture.md) | 新架构长什么样？模块怎么分？契约怎么定？ |
| [03 · 迁移计划](docs/03-migration-plan.md) | 先做什么后做什么？每步怎么验收？多久能出可用版？ |
| [04 · 风险登记](docs/04-risk-register.md) | 哪些坑会拖垮计划？怎么提前处置？ |
| [05 · 黄金样本手册](docs/05-golden-set-runbook.md) | M0 具体怎么录基准？怎么判定"没变差"？ |
| [ADR-0001 运行时策略](docs/adr/0001-runtime-strategy.md) | 为什么选 Electron + Python，而不是纯重写？ |
| [ADR-0002 引擎契约](docs/adr/0002-engine-contract.md) | 为什么用 stdio 而不是本地 HTTP？ |
| [ADR-0003 视频预览](docs/adr/0003-portable-preview.md) | mac 的原生播放器在 Windows 上怎么办？ |
| [ADR-0004 ffmpeg 发行版](docs/adr/0004-ffmpeg-distribution.md) | 为什么要用 GPL 构建，而不是更干净的 LGPL？ |
| [06 · 执行计划](docs/06-execution-plan.md) | M2–M6 的任务表、验收门槛与逐项进度 |
| [07 · 安装与验收](docs/07-install-and-verify.md) | 装哪个包、怎么装、怎么确认它真的在用 GPU |
| [变更记录](CHANGELOG.md) | 这个版本有什么、验证到什么程度、哪些没做 |
| [08 · 测试报告](docs/08-test-report.md) | 255 项检查分别测了什么、本轮修了什么、哪些没测到 |
| [ADR-0005 打包与 GPU](docs/adr/0005-packaging-and-gpu.md) | 为什么是 onedir，为什么出两个安装包 |
| [ADR-0006 超分走 ONNX](docs/adr/0006-realesrgan-via-onnx.md) | 为什么不复刻参考实现的 torch 运行时 |

---

## 已确认的决策（2026-11-20）

| 决策 | 结论 | 影响 |
|---|---|---|
| 运行时策略 | **Electron 外壳 + Python 引擎**（stdio JSON-RPC） | 算法零重写；[ADR-0001](docs/adr/0001-runtime-strategy.md) 已生效 |
| 首版超分范围 | **仅 Real-ESRGAN**；SeedVR2 不在 v1 范围 | M5 从 2–3 周收窄到 2 周，取消最大不确定性 |
| 目标机器基线 | **仅 NVIDIA + Win10/11 x64** | 直上 CUDA，不引入 DirectML；AMD/Intel 回退 CPU |

这意味着：**M1–M4 没有分支了，可以线性推进。**

---

## 最紧急的一件事

**在改任何代码之前，先用现有 mac 版录制 6 条「黄金样本」**（输入 + 标注 + 期望输出 + 耗时）。

重构一旦开始，"正确结果"就只存在于这个样本里。没有它，整个迁移过程无法验收。

---

## 仓库状态

```
docs/        ✅ 架构文档（6 篇 + 4 个 ADR）
contract/    ✅ 契约 v1（12 方法）+ 代码生成器
engine/      ✅ Python 引擎：RPC + 平台层 + 移植后的 LaMa 管线（10 个方法可用）
desktop/     ✅ Electron 外壳：主进程、preload 桥、React UI
golden/      ⬜ 黄金样本（M0，需在 mac 上执行）
tests/       ✅ 99 个引擎单测 + 20 个界面单测 + 31 项协议检查 + 23 项视频检查
```

## 如何验证

```bash
bash scripts/check.sh           # 快速门禁（约 15 秒）
bash scripts/check.sh --full    # 全部门禁，含真实推理与真实浏览器（约 2 分钟）
node scripts/e2e-desktop.mjs    # 真实 Electron 窗口点一遍（需要交互式桌面）
```

| 门禁 | 内容 | 数量 |
|---|---|---|
| 契约一致性 | 代码生成产物与 `contract/` 无漂移 | 1 |
| 引擎单测 | 平台层、契约、标注、任务、分块拼接 | 113 |
| 协议端到端 | 引擎 ↔ 外壳 JSON-RPC（噪声、崩溃、超时） | 31 |
| 外壳类型 + 界面单测 | 状态机、进度、取消、结果、超分（jsdom）；IPC/preload 契约 | 31 |
| 视频端到端 | 合成水印片段 → 真实推理 → 逐帧核对；含竖屏+音轨、取消、坏输入、CJK 路径、并发 | 41 |
| 超分端到端 | 分辨率、帧数、内容保真、锐度对比 | 5 |
| 真实浏览器 UI | 构建产物 + 真实引擎 + 真实文件 | 18 |
| 安装包验收 | 解包两个安装包 → 校验载荷 → 用包内引擎真实出片；并检查包不比代码旧 | 17 |

合计 **256 项**，全部通过。

> `e2e-renderer.mjs` 用 Playwright 的 chromium 加载 `desktop/dist-renderer` 的**构建产物**，
> 把 `window.eraser` 接到真实引擎的 JSON-RPC 上，因此除了 Electron 的 IPC 层之外，
> 整条链路都是真的。`e2e-desktop.mjs` 才是完整形态，但它需要交互式桌面：
> 本会话的沙箱里 Electron 连 `about:blank` 都渲染不了（见 `docs/06` §8）。

## 怎么用

### 直接装（发布物）

```
dist/installer/Eraser-Setup-0.1.0.exe        663 MB   CPU 版，任何机器
dist/installer/Eraser-Setup-GPU-0.1.0.exe   1439 MB   带 CUDA，NVIDIA 机器快 7 倍
```

双击安装即可，无需 Python / ffmpeg / CUDA 工具包。详见 [07 · 安装与验收](docs/07-install-and-verify.md)。

### 从源码跑桌面应用

```bash
cd desktop
npm install
npm run build
npm start
```

### 命令行（不开 UI 也能出片）

```bash
.venv/Scripts/python.exe -m engine.cli info
.venv/Scripts/python.exe -m engine.cli probe "D:/videos/clip.mp4"
.venv/Scripts/python.exe -m engine.cli process "D:/videos/clip.mp4" --rect 480,20,120,60
```

`--rect` 是 `x,y,宽,高`（视频像素），`--frames 0:239` 可只处理一段，`--output` 指定输出文件。

## 当前可用的引擎方法

| 方法 | 状态 |
|---|---|
| `system.ping` / `system.capabilities` / `system.shutdown` | ✅ 已实现 |
| `annotation.normalize` | ✅ 已实现（与参考实现逐条对齐） |
| `media.probe` / `process.start` / `process.cancel` / `process.status` | ✅ 已实现（真实推理 + 进度通知 + 协作式取消） |
| `model.list` / `assets.verify` | ✅ 已实现（含 Real-ESRGAN） |
| `upscale.start` | ✅ 已实现（Real-ESRGAN，2×/4×，复用任务生命周期） |
| `model.install` / `model.installStatus` | ⬜ 契约已声明，返回结构化 `NOT_IMPLEMENTED` |

> 声明了但未实现的方法会明确返回 `NOT_IMPLEMENTED` 而不是消失——这样 UI 永远知道"有哪些能力存在、哪些还没到"。这是对原仓库"静默 blocked"的直接修正。

`.reference/` 保存了参考仓库的关键源码快照，仅本地保留，不进入版本库。
