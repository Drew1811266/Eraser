# 03 · 迁移计划：从 Mac-Remover 到 Eraser

> 前置决策：`adr/0001-runtime-strategy.md`（Electron 外壳 + Python 引擎）
> 本文档回答三个问题：**先做什么、每步怎么验收、什么时候算完成。**

---

## 0. 计划的基本假设

| 项 | 假设 | 若不成立的影响 |
|---|---|---|
| 人力 | 1 名主程 + AI 辅助 | 工期 ×2–3 |
| 起点 | 可参考原仓库，但不共享代码库（新建 Eraser） | —— |
| 目标 | Windows 10/11 x64 优先，mac 不阻塞 | —— |
| 关键前提 | **原有 mac 版仍可运行** | 若已无法运行，黄金样本只能靠人工标注，验收成本显著上升 |

---

## 1. 一个时间敏感的动作（请优先确认）

**在动任何代码之前，必须从现有 mac 版录制「黄金样本」（golden set）。**

一旦开始重构，你就失去了"正确答案"。黄金样本是整个迁移期唯一的客观验收依据，内容包括：

```
golden/
  cases/
    case01_1080p_h264_single_roi/     input.mp4  + annotations.wmr.json + expected.mp4
    case02_1080p_aac_multi_roi/       ...
    case03_mkv_no_audio/              ...
    case04_edge_roi_out_of_bounds/    ...
    case05_portrait_720x1280/         ...
    case06_long_clip_60s/             ...
  meta.json                           # 每条用例的 model_id / 参数 / 耗时 / 环境
```

验收方式：**PSNR/SSIM 阈值 + 人工抽帧目视**，而不是逐比特比对（编码器与硬件不同，逐比特不可能相等）。

> 如果 mac 版已经跑不起来：退化为"用 Python 引擎在 mac 上跑一遍产出基准"，仍可完成，但需要先修复环境。

---

## 2. 里程碑总览

```
M0 基线冻结      ██                        2–3 天
M1 契约与骨架    ████                      1 周
M2 引擎 Windows 化 ██████                  2 周   ← 最高风险
M3 可用的 Windows 版 ██████                2 周   ← 第一个可交付版本
M4 资产与分发    ████                      1 周
M5 超分引擎      ██████                    2 周（仅 Real-ESRGAN）
M6 质量与发布    ████                      1 周
────────────────────────────────────────────────
到 M3 可用          约 5 周
到功能等价（M6）    约 9 周
```

**M3 是分水岭**：此时 Windows 上已经可以完成"导入 → 标注 → 去水印 → 导出"全链路，只是超分仍不可用。

---

## 3. M0 · 基线冻结（2–3 天）

**目标**：把"现在的正确行为"固定下来，并让后续每一步都可对比。

| # | 任务 | 交付物 |
|---|---|---|
| M0.1 | 在 mac 上录制 6 条黄金样本（见 §1） | `golden/cases/*` + `meta.json` |
| M0.2 | 记录当前环境的性能基线（每条用例耗时、峰值内存） | `golden/meta.json` 的 `baseline` 段 |
| M0.3 | 建立 Eraser 仓库骨架、`.gitignore`、CI 雏形 | 见 §4 |
| M0.4 | 固化参考仓库快照（commit sha + 关键文件镜像） | `.reference/`（不入 git） |

**验收门 G0**
- [ ] 6 条用例齐全，`input.mp4` + `expected.mp4` + `annotations.wmr.json` 三者可对应
- [ ] 基准耗时已记录（后续性能回归以此为分母）
- [ ] `main` 分支可空跑 CI（lint + 单元测试占位）

---

## 4. 目标仓库结构

```
Eraser/
├─ docs/                     # 本套架构文档
├─ contract/                 # 唯一契约源
│   ├─ methods.json
│   ├─ errors.json
│   ├─ models.json
│   └─ codegen/              # 生成 TS 类型 + Python 模型
├─ desktop/                  # Electron + React
│   ├─ electron/
│   │   ├─ main.ts
│   │   ├─ preload.ts
│   │   └─ services/{engineClient,preview,workspace,settings}.ts
│   └─ src/                  # 渲染进程（React）
├─ engine/                   # Python 引擎
│   ├─ rpc/                  # server / registry / trace
│   ├─ pipeline/             # probe / extract / inpaint / stabilize / encode / mux
│   ├─ engines/{inpaint,upscale}/
│   ├─ platform/             # paths / ffmpeg / device / processes / files / logging
│   ├─ assets/               # manifest / download / verify
│   └─ main.py
├─ assets/                   # 构建期资产（ffmpeg、模型 manifest，不入 git 二进制）
├─ golden/                   # 黄金样本（LFS 或外部存储）
├─ scripts/                  # 构建、打包、诊断
└─ .github/workflows/
```

---

## 5. M1 · 契约与骨架（1 周）

**目标**：先让"两个进程能对话"，再谈功能。

| # | 任务 | 关键点 | 交付物 |
|---|---|---|---|
| M1.1 | 定义 `contract/methods.json` v1 | 只收 12 个方法（见 §5.1），不追求全量 | methods.json |
| M1.2 | 写代码生成器 | 生成 TS interface + Python pydantic 模型 | `contract/codegen/` |
| M1.3 | 实现 Python RPC server | stdout 只走协议；`--rpc-trace` 落帧 | `engine/rpc/` |
| M1.4 | 实现 TS `EngineClient` | 请求超时、进程重启、事件分发 | `desktop/electron/services/engineClient.ts` |
| M1.5 | `ping` / `capabilities` 端到端打通 | 能启动引擎、拿到版本、干净退出 | 单测 + 手动 demo |

### 5.1 v1 方法清单（刻意最小）

```
system.ping                  → {pong:true}
system.capabilities          → {platform, ffmpeg, devices[], engines[]}
media.probe                  → {width,height,fps,frame_count,duration,has_audio}
annotation.normalize         → 规范化后的 segments
process.start                → {task_id}
process.cancel               → {ok}
process.status               → {state, phase, progress, ...}
model.list                   → 模型清单与安装状态
model.install                → {task_id}
assets.verify                → {ok, missing[]}
```
进度统一通过 `progress` 事件，不通过轮询。

**验收门 G1**
- [ ] `desktop` 启动 → 拉起 `engine` → `capabilities` 返回 → 关闭时子进程无残留
- [ ] 引擎打印到 stderr 的日志不会破坏协议（注入 1000 行噪音压测）
- [ ] 契约生成物与源一致（CI 校验 diff 为空）
- [ ] 杀掉引擎进程后，外壳 3 秒内感知并给出可操作提示

---

## 6. M2 · 引擎 Windows 化（2 周，最高风险）

**目标**：让 Python 引擎在 Windows 上**独立跑通**，不依赖 Electron。

> 这一阶段**先做 CLI**：`python -m engine.cli process --input ... --annotations ... --output ...`。
> 有了 CLI，一切都能在无 UI 的情况下回归测试，这是整个计划可执行的关键。

| # | 任务 | 现状缺陷 | 验收 |
|---|---|---|---|
| M2.1 | `platform/paths.py` | `config.py` 用 `/tmp`、`~/Downloads` | 全部路径来自 `%LOCALAPPDATA%`/`%TEMP%`/已知文件夹 |
| M2.2 | `platform/ffmpeg.py` | 平台键 `windows-x86_64`、缺 `.exe` | 放入 `vendor/ffmpeg/win32-x64/` 后可被定位；PATH 缺失时能明确报错 |
| M2.3 | `platform/device.py` | MPS 分支、`PYTORCH_MPS_*` | CUDA 可用时用 CUDA，否则 CPU；显存查询跨平台 |
| M2.4 | `platform/processes.py` | `SIGTERM` 取消 | 用 Token 取消；子进程用 `CREATE_NO_WINDOW` 不闪黑框 |
| M2.5 | `platform/files.py` | 删除被占用文件失败 | 原子写 + 重试 + 长路径前缀 |
| M2.6 | 移除运行时 `pip install` | `remover.py:90-96` | 无任何网络安装行为；缺依赖必须启动即失败并给出明确提示 |
| M2.7 | 切 ProPainter 死代码 | 396 处引用 | 删除后引擎体积与启动时间下降，且黄金样本全部通过 |
| M2.8 | 拆 `process_video` | 单方法约 2000 行 | 拆为 6 个阶段模块，行为不变（黄金样本为准） |
| M2.9 | 打包 torch（CPU 版） | —— | `--onedir` 产物可在纯净 Win11 虚拟机启动 |

**验收门 G2**
- [ ] 6 条黄金样本在 Windows 上**全部跑通**，SSIM 均 ≥ 阈值（建议 0.98，逐用例可调）
- [ ] 无网络环境下可完成一次完整去水印（离线可用）
- [ ] 长片（≥10 min）不 OOM、临时目录可清理
- [ ] 在**没有安装 Python 的干净 Windows 机器**上能跑（证明打包自洽）

---

## 7. M3 · 可用的 Windows 版（2 周）

**目标**：把 M2 的引擎接到 Electron 外壳，交付第一个可给用户体验的版本。

| # | 任务 | 现状 | 交付物 |
|---|---|---|---|
| M3.1 | `processor.ts` → `EngineClient` 调用 | 现为内嵌 ONNX 实现 | 删除重复实现，统一走引擎 |
| M3.2 | 预览双通道 | ADR-0003 | `<video>` + 帧服务自适应 |
| M3.3 | 修复预览相关 Windows 缺陷 | `HOME` 兜底、`SIGTERM` | 路径与取消统一 |
| M3.4 | 标注 sidecar 对齐 | 双端各写一份规范化 | 共享测试向量，双端结果一致 |
| M3.5 | 打包 NSIS | 现仅 `target:["nsis"]` | 有图标、有版本信息、可安装可卸载 |
| M3.6 | 首启引导 | —— | 缺 ffmpeg/模型时给出**可执行**的下一步，而非报错 |

**验收门 G3（第一个对外版本）**
- [ ] 干净 Win11 安装 → 导入 mp4 → 画 ROI → 处理 → 播放结果 → 打开输出目录，全程无手写命令
- [ ] MKV 文件不黑屏（走帧服务通道并提示）
- [ ] 关闭窗口后无残留进程（任务管理器验证）
- [ ] 卸载后用户数据目录行为明确（保留 or 清理，需产品决策）

---

## 8. M4 · 资产与分发（1 周）

| # | 任务 | 要点 |
|---|---|---|
| M4.1 | FFmpeg Windows 构建 | 选定 LGPL 构建以规避 GPL 传染；记录编译选项与源码获取方式 |
| M4.2 | 模型资产仓库 | 大文件移出代码仓库；`models.json` 含 sha256/尺寸/许可证 |
| M4.3 | manifest 签名 | 现有 `modelManager` 已校验 sha256，补 manifest 签名 |
| M4.4 | 按需下载 UX | 断点续传、失败重试、代理设置、国内可达性 |
| M4.5 | 安装包体积优化 | 剔除 torch 中无用组件；CUDA 作为独立可选组件 |

**验收门 G4**
- [ ] 基础安装包 ≤ 700 MB
- [ ] 弱网/断网下模型下载有明确状态与恢复
- [ ] 许可证清单随安装包分发

---

## 9. M5 · 超分引擎（2 周，可与 M3/M4 并行）

> 这是**唯一需要重新选型**的部分。原实现依赖 mac 上预构建的 py312 venv，不可复用。

**已定范围（2026-11-20 决策）：v1 只交付 Real-ESRGAN；SeedVR2 不进 v1。**

| 引擎 | 路径 | 状态 |
|---|---|---|
| Real-ESRGAN | ONNX Runtime（CUDA EP 为主，CPU 回退） | **v1 交付** |
| SeedVR2 3B | GGUF runner | **不在 v1 范围** |

处置原则：
- SeedVR2 在 UI 上明确标注「当前版本不支持」，**不再以静默 blocked 的形式存在**（这是原仓库的产品缺陷）。
- 如果后续要做 SeedVR2，它应当作为**一次独立的引擎接入**被执行——这正是 ADR-0001 契约解耦的价值所在：届时不需要改 UI。

**验收门 G5**
- [ ] Real-ESRGAN 在 CUDA 与 CPU 双路径出图质量通过目视评审
- [ ] 显存不足时有降级策略（分块处理或提示切换 CPU），不直接崩溃

---

## 10. M6 · 质量与发布（1 周）

| # | 任务 |
|---|---|
| M6.1 | 全量黄金样本回归（含性能基线对比，退化 >20% 即阻断） |
| M6.2 | 崩溃转储与诊断包（`Eraser.exe --diagnose` 生成 zip） |
| M6.3 | Authenticode 签名 + 安装包冒烟 |
| M6.4 | 文档：安装、常见问题、支持的格式矩阵 |
| M6.5 | 灰度发布与回滚方案 |

**验收门 G6（发布）**
- [ ] SmartScreen 不拦截（签名有效）
- [ ] 3 台不同配置机器（N 卡 / 核显 / 纯 CPU）安装并跑通
- [ ] 黄金样本 SSIM 全部达标，性能退化在阈值内

---

## 11. 依赖关系与并行度

```
M0 ──► M1 ──► M2 ──► M3 ──► M4 ──► M6
                 │      │
                 └──────┴──► M5（可并行）
```

- **M1 与 M2 可由两人并行**（契约定义 vs 平台层改造），接口以 `methods.json` 为准。
- **M5 与 M3/M4 并行**，但必须在 M6 前完成，否则从"功能等价"清单中移除并对外说明。

---

## 12. 首批具体任务（可直接开工）

| 顺序 | 任务 | 产出 | 预计 |
|---|---|---|---|
| 1 | 录制黄金样本 | `golden/cases/*` | 0.5 天 |
| 2 | 建仓库骨架 + `.gitignore` + CI | 目录结构 | 0.5 天 |
| 3 | 写 `methods.json` v1（12 个方法） | 契约源 | 1 天 |
| 4 | 起 Python RPC server + `ping` | 可启动的引擎 | 1.5 天 |
| 5 | 起 Electron 最小外壳 + `EngineClient` | 端到端 ping | 1.5 天 |
| 6 | 移植 `media.probe`（ffmpeg 探测） | 第一条真实能力 | 1 天 |
| 7 | `platform/ffmpeg.py` + 放入 Windows ffmpeg | 工具可定位 | 1 天 |
| 8 | CLI `process` 命令跑通单条用例 | 首个端到端结果 | 2 天 |

> 第 8 项完成时，你已经拥有一个**能在 Windows 上真正去水印的命令行工具**——这是整个计划的风险出清点。

---

## 13. "完成"的定义

本项目在满足以下全部条件时才算完成移植：

1. **功能等价**：原 mac 版的全部用户可见能力，在 Windows 上有对应实现或有明确降级说明。
2. **质量等价**：6 条黄金样本 SSIM 达标，人工目视无可察觉退化。
3. **性能可接受**：RTX 级别 GPU 上处理 1080p 60s 片段 ≤ 2 分钟；纯 CPU 有明确预期提示。
4. **可分发性**：签名安装包，干净机器可装、可跑、可卸载。
5. **可维护性**：新增一个引擎不需要改 UI；新增一个能力不需要改两处契约。
