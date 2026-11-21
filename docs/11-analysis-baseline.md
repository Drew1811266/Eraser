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
| 分析耗时 | **152 ms** |
| 引擎单测 | **283 个**，约 2.5 s（M1 前 174，新增 109） |
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

- **管线连通**：抽帧 → 检测接口 → 坐标回映射 → 跨帧关联 → 镜头切分 → 建议 → JSON。
- **算法确定**：同一输入重复运行结果一致（无随机性）。
- **规则本身由单测覆盖**（不是由这条基线覆盖）：
  - 1.5 s 合并窗口 / 5 s 重开窗口 / 超窗新建 —— `tests/test_analysis_association.py`
  - 切点强制断开区间、运动按区间计算 —— 同上 `ShotBoundaryTests`
  - 8 track 上限**按置信度**保留 —— 同上 `test_track_cap_keeps_the_most_confident`
  - 下采样帧的坐标回映射 —— `tests/test_analyze_methods.py` 的 1600×200 集成用例
  - 短镜头合并、蒙太奇不折叠 —— `tests/test_analysis_shots.py`

## 3. 不能证明什么

- **检测质量**：脚本检测器只是回放固定框，召回/误报没有意义。
- **动态水印**：逐帧掩码要等 M4（`watermark.py` 的 4 处掩码构造改造）。
- **真实素材**：夹具是合成片段，不能代表创作者素材的复杂度。
- **界面链路**：M1 没有 UI，分析报告/时间线/编辑栏在 M3。

> 真实检测器的召回率、误报数、track 准确率，由 M2 的
> `scripts/eval-analysis.py` 在 7 组合成素材上度量，结果记入
> `docs/12-quality-baseline.md`。

## 4. 独立审查后的修复（M1 收口）

首个 M1 分支通过了 244 条单测与全部门禁，但独立审查发现一个 **Critical**：

| 编号 | 问题 | 修复 |
|---|---|---|
| C1 | 下采样帧的检测框从未映射回原坐标，1080p 素材上所有 rect 都会错位 | `rescale_detections()` + 集成用例（1600 宽夹具断言 400→500） |
| I1 | 所有候选 IoU 为 0 时中心距回退不可达，小水印每帧被拆成新 track | `_match_candidate()`：IoU 优先、最近中心距兜底 |
| I2 | 重开只看尺寸和间隔，两个相似水印共漏一帧会互换身份 | 重开评分纳入位置；跨切点才允许忽略位置 |
| I3 | 关联不知道切点，运动跨镜头聚合 | `associate(shots=…)`；切点强制新区间、运动按区间计算 |
| I4 | 切点前后不强制采样、短镜头不合并、竖屏不缩放 | 切点补帧、短镜头合并（蒙太奇不折叠）、按最长边缩放 |
| I5 | `normalize` 丢掉 `keyframes`，sidecar 往返后轨迹消失 | `normalize_segments` 保留并归一化 keyframes（无则不加字段） |
| I6 | `DETECTOR_MISSING` 未进契约，被报成内部错误 | `contract/errors.json` 增 `-32006/-32007` |
| I7 | 自由对象在 TS 生成物里是 `Record<string, never>` | 改为 `Record<string, unknown>`；契约新增 `Analysis` 类型 |
| M1–M5 | cap 不按置信度、建议规则缺失、static 未收敛为单矩形、`frame_count` 未用、检测脚本健壮性 | 全部修正并补测试 |
| R1 | 复核发现：同帧重开用 50% 位移门限绕过了 8% 关联规则，会把相隔 160px 的两个水印并成一条 | 同镜头内统一用 8% 关联窗口，跨切点才放宽；新增 2 条回归测试 |
| R2–R5 | 跨切点评分与文档不符、近距 track 交叉绑定、切点补帧未含前一帧、坐标空间无说明 | 评分权重区分同镜头/跨镜头、改为全局贪心分配、补 `cut-1`、CLI 与脚本注明坐标空间 |
| N1 | 二次复核发现：8% 窗口也套在「间隔数秒后重新出现」上，违反 spec §4.3 规则 3 | 8% 只管连续性（≤1.5s）；长间隔回到 50% 合理性上限，允许移动水印移位后再出现 |

## 5. 已知限制

- **长视频内存**：采样步长会按 `MAX_SAMPLES=240` 自动放大，避免一次性持有上千帧；极端长片仍可能占用数百 MB。
- **取消响应**：`process.cancel` 在 ffmpeg 抽帧与切点扫描期间不会立即中断子进程（任务状态仍会正确落到 `cancelled`，只是要等当前 ffmpeg 返回）。切点补帧是逐个 seek（上限 64 帧、单次 60s），同样不可中断。修复需要给 `sample_frames`/`detect_scene_cuts` 传取消事件并终止子进程，留给 M2。
- **检测脚本的坐标空间**：`--detections` / `ERASER_DETECT_STUB` 里的框是**检测器看到的帧**的坐标（可能已被缩到 1280 宽），引擎会把它映射回源视频。按源视频坐标手写脚本，在宽 >1280 的素材上会被再放大一次。
- **检测器**：只有脚本桩；真实模型未接。`DETECTOR_FAILED` 错误码尚未加入契约——真实检测器接入时再连同异常包装一起加。
