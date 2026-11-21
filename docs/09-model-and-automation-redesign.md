# 09 · 模型与流程重设计（设计 spec）

- **状态**：待评审（第 13 节列出了已确认 / 待确认的决定）
- **日期**：2026-11-21
- **关联**：`docs/adr/0001`（运行时策略）、`0005`（打包）、`0006`（超分走 ONNX）、`docs/06`（执行计划）
- **前置决策**：使用形态为**个人 / 小范围内使用，不对外分发**；非商业许可（NC）的模型可用，但权重不进入仓库、不进入安装包。

---

## 1. 要解决的两个问题

产品在 macOS 时期做过两次性能妥协。换到 Windows + NVIDIA 显卡后，可以把两次妥协都收回来——但不是简单地"换个模型"，而是让软件先**理解这个视频**，再决定怎么处理。

| # | 当年的妥协 | 现在的目标 |
|---|---|---|
| **P1** | 去水印模型从"高质量档"降到 LaMa（单帧、轻量） | 换回时序修复模型，让擦除结果在时间上连续、不闪烁 |
| **P2** | 让用户手工框选水印位置与时间区间 | 软件自动分析水印情况并给出建议；用户确认或修改后再处理 |

这两件事共享同一条链路：**分析产出掩码 → 修复引擎消费掩码**。

### 1.1 目标（DoD）

1. 用户丢入视频、点击开始后，软件**先分析、后处理**，并把分析结论展示给用户确认。
2. 分析结论包含四件事：**几个水印 / 是否连贯 / 是否移动 / 几个镜头**，外加一条处理建议。
3. 用户可以在**时间线 + 水印编辑栏**里修正任何判断错误，然后才开始处理。
4. 质量档的擦除结果**在时序上明显优于**现有 LaMa 管线（以第 8 节的指标度量）。
5. 检测器与修复引擎都可替换：新增一个模型不改 `process_video` 主循环。
6. 每次质量相关的改动都有**可复现的数值基线**（当前仓库为 0 条 reference 用例，必须补上）。

### 1.2 非目标

- 不重写移植过来的 `engine/pipeline/watermark.py`（4774 行、由 `scripts/port_reference.py` 机械生成）。
- 不做批量队列 / 工程管理 / 云端推理。
- 不改 UI 框架（继续 React 18 + zustand + Material 3）。
- 不做"零点击全自动"——**永远让用户确认分析结果**。

---

## 2. 现状事实（设计依据）

读代码确认过的，不是推测：

| 事实 | 位置 | 对设计的影响 |
|---|---|---|
| 引擎接口 `inpaint_roi_sequence(roi_frames, roi_masks, ...)` **本来就收逐帧掩码列表** | [model_registry.py](../engine/pipeline/model_registry.py#L46)、[watermark.py](../engine/pipeline/watermark.py#L3073) | **动态水印在推理层没有障碍**——卡点只在数据模型 |
| 但两处调用都填同一张模板：`[roi_mask_template.copy() for _ in frames]` | [watermark.py](../engine/pipeline/watermark.py#L3149-L3160) | 说明"静态矩形"是调用方的假设，不是接口的限制 |
| 硬切检测 + 按切点拆序列**已有实现**：`_detect_hard_cuts_in_run` / `_split_sequence_by_cut_indices` | [watermark.py:1005](../engine/pipeline/watermark.py#L1005)、[1258](../engine/pipeline/watermark.py#L1258) | 多镜头情形有现成块件，但目前只挂在 ProPainter 分支 |
| 场景切点另有独立实现 `detect_scene_cuts()` | [upscale_plan.py:163](../engine/pipeline/upscale_plan.py#L163) | 分析阶段的镜头检测可以直接复用 |
| 已有引擎抽象 `BaseInpaintEngine`（`inpaint_roi` / `inpaint_roi_sequence`） | [model_registry.py](../engine/pipeline/model_registry.py#L32-L48) | 新模型只需写子类 |
| `SUPPORTED_MODEL_IDS = ("lama_roi",)`，`_create_engine()` 只 new `LamaRoiEngine` | [model_registry.py](../engine/pipeline/model_registry.py#L120-L132) | 插槽是空的 |
| 管线里保留完整 ProPainter 时序质量逻辑（约 1.5k 行），因 `effective_model_id == 'propainter_roi'` 永不成立而是死路径 | [watermark.py](../engine/pipeline/watermark.py#L1149-L2400)、`#L2779` | 接上引擎即复活；但当年是为 ProPainter 输出调的，需重新验证 |
| `Segment` 只有**一个 `rect` 对应一段帧区间** | [methods.json](../contract/methods.json#L58-L95) | 动态水印必须扩展数据模型（第 5 节） |
| 契约 23 个方法，`process.start` 参数只有 `input_path / output_dir / model_id / segments` | [methods.json](../contract/methods.json#L671) | 分析是独立能力，需要新方法 |
| 界面把模型 ID 硬编码为 `lama_roi` | [App.tsx](../desktop/renderer/App.tsx#L136-L139) | 档位选择需要改白名单 |
| 核心三文件（`watermark.py` / `remover.py` / `model_registry.py`）在 `tests/` 下**零引用** | `tests/` | 改核心前必须先补安全网 |
| 黄金用例 3 条全是 baseline，reference 为 0 | `golden/cases/` | 换模型后没有基线证明"变好" |
| 已有可复用的重运行时安装模式：venv → 固定 commit CLI → 权重 + sha256 → 子进程进度/取消 | [seedvr/runtime.py](../engine/pipeline/seedvr/runtime.py) | 重型修复模型直接复用 |
| 已有帧预览与框编辑工作台（810 行） | [AnnotationWorkspace.tsx](../desktop/renderer/workspace/AnnotationWorkspace.tsx) | 是"水印编辑栏"的雏形，缺时间线与实体概念 |

---

## 3. 总体流程

```
导入视频  →  用户点击「开始处理」
                    │
                    ▼
        ① 分析（analyze.start，独立任务，可取消）
             镜头切点 · 水印检测 · 跨帧关联 · 区间 · 运动判定
                    │
                    ▼
        ② 展示分析结论 + 建议（不是直接处理）
             用户能一眼看出对不对 → 确认，或去编辑
                    │
                    ▼
        ③ 水印编辑栏 + 时间线（可选）
             改区间、拆/合水印、拖动框、删掉误检
                    │
                    ▼
        ④ 编译成 Segment[]（复用 annotation.normalize）
                    │
                    ▼
        ⑤ 处理（process.start，现有管线/进度/取消/编码不变）
```

**硬规则**：第 ② 步不可跳过。软件先给结论，人来判断对错，然后才动手。这条规则覆盖所有情形，包括最简单的"单镜头 + 固定水印"。

---

## 4. 分析层

### 4.1 输出结构

```python
Analysis = {
  "video": {"width", "height", "fps", "frame_count", "duration_seconds"},
  "shots": [{"start_frame": int, "end_frame": int}],          # 至少 1 个
  "watermarks": [WatermarkTrack, ...],                        # 0..N
  "suggested": {
      "engine_id": "lama_roi" | "propainter_roi",
      "split_by_shot": bool,
      "warnings": [str, ...],
  },
  "model_id": str,
  "elapsed_ms": int,
}
```

### 4.2 四个问题分别怎么算

| 问题 | 算法 | 成本 |
|---|---|---|
| **几个镜头** | ffmpeg `scene` 分数扫描切点 → 合并过短镜头（规则与 [build_segments](../engine/pipeline/upscale_plan.py#L260) 一致） | 一次 ffmpeg 扫描，秒级 |
| **几个水印** | 逐采样帧检测 + **跨帧关联**成 track | 见 4.3 / 4.4，主要成本在这里 |
| **是否连贯** | 每个 track 的 `intervals`：只有 1 段 = 全程连续；多段 = 中间消失/淡出 | 关联的副产品 |
| **是否移动** | 区间内 bbox 中心位移（`max` 与 `std`）与尺寸变化率 | 关联的副产品 |

**镜头与区间的关系**：切点会**切断区间**（跨镜头的轨迹不能插值连线），但**不改变 track 身份**——同一个固定角标贯穿三个镜头，仍然是 1 个水印、3 段区间。

### 4.3 跨帧关联（这是本方案最难的一块）

用户已确认语义：**同一个水印在视频里出现→消失→再出现，算 1 个水印、多段区间。**

关联规则（初值，实验校准）：

1. 相邻采样点按 **IoU ≥ 0.5** 匹配；移动较快时回退到**中心距离 ≤ 画面宽 8%** 匹配。
2. 匹配上 → 延续当前区间。
3. 未匹配 → 若与某个已关闭 track 的上次观测满足「间隔 ≤ 5 秒 **且** 尺寸/宽高比相似（±25%）」→ 视为同一水印的**新区间**；否则新建 track。
4. 遇到镜头切点 → 强制断开区间（但 track 身份可延续，用于跨镜头重识别；第一版只做"同类合并"，不做特征重识别）。
5. 面积 > 画面 25% 的框、`confidence < 0.35` 的框不进入结果。
6. 单视频最多 8 个 track，按置信度排序。

> 关联错误（把两个水印并成一个、或把一个拆成两个）是这个阶段最主要的失败模式，
> 也是第 ⑧ 节要有"拆/合"编辑能力的直接原因——用户必须能修。

### 4.4 采样与耗时预算

逐帧检测代价过高（30s × 30fps = 900 帧）。设计：

- 默认**每 15 帧（约 0.5s）**采样一次；切点前后各强制采样一帧。
- 检测在**下采样后的帧**上跑（最长边 1280），框再映射回原坐标。
- 目标：30 秒 1080p 视频的分析在 **10 秒内**完成（含 ffprobe、切点扫描、检测、关联）。
- 分析是可取消的独立任务，进度走 `process.status`。

### 4.5 建议策略

| 分析结论 | 建议 |
|---|---|
| 1 个 track、`static`、1 个镜头 | 快速档即可；`split_by_shot = false` |
| 多个 track 或 `static`、多镜头 | 推荐质量档；`split_by_shot = true` |
| 任一 track 为 `moving` | 必须质量档（逐帧掩码）；提示"轨迹已推断，建议确认" |
| 有低置信度区间 | 在结论里标黄，提示可能漏检 |
| 0 个 track | 不开始处理，提示"未检测到水印"并转到手动打标 |

建议只是**建议**：用户可以在确认界面里改档位，软件不因为"你装了显卡"就偷偷换成慢模型。

---

## 5. 水印实体与数据模型

### 5.1 `WatermarkTrack`（分析层，新）

```python
{
  "id": "wm-1",
  "intervals": [{"start_frame": 0, "end_frame": 899}, ...],   # 多段出现区间
  "motion": "static" | "moving",
  "keyframes": [{"frame": 0, "rect": {x, y, width, height}}, ...],  # 采样点；static 时退化为 1 个
  "confidence": 0.92,
  "source": "auto" | "manual",
  "notes": ["低置信度区间 210-260"],
}
```

- `intervals` 为空表示"用户删掉了这个水印"（保留实体便于撤销）。
- `keyframes` 在 `static` 时只有一个矩形；在 `moving` 时是采样点的轨迹，区间内其余帧由**线性插值**补齐（后续可替换为跟踪算法，接口不变）。

### 5.2 编译到处理层（`Segment`，不变）

```
WatermarkTrack (多区间 + 轨迹)
        │ 每个区间 × 每个镜头 → 一个 Segment
        ▼
Segment[]:
  start_frame / end_frame   ← 区间 ∩ 镜头
  rect                      ← 该区间内所有关键帧矩形的并集（ROI 保持稳定，利于时序传播）
  expand_px / feather_px    ← 既有默认值（5 / 3）
        + 逐帧掩码           ← 由 keyframes 插值生成，落在 ROI 内
```

三条要点：

1. **处理层契约不变**：`process.start` 仍然只收 `segments`。
2. **ROI 用并集框**：动态水印在 ROI 内移动，裁剪框保持稳定——这对光流传播类模型反而有利。
3. **逐帧掩码是既有能力**：`inpaint_roi_sequence` 本来就收一个掩码列表（§2）。

### 5.3 `watermark.py` 的真实改动面（必须如实记录）

- **静态水印：零改动。** 现有调用填同一张模板，行为正确。
- **动态水印：需要改，而且只能改这一处。** 掩码是在 ROI 循环里构造的，共有 4 处
  `[roi_mask_template.copy() for _ in frames]`（[watermark.py:3069](../engine/pipeline/watermark.py#L3069)、`#L3081`、`#L3148`、`#L3158`）。
  动态水印要把这 4 处换成"按 keyframes 逐帧生成掩码"，并让 `_resolve_segment_geometry`
  对带 keyframes 的段使用并集框。预计 < 40 行。

**这 40 行怎么进仓库**：不能手改。该文件由 `scripts/port_reference.py` 生成，
`--check` 会把手改判为漂移。因此改动以**后处理补丁**的形式写进 `port_reference.py`
（`transform()` 之后的 patch 阶段，带注释和断言），保证"重新生成 → 得到同样的文件"。
这样 `port_reference.py --check` 依然是门禁的有效守卫，而不是被绕过。

### 5.4 需要连带修改的地方

| 位置 | 改动 | 风险 |
|---|---|---|
| `Segment` 类型（契约） | 新增可选 `keyframes` 字段；无该字段时行为与现在完全一致 | 低（可选字段） |
| `annotation.normalize` | 归一化 `keyframes`（逐点裁剪 + 时间排序）；无 `keyframes` 走原路径 | 中（这是被 parity 测试钉住的函数，必须逐条补测试） |
| sidecar（`*.annotations.json`） | `version: 2`，新增 `watermarks`（track 级）与 `segments` 并存；读 v1 时自动升级 | 中（旧文件兼容） |
| `watermark.py` | **静态水印零改动**；动态水印需改 4 处掩码构造（§5.3，< 40 行）；`propainter_roi` 重新进入 `SUPPORTED_MODEL_IDS` 后，那 1.5k 行死分支会激活 | 高（见 R3） |

---

## 6. 修复引擎层

### 6.1 扩展后的接口

```python
class BaseInpaintEngine:
    model_id: str
    display_name: str
    kind: str                     # "fast" | "quality"
    requires_runtime: bool
    temporal: bool

    def capabilities(self) -> dict:      # 显存需求、预计速度、不可用原因
    def load(self) -> None
    def inpaint_roi(self, roi, mask) -> np.ndarray
    def inpaint_roi_sequence(self, roi_frames, roi_masks, **kw) -> list[np.ndarray]
    def is_available(self) -> bool
    def get_warning(self) -> str
```

新增 `kind` / `temporal` / `capabilities()`；`inpaint_roi_sequence` 的签名**不变**（它本来就收掩码列表）。

### 6.2 两个引擎

| 引擎 | 运行时 | 定位 | 未安装时 |
|---|---|---|---|
| `lama_roi` | 内置 ONNX | 快速档，默认 | — |
| `propainter_roi` | 独立 venv + 权重（新建） | 质量档 | 报"未安装"并提供安装入口；**不静默回退** |

回退只允许发生在"用户显式选了质量档但运行时不可用"时，且必须在结果里说明（`ResolvedModel` 机制已存在）。

### 6.3 显存自适应

```
≥ 16GB → 720p ROI 上限, neighbor=10, subvideo=80, fp16
10–16GB → 512px ROI 上限, neighbor=8,  subvideo=60, fp16
6–10GB  → 384px ROI 上限, neighbor=6,  subvideo=40, fp16
< 6GB   → 不提供质量档，界面直接说明原因
```

超出上限时**缩小 ROI**（水印框 + 固定边距），而不是拒绝执行。

---

## 7. 契约变更

### 7.1 新增方法

| 方法 | 参数 | 返回 |
|---|---|---|
| `analyze.capabilities` | `{}` | 检测器列表、安装状态、缺失原因、显存提示 |
| `analyze.start` | `input_path`, `sample_step?`, `max_tracks?`, `min_confidence?` | `{task_id}` |

- 分析是**独立任务**，进度与结果一律走已有的 `process.status` / `process.cancel`。
- **不新增 `analyze.status` / `analyze.result`**：与 `upscale.*` 的做法一致——"第二套状态方法只会给外壳第二种出错的方式"（[upscale.py](../engine/methods/upscale.py#L1-L11) 的原始理由）。
- **不把分析塞进 `process.start`**：分析结果必须先给用户看、给人改，再进入处理。

### 7.2 修改方法

- `process.start`：新增可选 `engine_id`（`"lama_roi" | "propainter_roi"`）；`model_id` 保留为别名。
- `model.list`：每项增加 `kind`、`requires_runtime`、`vram_hint_mb`、`temporal`。
- `Segment` 类型：新增可选 `keyframes`（见 5.2）。
- `annotation.save` / `annotation.load`：sidecar v2（见 5.4），读 v1 兼容。
- **新增 `annotation.compile`**：输入 `WatermarkTrack[]`（含 `intervals` / `keyframes` / `motion`），
  输出 `Segment[]`。放在引擎侧的理由：编译规则必须与 `annotation.normalize` 的裁剪/排序规则一致，
  放在同一侧才不会漂移。界面每次编辑 track 之后重新编译一次，不自己拼 Segment。

外壳不做任何"水印 → Segment"的转换逻辑：摊平与插值都在引擎内完成（`annotation.compile`，见上），
外壳只负责把用户编辑后的 track 列表传进去。这样"检测结果"和"用户改过的结果"走的是同一条编译路径，
不会出现"自动识别的框和手动改的框规则不一致"。

### 7.3 新增错误码

| 代码 | 语义 | retryable |
|---|---|---|
| `DETECTOR_MISSING` | 检测模型未安装 | true |
| `DETECTOR_FAILED` | 检测运行失败 | true |
| `VRAM_INSUFFICIENT` | 质量档显存不足且已降到最低档 | false |
| `RUNTIME_MISSING` | 引擎运行时未安装 | true |

> **"没检测到水印"不是错误码**：任务 `success`、`watermarks` 为空数组，由界面提示并转手动。

---

## 8. 界面：分析报告 + 时间线 + 编辑栏

### 8.1 分析报告视图（新）

不是弹窗，是"开始处理"之后的第一个页面状态：

```
视频  1920×1080 · 30fps · 30.2s · 900 帧
镜头  3 个（切点 08:12 / 19:05）
─────────────────────────────────────────
水印 A  全程 · 位置固定 · 置信度 0.94
         建议：快速档即可
水印 B  00:21–00:28 · 缓慢移动 · 置信度 0.71  ⚠️ 低置信度
         建议：质量档 + 逐帧轨迹
─────────────────────────────────────────
[开始处理]   [编辑水印]   [重新分析]
```

### 8.2 时间线（新）

- 横轴时间，纵轴是 track（一条水印一条泳道）；
- 区间画成色块，多区间就是同一泳道上多段；
- `moving` 的区间叠一条轨迹带（框中心随时间的折线）；
- 镜头切点画竖线，跨切点的区间自动断开；
- 低置信度区间标黄。

### 8.3 水印编辑栏

复用并扩展现有的 [AnnotationWorkspace.tsx](../desktop/renderer/workspace/AnnotationWorkspace.tsx)（帧预览、拖框、逐帧、入点出点都已具备），新增：

| 能力 | 必要性 | 说明 |
|---|---|---|
| 拖动/缩放区间端点 | 必须 | 修"水印开始得太早/结束得太晚" |
| 拆分 / 合并 track | 必须 | 修关联错误（这是最主要的失败模式） |
| 删除误检 | 必须 | 字幕/台标被误判为水印 |
| 拖动关键帧的框 | 必须 | 修位置偏差 |
| 新增水印 / 区间 | 必须 | 漏检的兜底 |
| 手动逐帧跟踪 | 可选（后续） | 第一版用插值即可 |

确认界面同时显示当前档位（快速/质量），并允许切换。

---

## 9. 验证方案（实验随后执行）

两套指标，缺一不可：**分析准不准** + **擦得干不干净**。

### 9.1 素材

用 [generate_fixture.py](../scripts/generate_fixture.py) 生成**成对素材**：干净原片 + 同一素材叠加已知水印。因为干净原片已知，第一次可以算出**真值**，而不只是"没退化"。

| 用例 | 覆盖 |
|---|---|
| `static_corner` | 单镜头 + 固定角标（情形 1） |
| `translucent_text` | 半透明文字（掩码内仍有内容） |
| `multi_shot_static` | 多镜头 + 固定水印（情形 2） |
| `moving_logo` | 单镜头 + 缓慢移动（情形 3） |
| `moving_multi_shot` | 多镜头 + 移动（情形 4） |
| `no_watermark` | **负样本**：干净无水印视频，用于测误报 |
| `multi_watermark` | 同屏两个水印，用于测关联（拆/合） |

### 9.2 分析质量指标

| 指标 | 定义 |
|---|---|
| 检测召回 | 真值框被检出的比例（IoU ≥ 0.5） |
| 误报数 | `no_watermark` 用例上检出的 track 数，必须为 0 |
| track 准确率 | 水印数量判对的比例；区间端点误差（帧） |
| 运动判定准确率 | `static`/`moving` 判对的比例 |

### 9.3 去水印质量指标

| 指标 | 定义 |
|---|---|
| PSNR / SSIM | 输出 vs 干净原片 |
| 掩码外破坏 | 掩码外区域平均绝对差 |
| **时序闪烁** | ROI 内相邻帧差分的标准差（LaMa 的结构性短板） |
| 耗时 | 秒/帧、总耗时 |
| 峰值显存 | `torch.cuda.max_memory_allocated` |

### 9.4 判定

- **硬门槛**：质量档 PSNR/SSIM 不低于 LaMa，闪烁显著更低，掩码外破坏不劣化。
- **分析门槛**：负样本误报为 0；主用例召回 ≥ 0.9；运动判定在合成素材上必须全对。
- **不预设立场**：若 ProPainter 的收益不足以抵消"慢 + 大运行时"，方案降级为"只做分析自动化，模型保持 LaMa"，并如实记录。
- **产物**：`docs/11-quality-baseline.md`；7 组用例的干净原片作为 **reference 用例**加入 `golden/cases/`（仓库第一次有真值用例）。

### 9.5 新增脚本（不改现有门禁行为）

- `scripts/temporal-flicker.py`：输入两段视频 + 掩码区域，输出 ROI 相邻帧差分的均值/标准差。
- `scripts/eval-analysis.py`：对合成素材跑 `analyze.start`，输出召回/误报/track 准确率。

---

## 10. 运行时与资产

| 资产 | 位置 | 分发 |
|---|---|---|
| LaMa ONNX | 随包（已有） | 不变 |
| Florence-2-base 权重（约 0.5GB） | `models/florence2/` | 首次使用下载 + sha256 |
| ProPainter 运行时（venv + torch） | `models/runtime/propainter/` | 按需安装，复用 SeedVR2 安装器 |
| ProPainter 权重 | 同上 | 安装时下载 + sha256 |

规则：

- **权重与代码分离**：仓库只含代码；权重走 `scripts/fetch-assets.py` 或应用内 `model.download.*`（机制已有）。
- **许可记录**：每个资产的许可证写进文档并在"关于"页展示。ProPainter 为 NC，仅限个人/小范围使用——写进文档，不能只存在于口头。
- **不随包**：分析与质量档模型都不进安装包，安装包体积不变。

---

## 11. 工作项（顺序待定）

> 顺序暂不锁定（用户尚未确认）。下面按"依赖关系"列出，不按优先级。

| # | 工作项 | 依赖 |
|---|---|---|
| W1 | 安全网：`model_registry` / `remover` / `annotation.normalize` 的单测（当前核心零覆盖） | — |
| W2 | 引擎抽象扩展（`kind` / `temporal` / `capabilities()`） | W1 |
| W3 | 分析层：检测适配器（Florence-2） | W1 |
| W4 | 分析层：跨帧关联 + 区间 + 运动判定 | W3 |
| W5 | 水印实体与编译（`WatermarkTrack` → `Segment`，含 `keyframes`） | W4 |
| W6 | 契约：`analyze.*` + `annotation.compile` + `Segment.keyframes` + sidecar v2 | W5 |
| W6b | 动态掩码：把 4 处模板复制改为按 keyframes 生成，写进 `port_reference.py` 的后处理补丁 | W6 |
| W7 | 界面：分析报告视图 | W4 |
| W8 | 界面：时间线（泳道 + 区间 + 轨迹 + 切点） | W7 |
| W9 | 界面：编辑栏扩展（拖区间、拆/合、删、增） | W8、W5 |
| W10 | 质量档：ProPainter 运行时安装器 + 引擎适配器 | W2 |
| W11 | 验证：7 组素材 + 两套指标脚本 + `docs/10` | W1（可并行） |

---

## 12. 风险与未决项

| # | 风险 / 未决 | 影响 | 处置 |
|---|---|---|---|
| R1 | **目标显卡门槛未定** | 决定质量档能否普及 | 显存自适应（§6.3），拿到实测后回填 |
| R2 | ProPainter 是 Python 3.8 时代的代码，在 3.12 运行时可能不兼容 | W10 被阻塞 | 先做最小 spike；不通则改用 Python 3.10 运行时 |
| R3 | 那 1.5k 行质量逻辑是为 ProPainter 调的，状态未知；复活后可能"帮倒忙" | 质量不可控 | 加开关，允许绕过该逻辑直接比对；实验决定保留或绕开 |
| R4 | `watermark.py` 由脚本生成，手改会被 `port_reference.py --check` 判为漂移 | 维护成本 | 动态水印的掩码改动写进 `port_reference.py` 的后处理补丁（§5.3），重新生成可复现；不手改文件 |
| R4b | 4 处掩码构造点没有单测覆盖，改错会静默退化成"仍用模板" | 动态水印失效但不报错 | W1 里先给这 4 处加特征测试（传入带 keyframes 的段，断言掩码逐帧不同） |
| R5 | **跨帧关联错误**（两个水印并成一个 / 一个拆成两个） | 用户信任崩塌 | 拆/合编辑必须做（§8.3）；负样本与多水印用例专门测 |
| R6 | 检测把字幕、台标、画面文字误判为水印 | 自动化体验 | 面积上限 + 置信度阈值 + 可编辑；负样本用例 |
| R7 | 分析耗时过长，破坏"点一下就开始"的体验 | 产品价值 | 采样 + 下采样；目标 10 秒内；独立可取消任务 |
| R8 | 时间线 UI 复杂度高（多泳道、区间编辑、轨迹带） | 工期 | 分两步：先只读展示，再可编辑 |
| R9 | 「个人/小圈子」里若有人商用，NC 仍生效 | 合规 | 文档与"关于"页明示；不对接商业场景 |
| R10 | 扩散类模型速度慢 | 产品价值 | 优先流传播类（ProPainter），扩散类只作后续候选 |

---

## 13. 决定清单

### 已确认

| # | 决定 | 日期 |
|---|---|---|
| D1 | 换模型是否成立由**实测数据**判断；收益不足就只做自动化、保留 LaMa | 2026-11-21 |
| D2 | **永远先分析、展示结论、用户确认后才处理**；不做零点击全自动 | 2026-11-21 |
| D3 | 同一个水印「出现→消失→再出现」= **1 个实体 + 多段区间**（时间线一条泳道、多段亮起） | 2026-11-21 |
| D4 | 使用形态为个人/小范围使用，不对外分发 → NC 模型可用，权重不进仓库/安装包 | 2026-11-21 |
| D5 | 分析要回答四件事：水印数量 / 是否连贯 / 是否移动 / 镜头数量，并给处理建议 | 2026-11-21 |

### 待确认

| # | 待确认 | 影响 |
|---|---|---|
| Q1 | 工作项顺序（§11）与里程碑划分 | 决定先做什么 |
| Q2 | 目标显卡门槛（§6.3 默认分档是否可接受） | 决定质量档普及面 |
| Q3 | 质量档是否必须"开箱可用"，还是允许"首次使用时等待安装" | 决定 UI 与安装流程 |
