# Eraser

Windows 桌面端视频去水印工具。基于既有 mac 项目 [Mac-Remover](https://github.com/Drew1811266/Mac-Remover) 的移植与重构。

> 当前阶段：**架构规划完成，三项关键决策已确认（2026-11-20），可进入 M0 基线冻结。**

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
docs/        ✅ 架构文档（7 篇）
contract/    ✅ 契约 v1（12 方法）+ 代码生成器
engine/      ✅ Python 引擎（M1 部分：RPC 骨架 + 4 个可用方法）
desktop/     🟡 EngineClient 已实现并通过 E2E；Electron 外壳待装依赖后接线
golden/      ⬜ 黄金样本（M0，需在 mac 上执行）
tests/       ✅ 72 个单元测试 + 31 项端到端检查
```

## 如何验证

```bash
bash scripts/check.sh
```

依次执行：契约产物一致性 → 引擎单元测试 → 引擎与外壳的端到端契约检查。
三项全绿才算一个里程碑完成。当前输出：**72 tests OK / 31 checks passed**。

## 当前可用的引擎方法

| 方法 | 状态 |
|---|---|
| `system.ping` / `system.capabilities` / `system.shutdown` | ✅ 已实现 |
| `annotation.normalize` | ✅ 已实现（与参考实现逐条对齐） |
| `media.probe` / `process.*` / `model.*` / `assets.verify` | ⬜ 契约已声明，返回结构化 `NOT_IMPLEMENTED` |

> 声明了但未实现的方法会明确返回 `NOT_IMPLEMENTED` 而不是消失——这样 UI 永远知道"有哪些能力存在、哪些还没到"。这是对原仓库"静默 blocked"的直接修正。

`.reference/` 保存了参考仓库的关键源码快照，仅本地保留，不进入版本库。
