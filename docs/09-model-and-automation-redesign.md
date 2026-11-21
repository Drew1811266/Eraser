# 09 · 模型与流程重设计（设计 spec）

- **状态**：待评审
- **日期**：2026-11-21
- **关联**：`docs/adr/0001`（运行时策略）、`0005`（打包）、`0006`（超分走 ONNX）、`docs/06`（执行计划）
- **前置决策**：使用形态为**个人 / 小范围内使用，不对外分发**；因此非商业许可（NC）的模型可用，但权重不进入仓库、不进入版本库。

---

## 1. 要解决的两个问题

产品在 macOS 时期做过两次性能妥协，现在换到 Windows + NVIDIA 显卡，可以把这两次妥协都收回来。

| # | 当年的妥协 | 现在的目标 |
|---|---|---|
| **P1** | 去水印模型从"高质量档"降到 LaMa（单帧、轻量） | 换回时序修复模型，让擦除结果在时间上连续、不闪烁 |
| **P2** | 让用户手工框选水印位置与时间区间 | 由视觉模型自动识别水印位置，用户只做确认与精修 |

这两件事是独立的，但共享同一条链路：**检测产出掩码 → 修复引擎消费掩码**。所以一次设计、分阶段落地。

### 1.1 目标（DoD）

1. 用户丢入一段视频，**无需手动画框**即可得到去水印结果。
2. 质量档的擦除结果**在时序上明显优于**现有 LaMa 管线（以第 7 节的指标度量，不是主观判断）。
3. 检测/修复的模型都可**替换**：新增一个模型不改 `process_video` 主循环。
4. 现有手动打标流程**保留**，作为检测失败时的兜底和精修入口。
5. 每一次质量相关的改动都有**可复现的数值基线**（当前仓库为 0 条 reference 用例，必须补上）。

### 1.2 非目标

- 不重写移植过来的 `engine/pipeline/watermark.py`（4774 行、由 `scripts/port_reference.py` 机械生成）。
- 不做批量队列 / 工程管理 / 云端推理。
- 不改 UI 框架（继续 React 18 + zustand + Material 3）。
- 不引入需要联网推理的方案（全部本地）。

---

## 2. 现状事实（设计依据）

这些是读代码确认过的，不是推测：

| 事实 | 位置 | 对设计的影响 |
|---|---|---|
| 已有引擎抽象：`BaseInpaintEngine.inpaint_roi()` / `inpaint_roi_sequence()`，注释写明"时序模型可覆盖后者做批量推理" | [model_registry.py](../engine/pipeline/model_registry.py#L32-L48) | **新模型只需要写一个子类**，不需要动主循环 |
| 但 `SUPPORTED_MODEL_IDS = ("lama_roi",)`，且 `_create_engine()` 只 new `LamaRoiEngine` | [model_registry.py](../engine/pipeline/model_registry.py#L120-L132) | 插槽是空的 |
| 管线里保留了完整的 ProPainter 时序质量逻辑（Viterbi 序列选择、微闪烁修复、短时重现修复、去除充分性判定，约 1.5k 行），因 `effective_model_id == 'propainter_roi'` 永不成立，目前是**死路径** | [watermark.py](../engine/pipeline/watermark.py#L1149-L2400)、`#L2779` | 接上引擎即可复活；但它当年是为 ProPainter 输出调的，换模型需重新验证 |
| 掩码是**静态矩形** + 按尺寸自适应扩张/羽化 | [watermark.py](../engine/pipeline/watermark.py#L142-L243) | 非矩形/半透明/移动水印会被过度覆盖，是"效果不理想"的直接原因之一 |
| 契约有 23 个方法，`process.start` 参数只有 `input_path / output_dir / model_id / segments` | [contract/methods.json](../contract/methods.json#L671) | 自动化需要新方法，而不是往 `segments` 里塞魔法值 |
| 迁移后界面把模型 ID 硬编码为 `lama_roi` | [App.tsx](../desktop/renderer/App.tsx#L136-L139) | 新增档位需要改这一处白名单 |
| 核心三文件（`watermark.py` / `remover.py` / `model_registry.py`）在 `tests/` 下**零引用** | `tests/` | 改核心前必须先补安全网 |
| 黄金用例 3 条全是 baseline，reference 为 0 | `golden/cases/` | 换模型后没有基线能证明"变好" |
| 已有可复用的重运行时安装模式：独立 venv → 固定 commit CLI → 权重下载 + sha256 → 子进程进度/取消 | [seedvr/runtime.py](../engine/pipeline/seedvr/runtime.py) | 重型修复模型直接复用这套机制 |

---

## 3. 架构

```
                        ┌──────────────────────────────────────────┐
   用户丢入视频  ──────► │  Detector（新）                           │
                        │  Florence-2 (MIT) → 水印候选框             │
                        │  ＋ 跨帧聚合（采样 → 合并 → 跟踪补间）     │
                        │  ＋ 可选：SAM 2 (Apache 2.0) 掩码精修      │
                        └───────────────┬──────────────────────────┘
                                        │ DetectedWatermark[]
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │  annotation.normalize（复用，不新增规则）  │
                        └───────────────┬──────────────────────────┘
                                        │ Segment[]
                                        ▼
                        ┌──────────────────────────────────────────┐
                        │  Engine（可插拔）                          │
                        │  ├─ lama_roi       快速档，随包，秒级      │
                        │  └─ propainter_roi 质量档，按需安装运行时  │
                        └───────────────┬──────────────────────────┘
                                        ▼
                            现有编码/音轨/拼接管线（不改）
```

三条边界规则：

1. **检测器不认识修复引擎，修复引擎不认识检测器。** 二者只共享 `Segment`（已有类型）。
2. **检测结果必须经过 `annotation.normalize`**，与手动画框走同一套裁剪/排序/默认值规则——否则两条路径会产出不同的数据。
3. **`process_video` 主循环不改。** 所有新能力在 `BaseInpaintEngine` 与 RPC 层实现。

---

## 4. 检测层

### 4.1 新包 `engine/pipeline/detect/`

| 模块 | 职责 |
|---|---|
| `base.py` | `WatermarkDetector` 抽象：`detect(frames_or_video) -> list[DetectedWatermark]`、`is_available()`、`load()` |
| `florence.py` | Florence-2 适配器（`<OD>` / `<CAPTION_TO_PHRASE_GROUNDING>` / `<OCR_WITH_REGION>`） |
| `aggregate.py` | 跨帧聚合：把逐帧 bbox 合并成时间段；处理漏检、抖动、多区域 |
| `runtime.py` | 若 Florence-2 需要独立运行时，复用 SeedVR2 的安装器模式 |

### 4.2 采样与聚合策略

逐帧跑检测代价过高（一段 30s/30fps 的视频是 900 帧）。设计：

1. **采样**：默认每 15 帧（0.5s）检测一次，硬切处强制检测。
2. **聚合**：相邻采样点的 bbox IoU ≥ 0.5 视为同一水印；合并为 `[start_frame, end_frame]`。
3. **补间**：段内未检测的帧用相邻 bbox 线性插值（用户可后续用跟踪替换）。
4. **过滤**：`confidence < 0.35` 的候选不进入结果；面积超过画面 25% 的框丢弃（防止把字幕条/黑边当成水印）。
5. **上限**：单段视频最多返回 8 个区域，按置信度排序。

以上阈值都是**初值**，第 7 节的实验负责校准；写进代码时必须是命名常量，不是散落的魔法数字。

### 4.3 `DetectedWatermark`

```python
{
  "start_frame": int,
  "end_frame": int,
  "rect": {"x": int, "y": int, "width": int, "height": int},
  "confidence": float,      # 0..1
  "source": "florence2-od" | "florence2-ocr" | "manual",
  "samples": [{"frame": int, "bbox": [x1, y1, x2, y2]}],  # 供跟踪与 UI 展示
}
```

`samples` 保留逐帧原始检测，是为了将来做真正的跟踪（而不是插值），也为了 UI 能画出"检测到它的那几帧"。

---

## 5. 修复引擎层

### 5.1 扩展后的接口

```python
class BaseInpaintEngine:
    model_id: str
    display_name: str
    kind: str                     # "fast" | "quality"
    requires_runtime: bool        # True 时才走"按需安装"
    temporal: bool                # True 时管线走 sequence 路径

    def capabilities(self) -> dict:      # 新增：显存需求、预计速度、可用性原因
    def load(self) -> None
    def inpaint_roi(self, roi, mask) -> np.ndarray
    def inpaint_roi_sequence(self, roi_frames, roi_mask, **kw) -> list[np.ndarray]
    def is_available(self) -> bool
    def get_warning(self) -> str
```

新增 `kind` / `temporal` / `capabilities()` 三个成员，其余不变。**`watermark.py` 不需要改**。

### 5.2 两个引擎

| 引擎 | 运行时 | 定位 | 未安装时的行为 |
|---|---|---|---|
| `lama_roi` | 内置 ONNX（已有） | 快速档，默认 | — |
| `propainter_roi` | 独立 venv + 权重（新建） | 质量档 | 报"未安装"，提供安装入口；不静默回退 |

**回退规则**：只允许"用户显式选择质量档但运行时不可用"时回退到 LaMa，并在结果的 `model_warning` 里说明原因（`ResolvedModel` 机制已存在）。**不允许静默回退**——用户必须知道跑的是哪个模型。

### 5.3 显存自适应

ProPainter 的主要参数（`neighbor_length` / `ref_stride` / `subvideo_length` / `fp16`）按可用显存自动选择：

```
可用显存 ≥ 16GB → 720p ROI, neighbor=10, subvideo=80, fp16
可用显存 10-16GB → 512px ROI 上限, neighbor=8, subvideo=60, fp16
可用显存 6-10GB  → 384px ROI 上限, neighbor=6, subvideo=40, fp16
可用显存 < 6GB   → 不提供质量档，UI 直接说明原因
```

超出上限时**把 ROI 缩小**（水印框 + 固定边距），而不是拒绝执行——水印区域通常远小于整帧，这是本产品天然的成本优势。

---

## 6. 契约变更

### 6.1 新增方法（`contract/methods.json`）

| 方法 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `detect.capabilities` | `{}` | 检测器列表、安装状态、缺失原因 | 与 `upscale.capabilities` 同构 |
| `detect.start` | `input_path`, `sample_step?`, `max_regions?`, `min_confidence?` | `{task_id}` | 长任务，复用 `TaskManager` |

> 检测走"任务"而不是同步调用，因为一段长视频的检测可能要几十秒，界面必须有进度与取消。
> **不新增 `detect.status` / `detect.result`**：进度与结果一律走已有的 `process.status`，
> 与 `upscale.*` 的做法一致——"第二套状态方法只会给外壳第二种出错的方式"
> （[upscale.py](../engine/methods/upscale.py#L1-L11) 的原始理由）。

### 6.2 修改方法

- `process.start`：新增可选参数 `engine_id`（`"lama_roi" | "propainter_roi"`）。
  `model_id` 保留为别名，向后兼容。
- `model.list`：每项增加 `kind`、`requires_runtime`、`vram_hint_mb`、`temporal`。

> **不把检测塞进 `process.start`**。检测结果需要用户先看、先编辑（§8.1），
> 所以它必须是一个独立任务，完成后再由外壳带着 `segments` 调 `process.start`。
> 检测结果到 segment 的转换直接复用 `annotation.normalize`
> （`DetectedWatermark` 与 `Segment` 的字段是重叠的，缺的 `expand_px`/`feather_px`/`enabled`
> 由该函数按既有默认值补齐），外壳不新增任何转换逻辑。

### 6.3 新增错误码（`contract/errors.json`）

| 代码 | 语义 | retryable |
|---|---|---|
| `DETECTOR_MISSING` | 检测模型未安装 | true |
| `DETECTOR_FAILED` | 检测运行失败（模型崩溃/图像不可读） | true |
| `VRAM_INSUFFICIENT` | 质量档所需显存不足，且已降到最低档仍不够 | false |
| `RUNTIME_MISSING` | 引擎运行时未安装 | true |

> **"没检测到水印"不是错误码**：它是一个 `success` 的任务，`detections` 为空数组，
> 由界面决定提示与降级（§8.2 规则 1）。把它定义成错误会让外壳对"正常结果"走异常分支。

---

## 7. 质量验证方案（实验随后执行）

这是**换模型这件事能否成立**的唯一判据。

### 7.1 素材

用 [scripts/generate_fixture.py](../scripts/generate_fixture.py) 生成成对素材：**干净原片 + 同一素材叠加已知水印**。因为干净原片是已知的，第一次可以算出**真值指标**，而不只是"没退化"。

| 用例 | 覆盖的失效面 |
|---|---|
| `static_corner` | 最常见的固定角标 |
| `translucent_text` | 半透明文字（掩码内仍有内容） |
| `moving_logo` | 段内位置缓慢移动（静态矩形掩码的软肋） |
| `multi_region` | 同屏两处水印 |

### 7.2 指标

| 指标 | 定义 | 作用 |
|---|---|---|
| PSNR / SSIM | 输出 vs 干净原片 | 内容还原度 |
| 掩码外破坏 | 掩码外区域的平均绝对差 | 防止"修好了水印，弄坏了背景" |
| **时序闪烁** | ROI 内相邻帧差分的标准差（越低越稳） | 这正是 LaMa 的结构性短板 |
| 耗时 | 秒 / 帧、总耗时 | 决定质量档是否可接受 |
| 峰值显存 | 用 `torch.cuda.max_memory_allocated` | 决定 §5.3 的分档 |

### 7.3 判定

- **硬门槛**：质量档的 PSNR/SSIM **不低于** LaMa，闪烁指标**显著低于** LaMa，掩码外破坏不劣化。
- **不预设立场**：如果实测 ProPainter 的收益不足以抵消"慢 + 大运行时"，方案改为"只做自动化，模型保持 LaMa"，并如实记录结论。
- **产物**：`docs/10-quality-baseline.md` + 把 4 组用例的干净原片作为 **reference 用例**加入 `golden/cases/`（这是仓库第一次有真值用例，`golden.py` 已支持）。

### 7.4 时序闪烁的可测性

现有 `scripts/compare-video.py` 只做 PSNR/SSIM，不测时序。新增 `scripts/temporal-flicker.py`，输入两段视频 + 掩码区域，输出 ROI 相邻帧差分的均值与标准差。这是新增文件，不修改现有门禁的行为。

---

## 8. 产品流程

### 8.1 新流程

```
导入视频
  → [自动] 检测水印（进度条，可取消）
      ├─ 找到 N 个区域 → 预览上画出候选框（虚线 + 置信度）
      │    ├─ 用户直接开始 → 处理
      │    └─ 用户拖动/删除/新增 → 变成普通标注 → 处理
      └─ 没找到 → 明确提示"未检测到水印"，引导到手动打标（不静默开始处理）
  → [选择档位] 快速（默认）/ 质量（需已安装运行时）
  → 处理（现有进度/取消/结果链路不变）
```

### 8.2 关键产品规则

1. **检测无结果不是错误**：任务以 `success` 结束、`detections` 为空数组，界面进入手动模式并给出提示，而不是弹错误框。
2. **检测结果默认可编辑**：不做"黑盒一键"，否则一次误检就意味着用户无法挽救。
3. **低置信度不隐藏**：标黄显示，默认启用但可一键关闭。
4. **质量档必须显式选择**：不因为"用户装了显卡"就偷偷换成慢模型。

### 8.3 界面改动范围

- `ProcessView`：加入检测阶段与候选框展示。
- `AnnotationWorkspace`（810 行）：接收检测结果作为初始标注，其余交互不变——**这是本设计里 UI 风险最高的一处**，因为它同时承载手动与自动两条路径。
- `App.tsx`：移除 `normalizeModelId()` 的硬编码，改为从 `model.list` 读取。

---

## 9. 运行时与资产

| 资产 | 位置 | 分发方式 |
|---|---|---|
| LaMa ONNX | 随包（已有） | 不变 |
| Florence-2 权重（~0.5–1.5GB） | `models/florence2/` | 首次使用下载，sha256 校验 |
| ProPainter 运行时（venv + torch） | `models/runtime/propainter/` | 按需安装，复用 SeedVR2 安装器 |
| ProPainter 权重（~100MB 级） | 同上 | 安装时下载，sha256 校验 |

规则：

- **权重与代码分离**：仓库只含代码；权重通过 `scripts/fetch-assets.py` 或应用内 `model.download.*` 获取（机制已有）。
- **许可记录**：每个资产的许可证写进 `docs/`，界面"关于"页展示。ProPainter 为 NC，仅限个人/小圈子使用——这一约束要写进文档，不能只存在于口头。
- **不随包**：质量档与检测模型都不进安装包，保持安装包体积不变。

---

## 10. 分阶段落地

| 阶段 | 内容 | 完成标准 |
|---|---|---|
| **P0 · 安全网** | ① 补 `model_registry` / `remover` 的单测（当前零覆盖）② `BaseInpaintEngine` 扩展三个成员 ③ 新增 `temporal-flicker.py` ④ 建立 benchmark 素材与脚本 | 现有 174 个单测仍全绿；新增单测覆盖引擎解析与回退；benchmark 可一键跑出 LaMa 基线 |
| **P1 · 质量档** | ProPainter 运行时安装器 + 引擎适配器 + 显存自适应 + e2e | 真实视频跑通；§7 指标出来；结论写进 `docs/10` |
| **P2 · 自动化** | `detect.*` 契约 + Florence-2 适配器 + 聚合 + 界面接入 | 丢入视频可自动出候选框；手动流程未被破坏 |
| **P3 · 打磨** | SAM 2 掩码精修、多区域 UI、低置信度交互、质量档进度细化 | 按实测结果决定是否继续 |

**顺序理由**：P0 先补安全网，是因为核心文件零覆盖；P1 先于 P2，是因为"质量提升是否成立"决定 P2 的档位设计（如果 ProPainter 收益不足，P2 就只做检测）。

---

## 11. 风险与未决项

| # | 风险 / 未决 | 影响 | 处置 |
|---|---|---|---|
| R1 | 目标显卡门槛未定（6–8GB？8–12GB？） | 决定质量档能否普及 | 设计上做自适应（§5.3），不写死；实验拿到实测显存后回填 |
| R2 | ProPainter 代码较老（Python 3.8 时代），在 Python 3.12 运行时可能不兼容 | P1 被阻塞 | P1 第一步先做最小可行 spike，不通则换 Python 3.10 运行时 |
| R3 | 那 1.5k 行质量逻辑是为 ProPainter 调的，状态未知 | 复活后可能"帮倒忙" | P0 里加开关：允许绕过该逻辑直接比对，实验决定保留还是绕开 |
| R4 | `watermark.py` 是脚本生成的，手改会被 `port_reference.py --check` 判为漂移 | 维护成本 | 所有改动集中在引擎边界与 RPC 层；确需改核心时，走显式 diff 并同步参考实现 |
| R5 | 检测误检（字幕、台标边缘、画面文字） | 自动化体验 | 面积上限 + 置信度阈值 + 用户可编辑；实验里专门加"无水印视频"的负样本 |
| R6 | 「个人/小圈子」里若有人商用，NC 仍生效 | 合规 | 文档与"关于"页明示；不对接商业场景 |
| R7 | 扩散类模型速度慢，可能达不到"创作者随手用"的体验 | 产品价值 | 本方案优先选流传播类（ProPainter），扩散类只作为后续候选 |

---

## 12. 待评审确认的三个决定

1. **§7 的判定逻辑**是否认可——即"如果实测收益不足，就只做自动化、保留 LaMa"，而不是为了换模型而换模型。
2. **§8.1 的默认行为**：丢入视频后**自动开始检测**（用户什么都不用点），还是**先问一句"是否自动检测"**。
3. **§10 的阶段顺序**：是否同意 P1 先于 P2（先验证质量档，再做自动化）。
