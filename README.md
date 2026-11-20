# Eraser

Windows 桌面端视频去水印工具。基于既有 mac 项目 [Mac-Remover](https://github.com/Drew1811266/Mac-Remover) 的移植与重构。

> 当前阶段：**架构规划完成，等待关键决策确认后进入 M0。**

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
| [ADR-0001 运行时策略](docs/adr/0001-runtime-strategy.md) | 为什么选 Electron + Python，而不是纯重写？ |
| [ADR-0002 引擎契约](docs/adr/0002-engine-contract.md) | 为什么用 stdio 而不是本地 HTTP？ |
| [ADR-0003 视频预览](docs/adr/0003-portable-preview.md) | mac 的原生播放器在 Windows 上怎么办？ |

---

## 三个需要你先拍板的决策

1. **运行时策略**：Electron + Python 引擎（推荐）/ pywebview 快速复用 / 纯 TS 重写。
2. **首版范围**：Real-ESRGAN 是否必须内置？SeedVR2 是否允许延后？
3. **目标机器**：仅 NVIDIA，还是要支持 AMD/Intel 核显？Win10 是否在范围内？

详见 [docs/03-migration-plan.md](docs/03-migration-plan.md) 与对话中的决策清单。

---

## 最紧急的一件事

**在改任何代码之前，先用现有 mac 版录制 6 条「黄金样本」**（输入 + 标注 + 期望输出 + 耗时）。

重构一旦开始，"正确结果"就只存在于这个样本里。没有它，整个迁移过程无法验收。

---

## 仓库状态

```
docs/        ✅ 架构文档（已完成）
contract/    ⬜ 契约层（M1）
desktop/     ⬜ Electron + React（M1/M3）
engine/      ⬜ Python 引擎（M1/M2）
golden/      ⬜ 黄金样本（M0，最优先）
```

`.reference/` 保存了参考仓库的关键源码快照，仅本地保留，不进入版本库。
