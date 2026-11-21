# M1 · 分析管线实现计划（引擎侧）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让引擎具备"分析一个视频的水印情况"的能力——输出水印数量、出现区间、是否移动、镜头切点，并把这些结果编译成现有管线能吃的 `Segment[]`；本里程碑不接真实视觉模型、不做界面。

**Architecture:** 纯计算与视频 IO 分离。`engine/pipeline/analysis.py` 只放数据模型与算法（无 ffmpeg、无模型），因此可以完全用合成数据做单测；`frames.py` 负责抽帧，`detect/` 负责模型适配（本里程碑只有脚本桩），`analyze_job.py` 负责编排，`methods/analyze.py` 交付 RPC。检测器与修复引擎一样是**可注入的**，这是"换模型不改主流程"的具体落法。

**Tech Stack:** Python 3.13、numpy、OpenCV（读帧）、ffmpeg（抽帧与切点扫描）、unittest、现有 JSON-RPC 契约代码生成器。

**Spec:** [docs/09-model-and-automation-redesign.md](09-model-and-automation-redesign.md)

## Global Constraints

- 引擎侧不得引入新依赖；**不得 import torch / transformers / onnxruntime 之外的任何东西**（本里程碑甚至不需要 onnxruntime 以外的新包）。
- 任何模块都不得新增 `sys.platform` / `os.name` 分支或硬编码路径——平台差异只允许出现在 `engine/platform/`。
- 引擎日志一律走 `engine/pipeline/logging.py` 的 `logger`，**绝不 print 到 stdout**（stdout 只承载协议帧）。
- 契约是唯一事实源：改 `contract/methods.json` 之后必须运行 `python contract/codegen/generate.py`，`scripts/check.sh` 的第 1 道门禁会校验生成物无漂移。
- 所有新增公开函数必须有类型标注；所有新增测试用 `unittest`（仓库现有风格），不得引入 pytest。
- 帧索引一律是 **0-based 闭区间** `[start_frame, end_frame]`，与 `Segment` 的既有语义一致。
- 测试命令统一用 `.venv/Scripts/python.exe -m unittest ...`（Windows venv 路径）。

## Review Focus

- **视频只有 1 帧或帧数极少**：分析必须返回单镜头、不崩溃，且 `compile_tracks` 不产出空区间。
- **检测器返回空结果**（没有水印）：分析成功、`watermarks` 为空数组，而不是异常。
- **同一水印中途漏检一两帧**：不得因此被切成两条 track（否则时间线会碎）。
- **两个水印靠得很近**：不得被并成一条 track（用户看不出区别就无法修）。
- **画面里没有 ffmpeg 或视频不可读**：必须给出结构化错误，而不是挂住或返回空分析。

---

### Task 1: 安全网 —— 引擎注册表的特征测试

**Files:**
- Test: `tests/test_model_registry.py`（新建）
- 参考实现：`engine/pipeline/model_registry.py`

**Interfaces:**
- Consumes: `ModelRegistry(remover)`、`normalize_model_id(model_id)`、`resolve(model_id) -> (engine, ResolvedModel)`、`SUPPORTED_MODEL_IDS`、`LEGACY_MODEL_ALIASES`
- Produces: 无（只加测试）。后续任务修改注册表时，这些测试是唯一的安全网。

- [ ] **Step 1: 写失败测试**

```python
"""Pins the registry behaviour the analysis milestone depends on.

The ported core (watermark/remover/registry) has no unit coverage today; the
only guard is the end-to-end suite, which cannot say *which* rule broke.
"""

from __future__ import annotations

import unittest

from engine.pipeline.model_registry import (
    LEGACY_MODEL_ALIASES,
    SUPPORTED_MODEL_IDS,
    BaseInpaintEngine,
    ModelRegistry,
)


class _FakeRemover:
    """Stands in for WatermarkRemover; never loads a model."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.loaded = False

    def is_loaded(self) -> bool:
        return self.loaded

    def load_model(self) -> None:
        self.loaded = True
        if self.fail:
            raise RuntimeError("boom")

    def inpaint(self, image, mask):  # noqa: ANN001 - test double
        return image


class RegistryTests(unittest.TestCase):
    def test_default_model_is_lama(self) -> None:
        self.assertEqual(ModelRegistry.normalize_model_id(None), "lama_roi")

    def test_unknown_model_falls_back_to_lama(self) -> None:
        self.assertEqual(ModelRegistry.normalize_model_id("nope"), "lama_roi")

    def test_legacy_alias_is_rewritten(self) -> None:
        self.assertEqual(ModelRegistry.normalize_model_id("sttn_roi"), "lama_roi")

    def test_supported_ids_are_lowercase(self) -> None:
        for model_id in SUPPORTED_MODEL_IDS:
            self.assertEqual(model_id, model_id.lower())

    def test_alias_targets_are_supported(self) -> None:
        for target in LEGACY_MODEL_ALIASES.values():
            self.assertIn(target, SUPPORTED_MODEL_IDS)

    def test_engine_is_reused_for_the_same_id(self) -> None:
        registry = ModelRegistry(_FakeRemover())
        first, _ = registry.resolve("lama_roi")
        second, _ = registry.resolve("lama_roi")
        self.assertIs(first, second)

    def test_load_failure_falls_back_and_warns(self) -> None:
        registry = ModelRegistry(_FakeRemover(fail=True))
        engine, resolved = registry.resolve("lama_roi")
        self.assertIsInstance(engine, BaseInpaintEngine)
        self.assertEqual(resolved.requested_model_id, "lama_roi")
        self.assertEqual(resolved.effective_model_id, "lama_roi")
        self.assertIn("load failed", resolved.warning)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试，确认它们通过**

Run: `.venv/Scripts/python.exe -m unittest tests.test_model_registry -v`
Expected: 7 tests PASS（这是**特征测试**：它固化现有行为，不是新功能，所以第一次跑就应该绿）

- [ ] **Step 3: 如果失败，逐条确认是测试写错还是行为不符**

失败的合法原因只有两类：函数名/签名与预期不符（改测试），或现有行为确实是 bug（记下来，**本任务不修**，留给后续任务）。

- [ ] **Step 4: 提交**

```bash
git add tests/test_model_registry.py
git commit -m "test(engine): pin the inpaint model registry before the analysis work"
```

---

### Task 2: 分析领域模型

**Files:**
- Create: `engine/pipeline/analysis.py`
- Test: `tests/test_analysis_models.py`（新建）

**Interfaces:**
- Produces:
  - `BBox(x, y, width, height)`，含 `x2`/`y2`/`area`、`as_dict()`、`from_dict(raw)`、`clamp(width, height)`、`BBox.union(boxes)`
  - `Detection(frame: int, rect: BBox, confidence: float)`
  - `Interval(start_frame: int, end_frame: int)`，含 `as_dict()`
  - `Keyframe(frame: int, rect: BBox)`，含 `as_dict()`
  - `WatermarkTrack(id: str, intervals: list[Interval], keyframes: list[Keyframe], motion: str, confidence: float, source: str, notes: list[str])`，含 `as_dict()`
  - `Analysis(video: dict, shots: list[Interval], watermarks: list[WatermarkTrack], suggested: dict, model_id: str, elapsed_ms: int)`，含 `as_dict()`
  - `iou(a: BBox, b: BBox) -> float`、`center_distance(a: BBox, b: BBox) -> float`

- [ ] **Step 1: 写失败测试**

```python
"""The value objects the whole analysis pipeline passes around."""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import (
    Analysis,
    BBox,
    Interval,
    Keyframe,
    WatermarkTrack,
    center_distance,
    iou,
)


class BBoxTests(unittest.TestCase):
    def test_corners_and_area(self) -> None:
        box = BBox(10, 20, 30, 40)
        self.assertEqual((box.x2, box.y2), (40, 60))
        self.assertEqual(box.area, 1200)

    def test_dict_round_trip(self) -> None:
        box = BBox(1, 2, 3, 4)
        self.assertEqual(BBox.from_dict(box.as_dict()), box)

    def test_from_dict_coerces_strings(self) -> None:
        box = BBox.from_dict({"x": "1", "y": "2", "width": "3", "height": "4"})
        self.assertEqual(box, BBox(1, 2, 3, 4))

    def test_clamp_keeps_box_inside_the_frame(self) -> None:
        box = BBox(630, 350, 100, 100).clamp(640, 360)
        self.assertEqual(box, BBox(630, 350, 10, 10))

    def test_clamp_rejects_negative_origin(self) -> None:
        self.assertEqual(BBox(-5, -5, 10, 10).clamp(640, 360), BBox(0, 0, 10, 10))

    def test_clamp_never_returns_zero_size(self) -> None:
        box = BBox(639, 359, 50, 50).clamp(640, 360)
        self.assertGreaterEqual(box.width, 1)
        self.assertGreaterEqual(box.height, 1)

    def test_union_covers_every_box(self) -> None:
        merged = BBox.union([BBox(10, 10, 10, 10), BBox(50, 40, 20, 20)])
        self.assertEqual(merged, BBox(10, 10, 60, 50))


class GeometryTests(unittest.TestCase):
    def test_iou_of_identical_boxes_is_one(self) -> None:
        self.assertAlmostEqual(iou(BBox(0, 0, 10, 10), BBox(0, 0, 10, 10)), 1.0)

    def test_iou_of_disjoint_boxes_is_zero(self) -> None:
        self.assertEqual(iou(BBox(0, 0, 10, 10), BBox(100, 100, 10, 10)), 0.0)

    def test_iou_ignores_zero_sized_input(self) -> None:
        self.assertEqual(iou(BBox(0, 0, 0, 0), BBox(0, 0, 10, 10)), 0.0)

    def test_center_distance(self) -> None:
        self.assertAlmostEqual(center_distance(BBox(0, 0, 10, 10), BBox(30, 0, 10, 10)), 30.0)


class TrackSerialisationTests(unittest.TestCase):
    def test_track_dict_shape(self) -> None:
        track = WatermarkTrack(
            id="wm-1",
            intervals=[Interval(0, 299)],
            keyframes=[Keyframe(0, BBox(1, 2, 3, 4))],
            motion="static",
            confidence=0.9,
        )
        payload = track.as_dict()
        self.assertEqual(payload["id"], "wm-1")
        self.assertEqual(payload["intervals"], [{"start_frame": 0, "end_frame": 299}])
        self.assertEqual(payload["keyframes"], [{"frame": 0, "rect": {"x": 1, "y": 2, "width": 3, "height": 4}}])
        self.assertEqual(payload["source"], "auto")

    def test_analysis_dict_shape(self) -> None:
        analysis = Analysis(
            video={"width": 640, "height": 360, "fps": 30.0, "frame_count": 300, "duration_seconds": 10.0},
            shots=[Interval(0, 299)],
            watermarks=[],
            suggested={"engine_id": "lama_roi", "split_by_shot": False, "warnings": []},
            model_id="stub",
            elapsed_ms=12,
        )
        payload = analysis.as_dict()
        self.assertEqual(payload["shots"], [{"start_frame": 0, "end_frame": 299}])
        self.assertEqual(payload["watermarks"], [])
        self.assertEqual(payload["model_id"], "stub")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analysis_models -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'engine.pipeline.analysis'`

- [ ] **Step 3: 写最小实现**

```python
"""Analysis value objects and geometry.

Everything here is pure: no ffmpeg, no model, no file IO. That is deliberate —
the hard part of the feature (associating detections across frames) is checkable
with synthetic inputs, so it must not sit behind a video decoder.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    width: int
    height: int

    @property
    def x2(self) -> int:
        return self.x + self.width

    @property
    def y2(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, raw: Any) -> "BBox":
        data = raw if isinstance(raw, dict) else {}
        return cls(
            x=int(data.get("x", 0) or 0),
            y=int(data.get("y", 0) or 0),
            width=int(data.get("width", 0) or 0),
            height=int(data.get("height", 0) or 0),
        )

    def clamp(self, frame_width: int, frame_height: int) -> "BBox":
        """Fit inside the frame without ever collapsing to zero size."""
        if frame_width <= 0 or frame_height <= 0:
            return BBox(0, 0, 1, 1)
        x = min(max(0, self.x), frame_width - 1)
        y = min(max(0, self.y), frame_height - 1)
        width = max(1, min(max(1, self.width), frame_width - x))
        height = max(1, min(max(1, self.height), frame_height - y))
        return BBox(x, y, width, height)

    @staticmethod
    def union(boxes: Iterable["BBox"]) -> "BBox":
        items = list(boxes)
        if not items:
            return BBox(0, 0, 1, 1)
        x1 = min(item.x for item in items)
        y1 = min(item.y for item in items)
        x2 = max(item.x2 for item in items)
        y2 = max(item.y2 for item in items)
        return BBox(x1, y1, max(1, x2 - x1), max(1, y2 - y1))


@dataclass(frozen=True)
class Detection:
    frame: int
    rect: BBox
    confidence: float = 1.0


@dataclass(frozen=True)
class Interval:
    start_frame: int
    end_frame: int

    @property
    def length(self) -> int:
        return max(0, self.end_frame - self.start_frame + 1)

    def as_dict(self) -> dict[str, int]:
        return {"start_frame": self.start_frame, "end_frame": self.end_frame}


@dataclass(frozen=True)
class Keyframe:
    frame: int
    rect: BBox

    def as_dict(self) -> dict[str, Any]:
        return {"frame": self.frame, "rect": self.rect.as_dict()}


@dataclass
class WatermarkTrack:
    id: str
    intervals: list[Interval]
    keyframes: list[Keyframe]
    motion: str = "static"
    confidence: float = 0.0
    source: str = "auto"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "intervals": [item.as_dict() for item in self.intervals],
            "keyframes": [item.as_dict() for item in self.keyframes],
            "motion": self.motion,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "notes": list(self.notes),
        }


@dataclass
class Analysis:
    video: dict[str, Any]
    shots: list[Interval]
    watermarks: list[WatermarkTrack]
    suggested: dict[str, Any]
    model_id: str
    elapsed_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "video": dict(self.video),
            "shots": [item.as_dict() for item in self.shots],
            "watermarks": [item.as_dict() for item in self.watermarks],
            "suggested": dict(self.suggested),
            "model_id": self.model_id,
            "elapsed_ms": int(self.elapsed_ms),
        }


def iou(a: BBox, b: BBox) -> float:
    """Intersection over union; 0.0 when either box has no area."""
    ix1 = max(a.x, b.x)
    iy1 = max(a.y, b.y)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    intersection = (ix2 - ix1) * (iy2 - iy1)
    union = a.area + b.area - intersection
    if union <= 0:
        return 0.0
    return intersection / float(union)


def center_distance(a: BBox, b: BBox) -> float:
    ax, ay = a.center
    bx, by = b.center
    return math.hypot(ax - bx, ay - by)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analysis_models -v`
Expected: 13 tests PASS

- [ ] **Step 5: 提交**

```bash
git add engine/pipeline/analysis.py tests/test_analysis_models.py
git commit -m "feat(analysis): value objects for watermarks, intervals and geometry"
```

---

### Task 3: 帧采样

**Files:**
- Create: `engine/pipeline/frames.py`
- Test: `tests/test_frames.py`（新建）

**Interfaces:**
- Consumes: `engine.platform.ffmpeg.resolve("ffmpeg")`、`engine.platform.paths.temp_dir()`
- Produces: `sample_frames(path: str, *, step: int, source_width: int, max_width: int = 1280, timeout: float = 300.0) -> list[tuple[int, Any]]` —— 返回 `(原始帧号, BGR ndarray)` 列表，帧号 = 输出序号 × step。

- [ ] **Step 1: 写失败测试**

```python
"""Frame sampling maps output files back to *source* frame numbers."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine.pipeline.frames import sample_frames
from engine.platform.ffmpeg import resolve

FFMPEG = resolve("ffmpeg").path


def _make_video(path: Path, frames: int = 12, width: int = 64, height: int = 48) -> None:
    command = [
        FFMPEG or "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=gray:s={width}x{height}:r=10",
        "-frames:v", str(frames),
        "-pix_fmt", "yuv420p", str(path),
    ]
    subprocess.run(command, check=True, capture_output=True)


@unittest.skipUnless(FFMPEG, "ffmpeg is required for frame sampling")
class SampleFramesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._scratch = tempfile.mkdtemp(prefix="eraser-frames-")
        cls.video = Path(cls._scratch) / "clip.mp4"
        _make_video(cls.video)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._scratch, ignore_errors=True)

    def test_returns_every_step_th_frame(self) -> None:
        frames = sample_frames(str(self.video), step=5, source_width=64)
        self.assertEqual([index for index, _ in frames], [0, 5, 10])

    def test_images_are_bgr_arrays(self) -> None:
        frames = sample_frames(str(self.video), step=5, source_width=64)
        image = frames[0][1]
        self.assertEqual(image.ndim, 3)
        self.assertEqual(image.shape[2], 3)

    def test_downscales_wide_sources(self) -> None:
        frames = sample_frames(str(self.video), step=10, source_width=2560, max_width=1280)
        self.assertEqual(frames[0][1].shape[1], 1280)

    def test_step_below_one_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sample_frames(str(self.video), step=0, source_width=64)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m unittest tests.test_frames -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'engine.pipeline.frames'`

- [ ] **Step 3: 写最小实现**

```python
"""Extract every N-th frame for analysis.

One ffmpeg pass rather than one call per sample: a 30 s clip at a 15-frame step
is 60 samples, and 60 process launches costs more than the decode itself.

The mapping from output file to source frame index relies on `select` keeping
input order and `-vsync vfr` writing one file per selected frame, so file *k* is
source frame `k * step`. The count is verified and a mismatch is an error rather
than silently mislabelled data.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import cv2

from engine.pipeline.logging import logger
from engine.platform.ffmpeg import resolve
from engine.platform.paths import temp_dir

JPEG_QUALITY = 3


class FrameSamplingError(RuntimeError):
    """The clip could not be sampled."""


def sample_frames(
    path: str,
    *,
    step: int,
    source_width: int,
    max_width: int = 1280,
    timeout: float = 300.0,
) -> list[tuple[int, Any]]:
    if step < 1:
        raise ValueError("step must be >= 1")
    if step == 1:
        raise ValueError("step=1 would decode the whole clip; sample instead")

    ffmpeg = resolve("ffmpeg").path
    if not ffmpeg:
        raise FrameSamplingError("ffmpeg not found")

    filters = [f"select=not(mod(n\\,{step}))"]
    if source_width > max_width > 0:
        filters.append(f"scale={max_width}:-2")

    scratch = Path(tempfile.mkdtemp(prefix="analyze-", dir=temp_dir()))
    try:
        pattern = str(scratch / "%05d.jpg")
        command = [
            ffmpeg, "-y", "-v", "error",
            "-i", path,
            "-vf", ",".join(filters),
            "-vsync", "vfr",
            "-q:v", str(JPEG_QUALITY),
            pattern,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FrameSamplingError(f"ffmpeg failed: {exc}") from exc

        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace")[-300:]
            raise FrameSamplingError(f"ffmpeg exit {completed.returncode}: {detail}")

        files = sorted(scratch.glob("*.jpg"))
        if not files:
            raise FrameSamplingError(f"no frames extracted from {path}")

        frames: list[tuple[int, Any]] = []
        for offset, file in enumerate(files):
            image = cv2.imread(str(file))
            if image is None:
                logger.warning("unreadable sampled frame %s", file)
                continue
            frames.append((offset * step, image))
        if not frames:
            raise FrameSamplingError(f"no readable frames in {path}")
        return frames
    finally:
        for file in scratch.glob("*.jpg"):
            try:
                file.unlink()
            except OSError:
                pass
        try:
            scratch.rmdir()
        except OSError:
            pass
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m unittest tests.test_frames -v`
Expected: 4 tests PASS（没有 ffmpeg 的环境会 skip，这是允许的）

- [ ] **Step 5: 提交**

```bash
git add engine/pipeline/frames.py tests/test_frames.py
git commit -m "feat(analysis): sample every N-th frame in a single ffmpeg pass"
```

---

### Task 4: 检测器抽象与脚本检测器

**Files:**
- Create: `engine/pipeline/detect/__init__.py`、`engine/pipeline/detect/base.py`、`engine/pipeline/detect/stub.py`
- Test: `tests/test_detectors.py`（新建）

**Interfaces:**
- Consumes: `engine.pipeline.analysis` 的 `BBox` / `Detection`
- Produces:
  - `WatermarkDetector`（抽象基类）：`model_id: str`、`display_name: str`、`load()`、`detect(frame, frame_index) -> list[Detection]`、`is_available() -> bool`、`get_warning() -> str`
  - `ScriptedDetector(model_id, detections: dict[int, list[Detection]])`
  - `load_detections(path: str) -> dict[int, list[Detection]]` —— 读取 `{"frames": {"0": [{"x","y","width","height","confidence"}], ...}}`
  - `detect/__init__.py` 导出 `available_detectors() -> list[WatermarkDetector]`（本里程碑：仅当 `ERASER_DETECT_STUB` 指向文件时返回该桩，否则空列表）

- [ ] **Step 1: 写失败测试**

```python
"""Detector plumbing: the seam a real model will plug into later."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine.pipeline.analysis import BBox, Detection
from engine.pipeline.detect import available_detectors
from engine.pipeline.detect.base import WatermarkDetector
from engine.pipeline.detect.stub import ScriptedDetector, load_detections


class ScriptedDetectorTests(unittest.TestCase):
    def test_returns_scripted_detections(self) -> None:
        detector = ScriptedDetector({0: [Detection(0, BBox(1, 2, 3, 4), 0.9)]})
        found = detector.detect(frame=None, frame_index=0)  # noqa: ANN001
        self.assertEqual(found[0].rect, BBox(1, 2, 3, 4))

    def test_unscripted_frame_is_empty(self) -> None:
        detector = ScriptedDetector({})
        self.assertEqual(detector.detect(None, 7), [])

    def test_load_requires_an_existing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_detections("/nope/missing.json")

    def test_load_reads_frame_map(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "detections.json"
            path.write_text(
                json.dumps({"frames": {"0": [{"x": 5, "y": 6, "width": 7, "height": 8, "confidence": 0.5}]}}),
                encoding="utf-8",
            )
            detections = load_detections(str(path))
        self.assertEqual(detections[0][0].rect, BBox(5, 6, 7, 8))
        self.assertAlmostEqual(detections[0][0].confidence, 0.5)

    def test_abstract_contract_cannot_be_instantiated(self) -> None:
        with self.assertRaises(TypeError):
            WatermarkDetector()  # type: ignore[abstract]


class RegistryTests(unittest.TestCase):
    def test_no_detector_by_default(self) -> None:
        self.assertEqual(available_detectors(environ={}), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m unittest tests.test_detectors -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'engine.pipeline.detect'`

- [ ] **Step 3: 写最小实现**

`engine/pipeline/detect/base.py`:

```python
"""The detector seam.

A detector answers one question per frame: is a watermark visible, and where.
Everything downstream (association, tracks, the UI) is detector-agnostic, so a
new model is a new subclass here and nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from engine.pipeline.analysis import Detection


class WatermarkDetector(ABC):
    model_id: str = "base"
    display_name: str = "Base detector"

    @abstractmethod
    def load(self) -> None:
        """Make the model ready; may download/initialise. Must be idempotent."""

    @abstractmethod
    def detect(self, frame: np.ndarray | None, frame_index: int) -> list[Detection]:
        """Return watermark candidates for one frame. Never raises for a bad frame."""

    def is_available(self) -> bool:
        return True

    def get_warning(self) -> str:
        return ""
```

`engine/pipeline/detect/stub.py`:

```python
"""A detector that replays a fixed set of boxes.

It exists so the analysis pipeline can be exercised end to end (tests, CLI,
demo) before a real vision model is wired in, and so bug reports can carry an
exact detection list instead of "it looked wrong".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from engine.pipeline.analysis import BBox, Detection
from engine.pipeline.detect.base import WatermarkDetector


def load_detections(path: str) -> dict[int, list[Detection]]:
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"detection script not found: {path}")
    payload = json.loads(file.read_text(encoding="utf-8"))
    raw_frames = payload.get("frames", payload) if isinstance(payload, dict) else {}
    detections: dict[int, list[Detection]] = {}
    for key, entries in raw_frames.items():
        try:
            frame_index = int(key)
        except (TypeError, ValueError):
            continue
        items: list[Detection] = []
        for entry in entries or []:
            data = entry if isinstance(entry, dict) else {}
            items.append(
                Detection(
                    frame=frame_index,
                    rect=BBox.from_dict(data),
                    confidence=float(data.get("confidence", 1.0) or 1.0),
                )
            )
        if items:
            detections[frame_index] = items
    return detections


class ScriptedDetector(WatermarkDetector):
    model_id = "scripted"
    display_name = "Scripted detections"

    def __init__(self, detections: dict[int, list[Detection]]) -> None:
        self._detections = dict(detections)

    def load(self) -> None:
        return None

    def detect(self, frame: np.ndarray | None, frame_index: int) -> list[Detection]:
        return list(self._detections.get(int(frame_index), ()))
```

`engine/pipeline/detect/__init__.py`:

```python
"""Detector registry.

Only the scripted detector exists in this milestone. A real one is added by
registering another instance here; nothing else in the codebase changes.
"""

from __future__ import annotations

import os
from typing import Mapping

from engine.pipeline.detect.base import WatermarkDetector
from engine.pipeline.detect.stub import ScriptedDetector, load_detections

ENV_DETECT_STUB = "ERASER_DETECT_STUB"

__all__ = ["WatermarkDetector", "ScriptedDetector", "load_detections", "available_detectors"]


def available_detectors(environ: Mapping[str, str] | None = None) -> list[WatermarkDetector]:
    """Detectors this build can run right now.

    `ERASER_DETECT_STUB` is a deliberate test/demo hook — the same pattern as
    `ERASER_STDERR_NOISE` — and is documented as such in docs/09.
    """
    env = os.environ if environ is None else environ
    path = (env.get(ENV_DETECT_STUB) or "").strip()
    if not path:
        return []
    try:
        return [ScriptedDetector(load_detections(path))]
    except (OSError, ValueError):
        return []
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m unittest tests.test_detectors -v`
Expected: 6 tests PASS

- [ ] **Step 5: 提交**

```bash
git add engine/pipeline/detect tests/test_detectors.py
git commit -m "feat(analysis): pluggable watermark detector with a scripted stand-in"
```

---

### Task 5: 跨帧关联与运动判定

**Files:**
- Modify: `engine/pipeline/analysis.py`（追加 `associate()`、`classify_motion()`）
- Test: `tests/test_analysis_association.py`（新建）

**Interfaces:**
- Consumes: Task 2 的模型、Task 4 的 `Detection`
- Produces:
  - `associate(detections: dict[int, list[Detection]], *, frame_count: int, frame_width: int, frame_height: int, fps: float, min_confidence: float = 0.35, max_area_ratio: float = 0.25, max_gap_seconds: float = 5.0, max_tracks: int = 8) -> list[WatermarkTrack]`
  - `classify_motion(track: WatermarkTrack, *, frame_width: int, frame_height: int) -> str`

**规则（来自 spec §4.3，测试即规范）：**
1. 相邻采样点 IoU ≥ 0.5 匹配；否则中心距离 ≤ 画面宽 8% 也匹配（快速移动）。
2. 间隔 ≤ 1.5s 的"重新出现"并入**同一区间**（漏检不应切碎时间线）。
3. 间隔 ≤ 5s 且尺寸/宽高比相似（±25%）→ **同一 track 的新区间**；否则新 track。
4. `confidence < 0.35` 或面积 > 画面 25% 的框丢弃。
5. 最多 8 个 track，按首个关键帧排序。

- [ ] **Step 1: 写失败测试**

```python
"""Association is the hard part: these tests are the specification."""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import (
    BBox,
    Detection,
    WatermarkTrack,
    associate,
    classify_motion,
)

W, H, FPS = 640, 360, 30.0
FRAMES = 600


def _det(frame: int, x: int, y: int = 20) -> Detection:
    return Detection(frame, BBox(x, y, 80, 40), 0.9)


def _run(detections: dict[int, list[Detection]], **kwargs) -> list[WatermarkTrack]:
    params = dict(frame_count=FRAMES, frame_width=W, frame_height=H, fps=FPS)
    params.update(kwargs)
    return associate(detections, **params)


class ContinuationTests(unittest.TestCase):
    def test_single_static_watermark_is_one_track(self) -> None:
        tracks = _run({frame: [_det(frame, 100)] for frame in (0, 15, 30)})
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].intervals[0].start_frame, 0)
        self.assertEqual(tracks[0].intervals[0].end_frame, 30)
        self.assertEqual(len(tracks[0].keyframes), 3)

    def test_slow_motion_still_continues(self) -> None:
        tracks = _run({frame: [_det(frame, 100 + frame // 2)] for frame in (0, 15, 30)})
        self.assertEqual(len(tracks), 1)

    def test_single_missed_sample_does_not_split_the_interval(self) -> None:
        # 30 fps, samples every 15 frames: one missed sample is a 0.5 s gap,
        # well inside the 1.5 s merge window.
        tracks = _run({0: [_det(0, 100)], 15: [], 30: [_det(30, 100)]})
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0].intervals), 1)

    def test_gap_between_the_two_windows_starts_a_second_interval(self) -> None:
        # 90 frames at 30 fps = 3 s: past the 1.5 s merge window,
        # still inside the 5 s reopen window.
        tracks = _run({0: [_det(0, 100)], 90: [_det(90, 100)]})
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0].intervals), 2)

    def test_very_long_absence_creates_a_new_track(self) -> None:
        # 300 frames at 30 fps = 10 s: past the reopen window.
        tracks = _run({0: [_det(0, 100)], 300: [_det(300, 100)]})
        self.assertEqual(len(tracks), 2)


class SeparationTests(unittest.TestCase):
    def test_two_simultaneous_watermarks_stay_separate(self) -> None:
        detections = {
            frame: [_det(frame, 40), _det(frame, 480)]
            for frame in (0, 10, 20)
        }
        tracks = _run(detections)
        self.assertEqual(len(tracks), 2)

    def test_low_confidence_is_dropped(self) -> None:
        detections = {0: [Detection(0, BBox(10, 10, 20, 20), 0.1)]}
        self.assertEqual(_run(detections), [])

    def test_oversized_box_is_dropped(self) -> None:
        detections = {0: [Detection(0, BBox(0, 0, W, H), 0.99)]}
        self.assertEqual(_run(detections), [])

    def test_track_cap_is_enforced(self) -> None:
        detections = {0: [_det(0, 10 + index * 100) for index in range(12)]}
        self.assertEqual(len(_run(detections)), 8)


class MotionTests(unittest.TestCase):
    def test_static_track(self) -> None:
        track = _run({0: [_det(0, 100)], 15: [_det(15, 100)]})[0]
        self.assertEqual(classify_motion(track, frame_width=W, frame_height=H), "static")

    def test_large_displacement_is_moving(self) -> None:
        # 40 px of travel: inside the 8%-of-width association window (51 px), so
        # this is one track — and past the 5%-of-width motion threshold (32 px).
        track = _run({0: [_det(0, 100)], 15: [_det(15, 140)]})[0]
        self.assertEqual(classify_motion(track, frame_width=W, frame_height=H), "moving")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analysis_association -v`
Expected: FAIL —— `ImportError: cannot import name 'associate'`

- [ ] **Step 3: 写实现（追加到 `engine/pipeline/analysis.py` 末尾）**

```python
IOU_MATCH = 0.5
#: A re-appearance closer than this (in seconds) is a missed detection, not a
#: second appearance — the timeline must not shatter over one dropped sample.
MERGE_GAP_SECONDS = 1.5
#: Beyond this the watermark is considered to have come back as a new interval.
REOPEN_GAP_SECONDS = 5.0
SIZE_SIMILARITY = 0.25


def _similar_size(a: BBox, b: BBox) -> bool:
    if min(a.width, a.height, b.width, b.height) <= 0:
        return False

    def ratio(x: float, y: float) -> float:
        return abs(x - y) / max(1e-6, max(x, y))

    return (
        ratio(a.width, b.width) <= SIZE_SIMILARITY
        and ratio(a.height, b.height) <= SIZE_SIMILARITY
        and ratio(a.width / a.height, b.width / b.height) <= SIZE_SIMILARITY
    )


def associate(
    detections: dict[int, list[Detection]],
    *,
    frame_count: int,
    frame_width: int,
    frame_height: int,
    fps: float,
    min_confidence: float = 0.35,
    max_area_ratio: float = 0.25,
    max_gap_seconds: float = REOPEN_GAP_SECONDS,
    max_tracks: int = 8,
) -> list[WatermarkTrack]:
    """Group per-frame detections into watermark tracks.

    Greedy and deterministic on purpose: a wrong grouping is visible in the UI
    and fixable by splitting/merging, whereas a non-deterministic grouping would
    make the same clip analyse differently on every run.
    """
    del frame_count  # kept for signature symmetry with the callers
    frame_area = max(1, frame_width * frame_height)
    usable_gap = max(0.0, float(max_gap_seconds))

    open_tracks: list[_MutableTrack] = []
    idle_tracks: list[_MutableTrack] = []
    next_id = 1

    for frame in sorted(detections):
        # A track unseen for longer than the merge window is no longer a
        # *continuation* candidate: it either reopens as a second interval of the
        # same watermark (inside the reopen window) or becomes a new track.
        expired = [
            track
            for track in open_tracks
            if fps > 0 and (frame - track.last_frame) / fps > MERGE_GAP_SECONDS
        ]
        for track in expired:
            open_tracks.remove(track)
            idle_tracks.append(track)

        candidates = [
            item
            for item in detections[frame]
            if item.confidence >= min_confidence
            and 0 < item.rect.area <= frame_area * max_area_ratio
        ]
        used: set[int] = set()

        for track in sorted(open_tracks, key=lambda item: item.last_frame, reverse=True):
            index, score = _best_match(track.last_rect, candidates, used)
            if index is None:
                continue
            distance = center_distance(track.last_rect, candidates[index].rect)
            if score < IOU_MATCH and distance > 0.08 * frame_width:
                continue
            _extend(track, frame, candidates[index])
            used.add(index)

        for index, detection in enumerate(candidates):
            if index in used:
                continue
            track = _reopenable(idle_tracks, detection, frame, fps, usable_gap)
            if track is not None:
                idle_tracks.remove(track)
                gap = (frame - track.last_frame) / fps if fps > 0 else 0.0
                if gap <= MERGE_GAP_SECONDS:
                    track.intervals[-1] = Interval(track.intervals[-1].start_frame, frame)
                else:
                    track.intervals.append(Interval(frame, frame))
                track.keyframes.append(Keyframe(frame, detection.rect))
                track.confidences.append(detection.confidence)
                track.last_frame, track.last_rect = frame, detection.rect
                open_tracks.append(track)
                used.add(index)
                continue

            created = _MutableTrack(
                id=f"wm-{next_id}",
                intervals=[Interval(frame, frame)],
                keyframes=[Keyframe(frame, detection.rect)],
                confidences=[detection.confidence],
                last_frame=frame,
                last_rect=detection.rect,
            )
            next_id += 1
            open_tracks.append(created)
            used.add(index)

        still_open: list[_MutableTrack] = []
        for track in open_tracks:
            if track.last_frame == frame:
                still_open.append(track)
            else:
                idle_tracks.append(track)
        open_tracks = still_open

    idle_tracks.extend(open_tracks)
    tracks = [_finalise(item, frame_width, frame_height) for item in idle_tracks]
    tracks.sort(key=lambda item: (item.intervals[0].start_frame, item.id))
    return tracks[:max_tracks]


@dataclass
class _MutableTrack:
    id: str
    intervals: list[Interval]
    keyframes: list[Keyframe]
    confidences: list[float]
    last_frame: int
    last_rect: BBox


def _best_match(
    reference: BBox,
    candidates: list[Detection],
    used: set[int],
) -> tuple[int | None, float]:
    best_index: int | None = None
    best_score = 0.0
    for index, item in enumerate(candidates):
        if index in used:
            continue
        score = iou(reference, item.rect)
        if score > best_score:
            best_index, best_score = index, score
    return best_index, best_score


def _extend(track: _MutableTrack, frame: int, detection: Detection) -> None:
    track.intervals[-1] = Interval(track.intervals[-1].start_frame, frame)
    track.keyframes.append(Keyframe(frame, detection.rect))
    track.confidences.append(detection.confidence)
    track.last_frame, track.last_rect = frame, detection.rect


def _reopenable(
    idle: list[_MutableTrack],
    detection: Detection,
    frame: int,
    fps: float,
    max_gap_seconds: float,
) -> _MutableTrack | None:
    best: _MutableTrack | None = None
    best_gap = float("inf")
    for track in idle:
        gap = (frame - track.last_frame) / fps if fps > 0 else float("inf")
        if gap < 0 or gap > max_gap_seconds:
            continue
        if not _similar_size(track.last_rect, detection.rect):
            continue
        if gap < best_gap:
            best, best_gap = track, gap
    return best


def _finalise(track: _MutableTrack, frame_width: int, frame_height: int) -> WatermarkTrack:
    confidence = sum(track.confidences) / max(1, len(track.confidences))
    result = WatermarkTrack(
        id=track.id,
        intervals=list(track.intervals),
        keyframes=list(track.keyframes),
        confidence=confidence,
    )
    result.motion = classify_motion(result, frame_width=frame_width, frame_height=frame_height)
    return result


def classify_motion(track: WatermarkTrack, *, frame_width: int, frame_height: int) -> str:
    """`moving` when the box travels or changes size appreciably."""
    if len(track.keyframes) < 2:
        return "static"
    centers = [item.rect.center for item in track.keyframes]
    xs = [point[0] for point in centers]
    ys = [point[1] for point in centers]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    widths = [max(1, item.rect.width) for item in track.keyframes]
    heights = [max(1, item.rect.height) for item in track.keyframes]
    grows = max(max(widths) / min(widths), max(heights) / min(heights))
    if dx > 0.05 * frame_width or dy > 0.05 * frame_height:
        return "moving"
    if grows > 1.15:
        return "moving"
    return "static"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analysis_association -v`
Expected: 11 tests PASS

> 两个时间窗口是这套规则的核心，测试即规范：
> **合并窗口 1.5 s**（漏检不算断）、**重开窗口 5 s**（同一水印再次出现算第二段区间）。
> 超过 5 s 就当作另一个水印——用户可以在界面上合并，算法不替人猜。

- [ ] **Step 5: 提交**

```bash
git add engine/pipeline/analysis.py tests/test_analysis_association.py
git commit -m "feat(analysis): associate detections into multi-interval watermark tracks"
```

---

### Task 6: 镜头切分与处理建议

**Files:**
- Modify: `engine/pipeline/analysis.py`（追加 `build_shots()`、`suggest_strategy()`）
- Test: `tests/test_analysis_shots.py`（新建）

**Interfaces:**
- Produces:
  - `build_shots(cuts: list[float], *, fps: float, frame_count: int) -> list[Interval]`
  - `suggest_strategy(shots, watermarks, *, engines: list[str] | None = None) -> dict` —— 返回 `{"engine_id", "split_by_shot", "warnings"}`

- [ ] **Step 1: 写失败测试**

```python
"""Shot boundaries and the advice shown next to them."""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import (
    BBox,
    Interval,
    Keyframe,
    WatermarkTrack,
    build_shots,
    suggest_strategy,
)


class ShotTests(unittest.TestCase):
    def test_no_cuts_means_one_shot(self) -> None:
        self.assertEqual(build_shots([], fps=10.0, frame_count=100), [Interval(0, 99)])

    def test_cuts_split_the_timeline(self) -> None:
        shots = build_shots([1.0, 2.0], fps=10.0, frame_count=30)
        self.assertEqual(shots, [Interval(0, 9), Interval(10, 19), Interval(20, 29)])

    def test_cut_at_the_very_start_is_ignored(self) -> None:
        self.assertEqual(build_shots([0.0], fps=10.0, frame_count=30), [Interval(0, 29)])

    def test_cut_past_the_end_is_ignored(self) -> None:
        self.assertEqual(build_shots([99.0], fps=10.0, frame_count=30), [Interval(0, 29)])

    def test_zero_length_video(self) -> None:
        self.assertEqual(build_shots([], fps=10.0, frame_count=0), [Interval(0, 0)])


class StrategyTests(unittest.TestCase):
    @staticmethod
    def _track(motion: str = "static") -> WatermarkTrack:
        return WatermarkTrack(
            id="wm-1",
            intervals=[Interval(0, 99)],
            keyframes=[Keyframe(0, BBox(10, 10, 20, 20))],
            motion=motion,
        )

    def test_no_watermark_does_not_start_processing(self) -> None:
        advice = suggest_strategy([Interval(0, 99)], [])
        self.assertIn("no_watermark", advice["warnings"])

    def test_single_shot_static_prefers_fast_engine(self) -> None:
        advice = suggest_strategy([Interval(0, 99)], [self._track()], engines=["lama_roi", "propainter_roi"])
        self.assertEqual(advice["engine_id"], "lama_roi")
        self.assertFalse(advice["split_by_shot"])

    def test_multi_shot_requests_splitting(self) -> None:
        advice = suggest_strategy(
            [Interval(0, 49), Interval(50, 99)],
            [self._track()],
            engines=["lama_roi", "propainter_roi"],
        )
        self.assertTrue(advice["split_by_shot"])

    def test_moving_watermark_requests_the_quality_engine(self) -> None:
        advice = suggest_strategy(
            [Interval(0, 99)],
            [self._track("moving")],
            engines=["lama_roi", "propainter_roi"],
        )
        self.assertEqual(advice["engine_id"], "propainter_roi")
        self.assertIn("moving_watermark", advice["warnings"])

    def test_quality_engine_unavailable_falls_back_with_a_warning(self) -> None:
        advice = suggest_strategy([Interval(0, 99)], [self._track("moving")], engines=["lama_roi"])
        self.assertEqual(advice["engine_id"], "lama_roi")
        self.assertIn("quality_engine_unavailable", advice["warnings"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analysis_shots -v`
Expected: FAIL —— `ImportError: cannot import name 'build_shots'`

- [ ] **Step 3: 写实现（追加到 `engine/pipeline/analysis.py`）**

```python
ENGINE_FAST = "lama_roi"
ENGINE_QUALITY = "propainter_roi"


def build_shots(cuts: list[float], *, fps: float, frame_count: int) -> list[Interval]:
    """Turn cut timestamps into frame intervals covering the whole clip.

    Degenerate input (no frames, cuts outside the clip) must still yield at
    least one shot: the analysis report has to show something for a 1-frame
    video rather than an empty timeline.
    """
    total = max(1, int(frame_count))
    last = total - 1
    if last <= 0:
        return [Interval(0, 0)]

    boundaries = [0]
    for cut in sorted(float(item) for item in cuts):
        if fps <= 0:
            continue
        frame = int(round(cut * fps))
        if 0 < frame <= last and frame - boundaries[-1] > 0:
            boundaries.append(frame)
    # Exclusive upper bound: the last shot must *include* the final frame, so a
    # 100-frame clip is (0, 99) and not (0, 98).
    if boundaries[-1] != last + 1:
        boundaries.append(last + 1)

    shots: list[Interval] = []
    for index in range(len(boundaries) - 1):
        start = boundaries[index]
        end = boundaries[index + 1] - 1
        if end >= start:
            shots.append(Interval(start, end))
    return shots or [Interval(0, 0)]


def suggest_strategy(
    shots: list[Interval],
    watermarks: list[WatermarkTrack],
    *,
    engines: list[str] | None = None,
) -> dict[str, Any]:
    """Advice for the confirmation screen. Never a decision on the user's behalf."""
    available = list(engines) if engines is not None else [ENGINE_FAST]
    warnings: list[str] = []

    if not watermarks:
        warnings.append("no_watermark")
        return {"engine_id": ENGINE_FAST, "split_by_shot": False, "warnings": warnings}

    moves = any(item.motion == "moving" for item in watermarks)
    multi_shot = len(shots) > 1
    if moves:
        warnings.append("moving_watermark")

    wants_quality = moves or multi_shot
    engine = ENGINE_FAST
    if wants_quality:
        if ENGINE_QUALITY in available:
            engine = ENGINE_QUALITY
        else:
            warnings.append("quality_engine_unavailable")

    return {"engine_id": engine, "split_by_shot": multi_shot, "warnings": warnings}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analysis_shots -v`
Expected: 10 tests PASS

- [ ] **Step 5: 提交**

```bash
git add engine/pipeline/analysis.py tests/test_analysis_shots.py
git commit -m "feat(analysis): shot boundaries and engine advice for the confirm screen"
```

---

### Task 7: 编译 —— tracks → segments

**Files:**
- Modify: `engine/pipeline/analysis.py`（追加 `compile_tracks()`）
- Test: `tests/test_analysis_compile.py`（新建）

**Interfaces:**
- Consumes: Task 2/5/6 的模型
- Produces: `compile_tracks(tracks, *, shots, frame_width, frame_height, expand_px=5, feather_px=3) -> list[dict[str, Any]]` —— 每个 dict 同时满足 `Segment` 契约（`start_frame`/`end_frame`/`rect`/`expand_px`/`feather_px`/`enabled`）**并带 `keyframes`**。

- [ ] **Step 1: 写失败测试**

```python
"""Tracks compile down to the Segment shape the pipeline already eats."""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import (
    BBox,
    Interval,
    Keyframe,
    WatermarkTrack,
    compile_tracks,
)


def _track(intervals, keyframes, motion="static") -> WatermarkTrack:
    return WatermarkTrack(
        id="wm-1",
        intervals=[Interval(*item) for item in intervals],
        keyframes=[Keyframe(frame, BBox(*rect)) for frame, rect in keyframes],
        motion=motion,
    )


class CompileTests(unittest.TestCase):
    def test_static_single_shot_yields_one_segment(self) -> None:
        track = _track([(0, 99)], [(0, (10, 20, 30, 40))])
        segments = compile_tracks([track], shots=[Interval(0, 99)], frame_width=640, frame_height=360)
        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertEqual((segment["start_frame"], segment["end_frame"]), (0, 99))
        self.assertEqual(segment["rect"], {"x": 10, "y": 20, "width": 30, "height": 40})
        self.assertEqual(segment["expand_px"], 5)
        self.assertEqual(segment["feather_px"], 3)
        self.assertTrue(segment["enabled"])

    def test_multi_shot_splits_the_track(self) -> None:
        track = _track([(0, 99)], [(0, (10, 20, 30, 40))])
        segments = compile_tracks(
            [track],
            shots=[Interval(0, 49), Interval(50, 99)],
            frame_width=640,
            frame_height=360,
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["end_frame"], 49)
        self.assertEqual(segments[1]["start_frame"], 50)

    def test_moving_track_uses_the_union_box(self) -> None:
        track = _track(
            [(0, 49)],
            [(0, (10, 20, 30, 30)), (40, (200, 20, 30, 30))],
            motion="moving",
        )
        segment = compile_tracks([track], shots=[Interval(0, 49)], frame_width=640, frame_height=360)[0]
        self.assertEqual(segment["rect"], {"x": 10, "y": 20, "width": 220, "height": 30})

    def test_keyframes_are_kept_and_clipped_to_the_segment(self) -> None:
        track = _track([(0, 99)], [(0, (10, 10, 10, 10)), (60, (50, 10, 10, 10))])
        segments = compile_tracks(
            [track],
            shots=[Interval(0, 49), Interval(50, 99)],
            frame_width=640,
            frame_height=360,
        )
        self.assertEqual([item["frame"] for item in segments[0]["keyframes"]], [0])
        self.assertEqual([item["frame"] for item in segments[1]["keyframes"]], [60])

    def test_segment_ids_are_unique(self) -> None:
        track = _track([(0, 99)], [(0, (10, 10, 10, 10))])
        segments = compile_tracks(
            [track],
            shots=[Interval(0, 49), Interval(50, 99)],
            frame_width=640,
            frame_height=360,
        )
        self.assertEqual(len({item["id"] for item in segments}), len(segments))

    def test_box_is_clamped_to_the_frame(self) -> None:
        track = _track([(0, 9)], [(0, (630, 350, 100, 100))])
        segment = compile_tracks([track], shots=[Interval(0, 9)], frame_width=640, frame_height=360)[0]
        self.assertEqual(segment["rect"], {"x": 630, "y": 350, "width": 10, "height": 10})

    def test_empty_track_list_compiles_to_nothing(self) -> None:
        self.assertEqual(compile_tracks([], shots=[Interval(0, 9)], frame_width=640, frame_height=360), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analysis_compile -v`
Expected: FAIL —— `ImportError: cannot import name 'compile_tracks'`

- [ ] **Step 3: 写实现（追加到 `engine/pipeline/analysis.py`）**

```python
def compile_tracks(
    tracks: list[WatermarkTrack],
    *,
    shots: list[Interval],
    frame_width: int,
    frame_height: int,
    expand_px: int = 5,
    feather_px: int = 3,
) -> list[dict[str, Any]]:
    """Flatten tracks into the Segment list the existing pipeline understands.

    A track that spans a cut becomes one segment per shot: a temporal model must
    not interpolate a trajectory across a scene change, and the pipeline already
    treats segments as independent runs.

    The ROI is the union of the keyframes inside the range, so a moving watermark
    keeps a *stable* crop while its per-frame mask moves — stable crops are what
    the flow-propagation engines want.
    """
    segments: list[dict[str, Any]] = []
    effective_shots = shots or [Interval(0, max(0, max(
        (item.intervals[-1].end_frame for item in tracks if item.intervals), default=0
    )))]

    for track in tracks:
        for interval in track.intervals:
            for shot in effective_shots:
                start = max(interval.start_frame, shot.start_frame)
                end = min(interval.end_frame, shot.end_frame)
                if end < start:
                    continue

                inside = [item for item in track.keyframes if start <= item.frame <= end]
                if not inside:
                    before = [item for item in track.keyframes if item.frame <= end]
                    inside = before[-1:] or track.keyframes[:1]
                if not inside:
                    continue

                rect = BBox.union([item.rect for item in inside]).clamp(frame_width, frame_height)
                segments.append(
                    {
                        "id": f"{track.id}@{start}-{end}",
                        "start_frame": int(start),
                        "end_frame": int(end),
                        "rect": rect.as_dict(),
                        "expand_px": int(expand_px),
                        "feather_px": int(feather_px),
                        "enabled": True,
                        "keyframes": [
                            {"frame": int(item.frame), "rect": item.rect.as_dict()}
                            for item in inside
                        ],
                    }
                )

    segments.sort(key=lambda item: (item["start_frame"], item["end_frame"], item["id"]))
    return segments
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analysis_compile -v`
Expected: 7 tests PASS

- [ ] **Step 5: 提交**

```bash
git add engine/pipeline/analysis.py tests/test_analysis_compile.py
git commit -m "feat(analysis): compile watermark tracks into pipeline segments"
```

---

### Task 8: 契约与 RPC

**Files:**
- Modify: `contract/methods.json`（新增 3 个方法、4 个类型，`Segment` 增 `keyframes`）
- Modify: `engine/methods/annotation.py`（注册 `annotation.compile`）
- Create: `engine/pipeline/analyze_job.py`、`engine/methods/analyze.py`
- Modify: `engine/methods/__init__.py`
- Modify: `tests/test_contract.py`
- Regenerate: `engine/generated/contract.py`、`desktop/electron/generated/contract.ts`
- Test: `tests/test_analyze_methods.py`（新建）

**Interfaces:**
- Consumes: Task 3–7 的全部能力；`RpcServer.register`；`TaskManager.create/update/finish`
- Produces:
  - RPC `analyze.capabilities` → `{"detectors": [...], "available": bool}`
  - RPC `analyze.start` → `{"task_id": str}`；结果经 `process.status` 的 `result` 返回 `Analysis`
  - RPC `annotation.compile` → `{"segments": [...]}`
  - `engine.pipeline.analyze_job.run_analysis(input_path: str, detector: WatermarkDetector | None, *, sample_step: int = 15, max_tracks: int = 8, min_confidence: float = 0.35, progress: Callable[[dict], None] | None = None, cancel: Any = None, engines: list[str] | None = None) -> Analysis`

- [ ] **Step 1: 改契约（`contract/methods.json`）**

在 `"types"` 里加四个类型（放在 `Segment` 之后）：

```json
"FrameInterval": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "start_frame": { "type": "integer" },
    "end_frame": { "type": "integer" }
  },
  "required": ["start_frame", "end_frame"]
},
"RectKeyframe": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "frame": { "type": "integer" },
    "rect": { "$ref": "#/types/Rect" }
  },
  "required": ["frame", "rect"]
},
"WatermarkTrack": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "id": { "type": "string" },
    "intervals": { "type": "array", "items": { "$ref": "#/types/FrameInterval" } },
    "keyframes": { "type": "array", "items": { "$ref": "#/types/RectKeyframe" } },
    "motion": { "type": "string" },
    "confidence": { "type": "number" },
    "source": { "type": "string" },
    "notes": { "type": "array", "items": { "type": "string" } }
  },
  "required": ["id", "intervals", "keyframes"]
},
"DetectorEntry": {
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "model_id": { "type": "string" },
    "display_name": { "type": "string" },
    "installed": { "type": "boolean" },
    "available": { "type": "boolean" },
    "blocked_reason": { "type": "string" }
  },
  "required": ["model_id", "display_name", "installed", "available"]
}
```

在 `Segment` 的 `properties` 里加一个可选字段（不加进 `required`）：

```json
"keyframes": { "type": "array", "items": { "$ref": "#/types/RectKeyframe" } }
```

在 `"methods"` 数组末尾（`assets.verify` 之后）加三个方法：

```json
{
  "name": "analyze.capabilities",
  "summary": "Report which watermark detectors can run on this machine.",
  "implemented": true,
  "params": { "type": "object", "additionalProperties": false, "properties": {} },
  "result": {
    "type": "object",
    "properties": {
      "detectors": { "type": "array", "items": { "$ref": "#/types/DetectorEntry" } },
      "available": { "type": "boolean" }
    },
    "required": ["detectors", "available"]
  }
},
{
  "name": "analyze.start",
  "summary": "Analyse a video: shots, watermark tracks, intervals and motion.",
  "implemented": true,
  "params": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "input_path": { "type": "string" },
      "sample_step": { "type": "integer" },
      "max_tracks": { "type": "integer" },
      "min_confidence": { "type": "number" }
    },
    "required": ["input_path"]
  },
  "result": {
    "type": "object",
    "properties": { "task_id": { "type": "string" } },
    "required": ["task_id"]
  }
},
{
  "name": "annotation.compile",
  "summary": "Turn watermark tracks into the segment list the pipeline consumes.",
  "implemented": true,
  "params": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "watermarks": { "type": "array", "items": { "$ref": "#/types/WatermarkTrack" } },
      "shots": { "type": "array", "items": { "$ref": "#/types/FrameInterval" } },
      "video": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "width": { "type": "integer" },
          "height": { "type": "integer" },
          "frame_count": { "type": "integer" }
        },
        "required": ["width", "height", "frame_count"]
      }
    },
    "required": ["watermarks", "shots", "video"]
  },
  "result": {
    "type": "object",
    "properties": {
      "segments": { "type": "array", "items": { "$ref": "#/types/Segment" } }
    },
    "required": ["segments"]
  }
}
```

- [ ] **Step 2: 重新生成两端契约产物**

Run: `.venv/Scripts/python.exe contract/codegen/generate.py`
Expected: 无输出（成功），`engine/generated/contract.py` 与 `desktop/electron/generated/contract.ts` 均被更新

- [ ] **Step 3: 写失败的 RPC 测试**

```python
"""analyze.* and annotation.compile at the RPC boundary."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from engine.methods import register_all
from engine.rpc.server import RpcServer


class _Harness:
    """Drives RpcServer over two in-memory streams."""

    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.server = RpcServer(stdin=self.stdin, stdout=self.stdout)
        register_all(self.server)

    def call(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        self.server._dispatch(json.dumps(payload))  # noqa: SLF001 - test harness
        # A background task may emit notifications on the same stream, so match
        # on the request id instead of assuming the last line is the response.
        for line in reversed(self.stdout.getvalue().strip().splitlines()):
            frame = json.loads(line)
            if frame.get("id") == 1:
                return frame
        raise AssertionError(f"no response frame for {method}")


class AnalyzeCapabilitiesTests(unittest.TestCase):
    def test_reports_a_shape_the_ui_can_render(self) -> None:
        frame = _Harness().call("analyze.capabilities")
        result = frame["result"]
        self.assertIn("detectors", result)
        self.assertIn("available", result)

    def test_without_a_detector_nothing_is_available(self) -> None:
        frame = _Harness().call("analyze.capabilities", {})
        self.assertFalse(frame["result"]["available"])


class CompileTests(unittest.TestCase):
    def test_compile_returns_segments(self) -> None:
        frame = _Harness().call(
            "annotation.compile",
            {
                "watermarks": [
                    {
                        "id": "wm-1",
                        "intervals": [{"start_frame": 0, "end_frame": 49}],
                        "keyframes": [{"frame": 0, "rect": {"x": 1, "y": 2, "width": 3, "height": 4}}],
                    }
                ],
                "shots": [{"start_frame": 0, "end_frame": 49}],
                "video": {"width": 640, "height": 360, "frame_count": 50},
            },
        )
        segments = frame["result"]["segments"]
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["start_frame"], 0)

    def test_compile_rejects_a_missing_video_block(self) -> None:
        frame = _Harness().call("annotation.compile", {"watermarks": [], "shots": []})
        self.assertEqual(frame["error"]["data"]["code"], "INVALID_PARAMS")


class AnalyzeStartTests(unittest.TestCase):
    def test_missing_file_is_rejected(self) -> None:
        frame = _Harness().call("analyze.start", {"input_path": "/nope/missing.mp4"})
        self.assertEqual(frame["error"]["data"]["code"], "INVALID_PARAMS")

    def test_without_a_detector_it_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"not really a video")
            frame = _Harness().call("analyze.start", {"input_path": str(video)})
        self.assertEqual(frame["error"]["data"]["code"], "DETECTOR_MISSING")

    def test_with_a_stub_detector_it_returns_a_task_id(self) -> None:
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"not really a video")
            script = Path(scratch) / "detections.json"
            script.write_text('{"frames": {}}', encoding="utf-8")
            with patch.dict(os.environ, {"ERASER_DETECT_STUB": str(script)}):
                frame = _Harness().call("analyze.start", {"input_path": str(video)})
        self.assertIn("task_id", frame.get("result", {}), "start must return immediately")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analyze_methods -v`
Expected: FAIL —— `METHOD_NOT_FOUND: Unknown method: analyze.capabilities`

- [ ] **Step 5: 写实现**

`engine/pipeline/analyze_job.py`：

```python
"""One analysis run, independent of who asked for it.

Mirrors `engine/runner.py`: the RPC handler and the CLI must do exactly the same
thing, so the orchestration lives here rather than being duplicated.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from engine.pipeline.analysis import (
    Analysis,
    Detection,
    build_shots,
    associate,
    suggest_strategy,
)
from engine.pipeline.detect.base import WatermarkDetector
from engine.pipeline.frames import FrameSamplingError, sample_frames
from engine.pipeline.logging import logger
from engine.pipeline.upscale_plan import detect_scene_cuts

ProgressCallback = Callable[[dict[str, Any]], None]

DEFAULT_SAMPLE_STEP = 15
DEFAULT_MAX_TRACKS = 8
DEFAULT_MIN_CONFIDENCE = 0.35
ANALYSIS_MAX_WIDTH = 1280


class AnalysisError(RuntimeError):
    """The clip could not be analysed."""


def run_analysis(
    input_path: str,
    detector: WatermarkDetector | None,
    *,
    sample_step: int = DEFAULT_SAMPLE_STEP,
    max_tracks: int = DEFAULT_MAX_TRACKS,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    progress: ProgressCallback | None = None,
    cancel: Any = None,
    engines: list[str] | None = None,
) -> Analysis:
    from engine.pipeline.watermark import VideoProcessor

    if detector is None:
        raise AnalysisError("no watermark detector is available")

    started = time.monotonic()

    def emit(**payload: Any) -> None:
        if progress is not None:
            try:
                progress(payload)
            except Exception as exc:  # noqa: BLE001 - progress must never break a run
                logger.debug("dropping analysis progress event: %s", exc)

    emit(phase="probe", progress=0.02, message="Reading video")
    info = VideoProcessor(remover=None).get_video_info(input_path)
    frame_count = int(info.frame_count)
    if frame_count <= 0:
        raise AnalysisError("the clip reports no frames")

    emit(phase="shots", progress=0.08, message="Finding shot boundaries")
    cuts = detect_scene_cuts(input_path)
    shots = build_shots(cuts, fps=float(info.fps), frame_count=frame_count)
    emit(phase="shots", progress=0.15, message=f"{len(shots)} shot(s)")

    if cancel is not None and cancel.is_set():
        raise AnalysisError("cancelled")

    emit(phase="sample", progress=0.2, message="Sampling frames")
    try:
        frames = sample_frames(
            input_path,
            step=max(2, int(sample_step)),
            source_width=int(info.width),
            max_width=ANALYSIS_MAX_WIDTH,
        )
    except FrameSamplingError as exc:
        raise AnalysisError(f"cannot sample frames: {exc}") from exc

    detector.load()
    detections: dict[int, list[Detection]] = {}
    total = max(1, len(frames))
    for position, (frame_index, image) in enumerate(frames, start=1):
        if cancel is not None and cancel.is_set():
            raise AnalysisError("cancelled")
        detections[frame_index] = detector.detect(image, frame_index)
        if position == total or position % 5 == 0:
            emit(
                phase="detect",
                progress=0.2 + 0.6 * (position / total),
                message=f"Detecting {position}/{total}",
            )

    emit(phase="associate", progress=0.85, message="Grouping detections")
    tracks = associate(
        detections,
        frame_count=frame_count,
        frame_width=int(info.width),
        frame_height=int(info.height),
        fps=float(info.fps),
        max_tracks=max(1, int(max_tracks)),
        min_confidence=float(min_confidence),
    )

    suggested = suggest_strategy(shots, tracks, engines=engines)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "analysis: %d frame(s) sampled, %d shot(s), %d track(s) in %d ms",
        len(frames), len(shots), len(tracks), elapsed_ms,
    )
    emit(phase="finalize", progress=0.99, message="Analysis complete")
    return Analysis(
        video={
            "width": int(info.width),
            "height": int(info.height),
            "fps": float(info.fps),
            "frame_count": frame_count,
            "duration_seconds": float(info.duration),
        },
        shots=shots,
        watermarks=tracks,
        suggested=suggested,
        model_id=detector.model_id,
        elapsed_ms=elapsed_ms,
    )
```

`engine/methods/analyze.py`：

```python
"""analyze.* handlers: capabilities and start.

Detection is injected, and in this milestone the only registered detector is the
scripted one (`ERASER_DETECT_STUB`), so `analyze.capabilities` honestly reports
"nothing available" on a stock install rather than pretending.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from engine.pipeline.detect import available_detectors
from engine.pipeline.logging import logger
from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer
from engine.tasks import TaskManager, TaskRecord


def _detector_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for detector in available_detectors():
        available = False
        reason = ""
        try:
            available = bool(detector.is_available())
            reason = detector.get_warning() if not available else ""
        except Exception as exc:  # noqa: BLE001 - a probe must not break the list
            reason = f"{type(exc).__name__}: {exc}"
        entries.append(
            {
                "model_id": detector.model_id,
                "display_name": detector.display_name,
                "installed": True,
                "available": available,
                "blocked_reason": reason,
            }
        )
    return entries


def _pick_detector(model_id: str | None) -> Any:
    detectors = available_detectors()
    if not detectors:
        raise RpcError("DETECTOR_MISSING", "No watermark detector is installed", {})
    if model_id:
        for detector in detectors:
            if detector.model_id == model_id:
                return detector
        raise RpcError("DETECTOR_MISSING", f"Unknown detector: {model_id}", {"model_id": model_id})
    return detectors[0]


def _run_task(
    manager: TaskManager,
    task: TaskRecord,
    input_path: str,
    detector: Any,
    sample_step: int,
    max_tracks: int,
    min_confidence: float,
) -> None:
    from engine.pipeline.analyze_job import AnalysisError, run_analysis

    try:
        manager.update(task, phase="analyze", message="Analysing video")
        analysis = run_analysis(
            input_path,
            detector,
            sample_step=sample_step,
            max_tracks=max_tracks,
            min_confidence=min_confidence,
            progress=lambda payload: manager.update(task, **{
                key: value for key, value in payload.items() if key in {"phase", "progress", "message"}
            }),
            cancel=task.cancel,
        )
        if task.cancel.is_set():
            manager.finish(task, error="Cancelled")
            return
        manager.finish(task, result=analysis.as_dict())
    except AnalysisError as exc:
        manager.finish(task, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - task boundary
        logger.error("analysis task failed: %s", exc)
        manager.finish(task, error=str(exc))


def register(server: RpcServer, manager: TaskManager) -> None:
    def handle_capabilities(_params: dict[str, Any]) -> dict[str, Any]:
        detectors = _detector_entries()
        return {"detectors": detectors, "available": any(item["available"] for item in detectors)}

    def handle_start(params: dict[str, Any]) -> dict[str, Any]:
        input_path = str(params.get("input_path", ""))
        if not Path(input_path).is_file():
            raise RpcError("INVALID_PARAMS", f"File not found: {input_path}", {"path": input_path})
        detector = _pick_detector(None)
        task = manager.create("analyze")
        thread = threading.Thread(
            target=_run_task,
            args=(
                manager,
                task,
                input_path,
                detector,
                int(params.get("sample_step") or 15),
                int(params.get("max_tracks") or 8),
                float(params.get("min_confidence") or 0.35),
            ),
            name=f"analyze-{task.task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {"task_id": task.task_id}

    server.register("analyze.capabilities", handle_capabilities)
    server.register("analyze.start", handle_start)
```

在 `engine/methods/annotation.py` 的 `register()` 里加上编译入口（并在文件顶部 import 需要的类型）：

```python
    def handle_compile(params: dict[str, Any]) -> dict[str, Any]:
        from engine.pipeline.analysis import (
            BBox,
            Interval,
            Keyframe,
            WatermarkTrack,
            compile_tracks,
        )

        raw_tracks = params.get("watermarks")
        raw_shots = params.get("shots")
        video = params.get("video")
        if not isinstance(raw_tracks, list) or not isinstance(raw_shots, list):
            raise invalid_params("watermarks and shots must be arrays")
        if not isinstance(video, dict):
            raise invalid_params("video must be an object")

        tracks: list[WatermarkTrack] = []
        for raw in raw_tracks:
            data = raw if isinstance(raw, dict) else {}
            intervals = [
                Interval(int(item.get("start_frame", 0)), int(item.get("end_frame", 0)))
                for item in (data.get("intervals") or [])
                if isinstance(item, dict)
            ]
            keyframes = [
                Keyframe(int(item.get("frame", 0)), BBox.from_dict(item.get("rect")))
                for item in (data.get("keyframes") or [])
                if isinstance(item, dict)
            ]
            tracks.append(
                WatermarkTrack(
                    id=str(data.get("id") or uuid4().hex),
                    intervals=intervals,
                    keyframes=keyframes,
                    motion=str(data.get("motion") or "static"),
                    confidence=float(data.get("confidence") or 0.0),
                    source=str(data.get("source") or "manual"),
                    notes=[str(item) for item in (data.get("notes") or [])],
                )
            )

        shots = [
            Interval(int(item.get("start_frame", 0)), int(item.get("end_frame", 0)))
            for item in raw_shots
            if isinstance(item, dict)
        ]
        segments = compile_tracks(
            tracks,
            shots=shots,
            frame_width=int(video.get("width", 0) or 0),
            frame_height=int(video.get("height", 0) or 0),
        )
        return {"segments": segments}

    server.register("annotation.compile", handle_compile)
```

在 `engine/methods/__init__.py` 里接上：

```python
from engine.methods import (
    analyze,
    annotation,
    annotation_store,
    assets,
    media,
    model_download,
    preview,
    process,
    stubs,
    system,
    upscale,
)
```

```python
    manager: TaskManager = process.create_manager(server)
    media.register(server)
    preview.register(server)
    process.register(server, manager)
    upscale.register(server, manager)
    analyze.register(server, manager)
```

- [ ] **Step 6: 更新 `tests/test_contract.py` 的期望集合**

在 `test_live_surface_matches_the_delivered_engine` 的集合里加三行（保持字母序插入）：

```python
                "analyze.capabilities",
                "analyze.start",
                "annotation.compile",
```

- [ ] **Step 7: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m unittest tests.test_analyze_methods tests.test_contract -v`
Expected: 全部 PASS（`test_contract` 的三条新方法名都在 `IMPLEMENTED_METHODS` 里）

- [ ] **Step 8: 跑门禁 1 与 2**

Run: `bash scripts/check.sh`（前两道就是契约同步 + 引擎单测）
Expected: `ALL GATES PASSED`

- [ ] **Step 9: 提交**

```bash
git add contract/methods.json engine/generated/contract.py desktop/electron/generated/contract.ts \
        engine/pipeline/analyze_job.py engine/methods/analyze.py engine/methods/annotation.py \
        engine/methods/__init__.py tests/test_analyze_methods.py tests/test_contract.py
git commit -m "feat(engine): analyze.start/analyze.capabilities and annotation.compile over RPC"
```

---

### Task 9: CLI `analyze`

**Files:**
- Modify: `engine/cli.py`
- Test: `tests/test_cli_analyze.py`（新建，只测参数解析与错误路径，不跑真实推理）

**Interfaces:**
- Consumes: `engine.pipeline.analyze_job.run_analysis`、`engine.pipeline.detect.stub.load_detections`、`ScriptedDetector`
- Produces: `python -m engine.cli analyze <video> --detections <json> [--step N] [--json]`，成功退出码 0，失败 1，用法错误 2。

- [ ] **Step 1: 写失败测试**

```python
"""The CLI is the way to prove the analysis works without Electron."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from engine import cli


class ParserTests(unittest.TestCase):
    def test_analyze_subcommand_requires_a_path(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            cli.main(["analyze"])
        self.assertEqual(caught.exception.code, 2)

    def test_analyze_accepts_detection_script(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["analyze", "clip.mp4", "--detections", "d.json", "--json"])
        self.assertEqual(args.input, "clip.mp4")
        self.assertTrue(args.json)


class CommandTests(unittest.TestCase):
    def test_missing_file_exits_two(self) -> None:
        with redirect_stdout(io.StringIO()):
            code = cli.main(["analyze", "/nope/missing.mp4"])
        self.assertEqual(code, 2)

    def test_missing_detection_script_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"x")
            with redirect_stdout(io.StringIO()):
                code = cli.main(["analyze", str(video), "--detections", "/nope/d.json"])
        self.assertEqual(code, 2)

    def test_success_prints_json(self) -> None:
        payload = {"shots": [{"start_frame": 0, "end_frame": 9}], "watermarks": []}
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"x")
            script = Path(scratch) / "d.json"
            script.write_text(json.dumps({"frames": {}}), encoding="utf-8")
            with patch("engine.cli._analyze", return_value=payload):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(["analyze", str(video), "--detections", str(script), "--json"])
        self.assertEqual(code, 0)
        self.assertIn('"watermarks"', buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m unittest tests.test_cli_analyze -v`
Expected: FAIL —— `cli.build_parser` 不存在（当前 `main()` 内部直接构造 parser）

- [ ] **Step 3: 给 `engine/cli.py` 加 `analyze` 子命令**

`build_parser()` 和 `main()` **已经存在**（在 `engine/cli.py` 末尾），不要重写它们。
只在 `build_parser()` 里、`upscale` 子命令的 `set_defaults` 之后插入这段：

```python
    analyze = subparsers.add_parser("analyze", help="analyse a video's watermark situation")
    analyze.add_argument("input")
    analyze.add_argument("--detections", required=True, help="JSON detection script")
    analyze.add_argument("--step", type=int, default=15, help="sample every N frames")
    analyze.add_argument("--max-tracks", type=int, default=8)
    analyze.add_argument("--min-confidence", type=float, default=0.35)
    analyze.add_argument("--json", action="store_true", help="print the analysis as JSON")
    analyze.set_defaults(handler=command_analyze)
```

然后在 `command_upscale` 之后新增两个函数：

```python
def _analyze(args: argparse.Namespace) -> dict:
    from engine.pipeline.analyze_job import run_analysis
    from engine.pipeline.detect.stub import ScriptedDetector, load_detections

    detector = ScriptedDetector(load_detections(args.detections))
    analysis = run_analysis(
        args.input,
        detector,
        sample_step=args.step,
        max_tracks=args.max_tracks,
        min_confidence=args.min_confidence,
    )
    return analysis.as_dict()


def command_analyze(args: argparse.Namespace) -> int:
    import json

    from engine.pipeline.analyze_job import AnalysisError

    path = Path(args.input)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2
    if not Path(args.detections).is_file():
        print(f"no such detection script: {args.detections}", file=sys.stderr)
        return 2

    try:
        payload = _analyze(args)
    except AnalysisError as exc:
        print(f"analysis failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    video = payload["video"]
    print(f"video      {video['width']}x{video['height']} {video['fps']}fps "
          f"{video['frame_count']} frames")
    print(f"shots      {len(payload['shots'])}")
    for track in payload["watermarks"]:
        intervals = ", ".join(
            f"{item['start_frame']}-{item['end_frame']}" for item in track["intervals"]
        )
        print(f"watermark  {track['id']}  {track['motion']}  {intervals}  "
              f"confidence {track['confidence']:.2f}")
    if not payload["watermarks"]:
        print("watermark  (none detected)")
    advice = payload["suggested"]
    print(f"suggested  engine={advice['engine_id']} split_by_shot={advice['split_by_shot']}")
    if advice["warnings"]:
        print(f"warnings   {', '.join(advice['warnings'])}")
    return 0
```

最后把 `engine/entrypoint.py` 里的 `{"info", "probe", "process", "upscale"}` 改成
`{"info", "probe", "process", "upscale", "analyze"}`。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m unittest tests.test_cli_analyze -v`
Expected: 5 tests PASS

- [ ] **Step 5: 手工冒烟（用仓库里现成的夹具）**

```bash
.venv/Scripts/python.exe - <<'PY'
import json, pathlib
payload = {"frames": {"0": [{"x": 480, "y": 20, "width": 120, "height": 60, "confidence": 0.9}],
                      "15": [{"x": 480, "y": 20, "width": 120, "height": 60, "confidence": 0.9}]}}
pathlib.Path(".cache/cli-detections.json").write_text(json.dumps(payload), encoding="utf-8")
print("written")
PY
.venv/Scripts/python.exe -m engine.cli analyze .cache/fixture.mp4 --detections .cache/cli-detections.json
```

Expected: 打印 `shots` 与一条 `watermark wm-1 static 0-15 ...`（若 `.cache/fixture.mp4` 不存在，先跑 `node scripts/e2e-engine.mjs` 生成，或跳过本步并在结果里说明）

- [ ] **Step 6: 提交**

```bash
git add engine/cli.py engine/entrypoint.py tests/test_cli_analyze.py
git commit -m "feat(cli): analyze subcommand so the pipeline can be checked without Electron"
```

---

### Task 10: 收尾 —— 文档、门禁、里程碑记录

**Files:**
- Modify: `docs/09-model-and-automation-redesign.md`（§11 勾选已完成工作项，§13 状态更新）
- Modify: `CHANGELOG.md`（新增"未发布"小节）
- Create: `docs/10-analysis-baseline.md`（记录本里程碑实测：合成夹具上的 track 数、耗时）

**Interfaces:**
- Consumes: 前 9 个任务的全部产出
- Produces: 无代码；给下一个里程碑（真实检测器 + 界面）一份可对照的基线。

- [ ] **Step 1: 跑全量快速门禁**

Run: `bash scripts/check.sh`
Expected: `ALL GATES PASSED`（4 道：契约同步、174+ 单测、协议 e2e、外壳 typecheck+UI）

- [ ] **Step 2: 记录实测数字**

用 Task 9 的 CLI 在 `.cache/fixture.mp4` 上跑一次，把 `elapsed_ms`、track 数、shots 数写进 `docs/10-analysis-baseline.md`：

```markdown
# 10 · 分析管线基线（M1）

- 日期：2026-11-21
- 夹具：`.cache/fixture.mp4`（640×360，24fps，48 帧）
- 检测源：脚本化检测（`ERASER_DETECT_STUB` / `--detections`），非真实模型

| 指标 | 结果 |
|---|---|
| 采样帧数 | `<填>` |
| shots | `<填>` |
| tracks | `<填>` |
| 耗时 | `<填>` ms |

> 本基线只证明管线连通与算法确定，**不代表真实检测质量**；真实检测器的召回/误报
> 由 M2 的 `scripts/eval-analysis.py` 度量。
```

- [ ] **Step 3: 更新 spec 的状态**

在 `docs/09-model-and-automation-redesign.md` 的 §11 表格里，把 W1、W3（桩）、W4、W5、W6 标记为 `✅ M1`；§13 的"待确认"保留 Q1/Q2/Q3 原样。

- [ ] **Step 4: 更新 CHANGELOG**

在 `CHANGELOG.md` 顶部加：

```markdown
## 未发布 · M1 分析管线（引擎侧）

- 新增 `analyze.start` / `analyze.capabilities`：分析镜头切点、水印数量、出现区间、是否移动。
- 新增 `annotation.compile`：把水印轨道编译成现有管线消费的 `Segment[]`（含逐帧关键帧）。
- 新增 CLI `eraser-engine analyze`，不装界面也能看到分析结果。
- 检测器为可插拔接口；本里程碑只带脚本化检测器（`ERASER_DETECT_STUB`），真实视觉模型在 M2 接入。
```

- [ ] **Step 5: 提交**

```bash
git add docs/09-model-and-automation-redesign.md docs/10-analysis-baseline.md CHANGELOG.md
git commit -m "docs: record the M1 analysis baseline and mark the finished work items"
```

---

## 里程碑边界（本计划不做什么）

- **不接真实检测模型。** Florence-2 适配器、权重下载、运行时是 M2；M1 只有接口与脚本桩。
- **不做界面。** 分析报告、时间线、编辑栏是 M3；M1 用 CLI 证明能力。
- **不接质量档引擎。** ProPainter 运行时是 M4；M1 只在 `suggest_strategy` 里预留 `engine_id` 与告警。
- **不改 `watermark.py` 的掩码构造。** 逐帧掩码（spec §5.3）属于 M4，与质量档一起做。

## Self-Review

- **Spec coverage**：§4.1（Analysis 结构）→ Task 2；§4.2（四个问题）→ Task 5/6；§4.3（关联规则）→ Task 5；§4.4（采样）→ Task 3；§4.5（建议）→ Task 6；§5.1（track 模型）→ Task 2；§5.2（编译）→ Task 7；§7.1（契约）→ Task 8；§9.2（分析指标）→ 部分（M1 只有单元测试，真实指标在 M2）。**已识别缺口**：§6（引擎层）与 §8（界面）不在本计划内，已在"里程碑边界"说明。
- **Placeholder scan**：无 TBD / "略" / "similar to Task N"。所有代码步骤都给了可粘贴的实现；Task 9 只给增量片段，因为 `build_parser()` 与 `main()` 已存在于 `engine/cli.py`。
- **Type consistency**：`BBox`/`Detection`/`Interval`/`Keyframe`/`WatermarkTrack`/`Analysis` 在 Task 2 定义，Task 4–8 全部复用同一批名字；`compile_tracks` 的返回键 `start_frame`/`end_frame`/`rect`/`keyframes` 与契约里的 `Segment` 完全一致。
- **Review Focus 覆盖**：1 帧/极少帧 → Task 6 `test_zero_length_video`、Task 7 `test_empty_track_list_compiles_to_nothing`；空检测 → Task 6 `test_no_watermark_does_not_start_processing`；漏检不切碎 → Task 5 `test_single_missed_sample_does_not_split_the_interval`；两水印不合并 → Task 5 `test_two_simultaneous_watermarks_stay_separate`；ffmpeg 缺失 → Task 3 `skipUnless` + `FrameSamplingError`，Task 8 的 `test_missing_file_is_rejected`。

## 执行方式

本计划任务间有强接口依赖（Task 2 的类型贯穿 4–8），且总共 10 个任务、每一步都要求跑测试，**推荐 Native 执行**（我在本会话里按顺序做），最后对整条分支做一次审查。
