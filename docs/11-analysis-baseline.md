# 11 · 分析管线基线（M1）

- **日期**：2026-11-21
- **对应计划**：[docs/10-implementation-plan.md](10-implementation-plan.md)
- **夹具**：`.cache/fixture.mp4`（640×360，24fps，48 帧）
- **检测源**：脚本化检测（`--detections` / `ERASER_DETECT_STUB`），**不是真实视觉模型**

---

## 1. 实测

| 项目 | 结果 |
|---|---|
| 采样帧数 | 4（step=15，帧 0 / 15 / 30 / 45） |
| 镜头数 | 1 |
| 水印 track 数 | 1（`wm-1`，`static`，区间 0–30） |
| 分析耗时 | **205 ms** |
| 引擎单测 | **244 个**，约 2.0 s |
| 协议端到端 | 48/48 |
| 外壳类型 + UI 测试 | 25/25 |
| 快速门禁 | 4/4，`ALL GATES PASSED` |

复现命令：

```bash
export ERASER_TEMP_DIR="$(cygpath -w "$PWD/.cache/tmp")"
.venv/Scripts/python.exe -m engine.cli analyze .cache/fixture.mp4 \
    --detections .cache/cli-detections.json
```

## 2. 这组数字能证明什么

- 管线连通：抽帧 → 检测接口 → 跨帧关联 → 镜头切分 → 建议 → JSON。
- 算法确定：同一输入重复运行结果一致（无随机性）。
- 关联规则按 spec 生效：1.5 s 合并窗口、5 s 重开窗口、8 个 track 上限、置信度与面积过滤。

## 3. 不能证明什么

- **检测质量**：脚本检测器只是回放固定框，召回/误报没有意义。
- **动态水印**：逐帧掩码要等 M4（`watermark.py` 的 4 处掩码构造改造）。
- **真实素材**：夹具是合成片段，不能代表创作者素材的复杂度。
- **界面链路**：M1 没有 UI，分析报告/时间线/编辑栏在 M3。

> 真实检测器的召回率、误报数、track 准确率，由 M2 的
> `scripts/eval-analysis.py` 在 7 组合成素材上度量，结果记入
> `docs/12-quality-baseline.md`。
