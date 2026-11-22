# M2 · 真实检测器实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把 M1 的脚本桩换成真实视觉模型（Florence-2），按需安装到一个隔离运行时里，并用一套带真值的合成素材量出召回/误报，回答"这套分析到底能不能用"。

**Architecture:** 检测器仍然实现 M1 的 `WatermarkDetector` 接口，引擎进程里不出现 torch。真正跑模型的代码是一段独立 worker 脚本，由 **模型目录里的专属 venv** 执行：引擎把采样帧写成 JPEG、把作业写成 JSON，worker 逐帧推理并回吐 NDJSON。安装（venv + 固定版本依赖 + 444MB 权重）走 `model.install` 任务，与去水印模型下载一样有进度、可取消。

**Tech Stack:** Python 3.13（引擎与运行时同版本）、transformers **4.46.3（必须 <5，见 §1 发现）**、torch（有 CUDA 装 CUDA 版，否则 CPU）、timm、einops、Pillow、huggingface 权重（`microsoft/Florence-2-base`，MIT，版本 `5ca5edf5bd017b9919c05d08aebef5e4c7ac3bac`）。

**Spec:** [docs/09-model-and-automation-redesign.md](09-model-and-automation-redesign.md) §4.1、§6、§7.2、§11 W3

---

## 1. 已完成的可行性验证（2026-11-22）

在 `.cache/spike-venv`（独立环境，非引擎 venv）上实测，素材为 ffmpeg 合成的 1280×720 渐变图：

| 素材 | 检测结果 | 面积占比 | 单帧耗时（CPU） |
|---|---|---|---|
| 右下角文字 `© StudioName 2026` | `[1009, 660, 1265, 709]` | 1.1% | 0.98s |
| 中心 `DEMO`（96px） | `[538, 318, 740, 404]` | 1.9% | 1.01s |
| **无水印对照** | `[0.6, 0.3, 1278, 719]`（整幅） | **99.6%** | 1.17s |

结论（这些直接决定实现）：

1. **两种提示等效**：`<OPEN_VOCABULARY_DETECTION>watermark` 与 `<CAPTION_TO_PHRASE_GROUNDING>a watermark in the corner` 的框几乎相同；选用前者（自由短语、语义正确）。
2. **模型永远返回一个框**：无水印时返回整幅画面。M1 的 `max_area_ratio=0.25` 过滤正好拦掉它——**这个过滤不是可选项，是正确性的前提**。
3. **没有置信度**：Florence-2 不输出分数，`Detection.confidence` 一律 1.0，`min_confidence` 过滤对它是惰性的；判据只能是几何 + 时序。
4. **版本必须钉死**：transformers 5.24 会让 Florence-2 的 remote code 崩在 `forced_bos_token_id`；4.46.3 正常。
5. **CPU 可用但慢**：单帧 ~1.0s，60 个采样点约 60s；GPU 预计 0.05–0.15s/帧。CPU 作为降级路径，必须在 `analyze.capabilities` 里如实标明。

## 2. 权重的固定清单（写进代码，不用 hub 客户端）

版本 `5ca5edf5bd017b9919c05d08aebef5e4c7ac3bac`，共 9 个文件 444.3 MB：

```
config.json                    2,430      c666d0fe…3aa8f
configuration_florence2.py    15,119      de2e45a9…93bdf
model.safetensors        463,221,266     03075d2d…ee060
modeling_florence2.py        127,455      5162bf46…b8a24
preprocessor_config.json         806      2f5921bb…cf29
processing_florence2.py       48,676      f146023a…68e98
tokenizer.json             1,355,863      847bbeab…df70e
tokenizer_config.json             34      79ffcf43…70489c
vocab.json                 1,099,884      394fdc63…17b3c
```

下载地址：`https://huggingface.co/microsoft/Florence-2-base/resolve/<revision>/<file>`。逐个校验 sha256，失败即报错并删除半成品。

## Global Constraints

- **引擎 venv 里不得出现 torch 的新依赖**：worker 只在模型目录的专属 venv 里跑。`engine/pipeline/detect/florence.py` 不得 `import torch/transformers`。
- 权重与 venv 一律不进仓库、不进安装包：落在 `paths.models_dir()/florence2/`（`venv/`、`weights/`、`state.json`）。
- 依赖版本全部钉死（`transformers==4.46.3`、`timm==1.0.31`、`einops`、`pillow`）；torch 走与 SeedVR2 相同的 CUDA 判定。
- 单元测试**不得联网**、不得真跑模型：worker 用假脚本验证协议，运行时用临时目录 + `file://` URL 验证下载。
- stdout 只承载协议帧；worker 的进度走 NDJSON（它是子进程，不是引擎）。

## Review Focus

- **无水印视频**：整幅框必须被面积过滤掉；分析结果应是 `watermarks: []` + `no_watermark` 建议，而不是 1 个覆盖全屏的水印。
- **worker 崩溃 / 超时**：引擎必须拿到结构化错误（任务 failed + 原因），不能挂住或返回空分析。
- **权重缺失但状态说 ready**：`analyze.capabilities` 要报 `available: false` 且给出原因，`analyze.start` 拒绝并报 `DETECTOR_MISSING`。
- **批处理内存**：一次判定 240 帧，worker 必须分批（默认 8）并且每帧结果立即回吐，不能攒到最后。
- **坐标空间**：worker 看到的 JPEG 是下采样后的帧；引擎侧的 `rescale_detections` 必须仍然生效（M1 的 1600 宽用例要改成走 `detect_many` 后依旧通过）。
- **CPU 降级**：没有 CUDA 时必须仍然可用，并在 capabilities 里说明"会更慢"。

---

### Task 1: 批量检测接口

**Files:**
- Modify: `engine/pipeline/detect/base.py`
- Modify: `engine/pipeline/analyze_job.py`
- Test: `tests/test_detectors.py`

**Interfaces:**
- Produces: `WatermarkDetector.detect_many(frames: list[tuple[int, np.ndarray]]) -> dict[int, list[Detection]]`（默认实现逐帧调用 `detect`）
- 消费方：`run_analysis` 改为调用一次 `detect_many(frames)`，再逐帧 `rescale_detections`

- [ ] **Step 1: 写失败测试**

```python
class BatchTests(unittest.TestCase):
    def test_default_batch_calls_detect_per_frame(self) -> None:
        class Counter(ScriptedDetector):
            calls: list[int] = []

            def detect(self, frame, frame_index):  # noqa: ANN001
                Counter.calls.append(frame_index)
                return super().detect(frame, frame_index)

        detector = Counter({0: [Detection(0, BBox(1, 1, 2, 2), 1.0)]})
        result = detector.detect_many([(0, None), (5, None)])
        self.assertEqual(Counter.calls, [0, 5])
        self.assertEqual(sorted(result), [0, 5])
        self.assertEqual(result[0][0].rect, BBox(1, 1, 2, 2))

    def test_batch_of_no_frames_is_empty(self) -> None:
        self.assertEqual(ScriptedDetector({}).detect_many([]), {})
```

- [ ] **Step 2: 运行确认失败**（`AttributeError: detect_many`）→ `.venv/Scripts/python.exe -m unittest tests.test_detectors -v`
- [ ] **Step 3: 实现**

`base.py` 增加：

```python
    def detect_many(
        self, frames: list[tuple[int, np.ndarray]]
    ) -> dict[int, list[Detection]]:
        """Detect a batch. Override when the model wants the whole batch at once."""
        return {index: self.detect(image, index) for index, image in frames}
```

`run_analysis` 的检测循环改为：

```python
    detector.load()
    raw = detector.detect_many(frames)
    detections: dict[int, list[Detection]] = {}
    total = max(1, len(frames))
    for position, (frame_index, image) in enumerate(frames, start=1):
        height, width = image.shape[:2]
        scale_x = info.width / width if width else 1.0
        scale_y = info.height / height if height else 1.0
        detections[frame_index] = rescale_detections(
            raw.get(frame_index, []), scale_x, scale_y
        )
        if position == total or position % 20 == 0:
            emit(phase="detect", progress=0.2 + 0.6 * position / total,
                 message=f"Detecting {position}/{total}")
```

- [ ] **Step 4: 运行确认通过**，并跑 `tests.test_analyze_methods`（1600 宽集成用例必须仍然绿）
- [ ] **Step 5: 提交** `feat(detect): batch detection interface for runtime-backed models`

---

### Task 2: worker 协议（不依赖 torch 的部分）

**Files:**
- Create: `engine/pipeline/detect/worker_protocol.py`
- Create: `engine/pipeline/detect/florence_worker.py`（**在运行时 venv 里执行**，不得被引擎 import）
- Test: `tests/test_detect_worker_protocol.py`

**Interfaces:**
- Produces:
  - `encode_job(weights_dir, images: list[tuple[int, str]], *, prompt="watermark", task="<OPEN_VOCABULARY_DETECTION>", batch=8) -> dict`
  - `parse_line(line: str) -> dict`（容忍空行/坏行，坏行→`{"type":"error"}`）
  - `collect_frames(lines, total) -> dict[int, list[BBox]]`
- Worker CLI：`python florence_worker.py --job job.json`，向 stdout 逐行输出：
  `{"type":"progress","done":k,"total":n}` / `{"type":"frame","frame":i,"boxes":[[x1,y1,x2,y2]]}` / `{"type":"error","message":str}` / `{"type":"done"}`

- [ ] **Step 1: 写失败测试**（协议编解码；**不需要 torch**）

```python
class JobTests(unittest.TestCase):
    def test_job_contains_weights_and_images(self) -> None:
        job = encode_job("C:/w", [(0, "a.jpg"), (15, "b.jpg")], batch=4)
        self.assertEqual(job["weights"], "C:/w")
        self.assertEqual(job["prompt"], "watermark")
        self.assertEqual([item["frame"] for item in job["images"]], [0, 15])
        self.assertEqual(job["batch"], 4)


class ParseTests(unittest.TestCase):
    def test_reads_a_frame_line(self) -> None:
        frame = parse_line('{"type":"frame","frame":3,"boxes":[[1,2,3,4]]}')
        self.assertEqual(frame["type"], "frame")
        self.assertEqual(frame["boxes"], [[1, 2, 3, 4]])

    def test_blank_and_broken_lines_are_reported_not_raised(self) -> None:
        self.assertEqual(parse_line("")["type"], "error")
        self.assertEqual(parse_line("{nope")["type"], "error")

    def test_collects_boxes_by_frame(self) -> None:
        lines = [
            '{"type":"frame","frame":0,"boxes":[[1,2,3,4]]}',
            '{"type":"progress","done":1,"total":2}',
            '{"type":"frame","frame":15,"boxes":[]}',
        ]
        boxes = collect_frames(lines, total=2)
        self.assertEqual(boxes[0], [BBox(1, 2, 2, 2)])
        self.assertEqual(boxes[15], [])
```

- [ ] **Step 2: 运行确认失败** → `.venv/Scripts/python.exe -m unittest tests.test_detect_worker_protocol -v`
- [ ] **Step 3: 实现协议模块**

```python
"""Wire format between the engine and the Florence worker.

Kept torch-free and importable from the engine so the protocol is unit-testable
without a 444 MB model or a GPU.
"""
from __future__ import annotations
import json
from typing import Any
from engine.pipeline.analysis import BBox

DEFAULT_PROMPT = "watermark"
DEFAULT_TASK = "<OPEN_VOCABULARY_DETECTION>"
DEFAULT_BATCH = 8


def encode_job(weights_dir, images, *, prompt=DEFAULT_PROMPT, task=DEFAULT_TASK, batch=DEFAULT_BATCH) -> dict[str, Any]:
    return {
        "weights": str(weights_dir),
        "prompt": prompt,
        "task": task,
        "batch": int(batch),
        "images": [{"frame": int(frame), "path": str(path)} for frame, path in images],
    }


def parse_line(line: str) -> dict[str, Any]:
    text = (line or "").strip()
    if not text:
        return {"type": "error", "message": "empty line"}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"type": "error", "message": f"bad json: {exc}"}
    if not isinstance(payload, dict) or "type" not in payload:
        return {"type": "error", "message": "not a protocol object"}
    return payload


def _to_box(raw: Any) -> BBox | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return BBox(int(round(x1)), int(round(y1)), int(round(x2 - x1)), int(round(y2 - y1)))


def collect_frames(lines: list[str], *, total: int) -> dict[int, list[BBox]]:
    frames: dict[int, list[BBox]] = {}
    for line in lines:
        payload = parse_line(line)
        if payload.get("type") != "frame":
            continue
        frame = int(payload.get("frame", -1))
        boxes = [box for box in (_to_box(raw) for raw in payload.get("boxes") or []) if box]
        frames[frame] = boxes
    return frames
```

`florence_worker.py` 的核心（不在单测里执行）：

```python
def main(argv: list[str]) -> int:
    """Run inside the detector runtime: no engine imports beyond the protocol."""
    import torch
    from PIL import Image
    from transformers import AutoModelForCausalLM, AutoProcessor

    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    weights = job["weights"]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForCausalLM.from_pretrained(
        weights, trust_remote_code=True,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
    ).to(device).eval()
    processor = AutoProcessor.from_pretrained(weights, trust_remote_code=True)

    images = job["images"]
    for start in range(0, len(images), job["batch"]):
        chunk = images[start:start + job["batch"]]
        for item in chunk:
            image = Image.open(item["path"]).convert("RGB")
            inputs = processor(text=job["task"] + job["prompt"], images=image, return_tensors="pt")
            inputs = {k: v.to(device) for k, v in inputs.items()}
            with torch.inference_mode():
                generated = model.generate(**inputs, max_new_tokens=256, num_beams=1, do_sample=False)
            text = processor.batch_decode(generated, skip_special_tokens=False)[0]
            parsed = processor.post_process_generation(text, task=job["task"], image_size=image.size)
            boxes = (parsed.get(job["task"]) or {}).get("bboxes") or []
            print(json.dumps({"type": "frame", "frame": item["frame"], "boxes": boxes}), flush=True)
        print(json.dumps({"type": "progress", "done": min(start + len(chunk), len(images)),
                          "total": len(images)}), flush=True)
    print(json.dumps({"type": "done"}), flush=True)
    return 0
```

> `num_beams=1`（spike 用的 3）是为了速度：检测任务上 1 beam 的框基本不变，用评测脚本验证后再决定是否调高。

- [ ] **Step 4: 运行确认通过**（3 个测试）
- [ ] **Step 5: 提交** `feat(detect): torch-free protocol for the Florence worker`

---

### Task 3: Florence 检测器适配器

**Files:**
- Create: `engine/pipeline/detect/florence.py`
- Modify: `engine/pipeline/detect/__init__.py`
- Test: `tests/test_detect_florence.py`

**Interfaces:**
- Consumes: Task 1 的 `detect_many`、Task 2 的协议、Task 4 的 `runtime`（可用性/路径）
- Produces: `FlorenceDetector(weights_dir=None, python=None, timeout=900)`，`model_id="florence2"`，`display_name="Florence-2 (watermark)"`

- [ ] **Step 1: 写失败测试**（用假 worker，不联网、不加载模型）

```python
FAKE_WORKER = """
import json, sys
job = json.loads(open(sys.argv[2], encoding="utf-8").read())
for item in job["images"]:
    print(json.dumps({"type": "frame", "frame": item["frame"],
                      "boxes": [[10, 20, 40, 60]]}), flush=True)
print(json.dumps({"type": "done"}), flush=True)
"""

class FlorenceDetectorTests(unittest.TestCase):
    def _detector(self, script: str, tmp: str) -> FlorenceDetector:
        worker = Path(tmp) / "fake_worker.py"
        worker.write_text(script, encoding="utf-8")
        return FlorenceDetector(python=str(Path(sys.executable)), worker=worker)

    def test_boxes_come_back_in_sample_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            detector = self._detector(FAKE_WORKER, tmp)
            frames = [(0, np.zeros((40, 80, 3), dtype=np.uint8)),
                      (15, np.zeros((40, 80, 3), dtype=np.uint8))]
            found = detector.detect_many(frames)
        self.assertEqual(sorted(found), [0, 15])
        self.assertEqual(found[0][0].rect, BBox(10, 20, 30, 40))

    def test_worker_error_becomes_a_detector_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            detector = self._detector("import sys; print('boom'); sys.exit(3)", tmp)
            with self.assertRaises(DetectorError):
                detector.detect_many([(0, np.zeros((10, 10, 3), dtype=np.uint8))])

    def test_missing_weights_means_not_available(self) -> None:
        detector = FlorenceDetector(weights_dir="/nope")
        self.assertFalse(detector.is_available())
        self.assertIn("weights", detector.get_warning())
```

- [ ] **Step 2: 运行确认失败** → `.venv/Scripts/python.exe -m unittest tests.test_detect_florence -v`
- [ ] **Step 3: 实现**

要点：
1. `detect_many` 把每帧 `cv2.imencode(".jpg")` 写进 `temp_dir()` 下的临时目录，构造 job，`subprocess.run([python, worker, "--job", job_path], capture_output=True, timeout=...)`。
2. 解析 stdout 的 NDJSON（`collect_frames`），按帧返回 `Detection(frame=index, rect=box, confidence=1.0)`。**置信度恒为 1.0 是已知事实**，注释说明。
3. 非零退出、`{"type":"error"}`、超时 → `DetectorError`，消息里带 stderr 尾部。
4. `is_available()` = `python` 可执行 + `weights_dir/model.safetensors` 存在；`get_warning()` 在 CPU 时给出"会更慢"的提示（`runtime.cuda_available()` 读状态文件）。
5. `detect_many([])` 直接返回 `{}`，不启动子进程。
6. 临时目录用 `try/finally` 清理。

- [ ] **Step 4: 运行确认通过**（3 个测试）
- [ ] **Step 5: 提交** `feat(detect): Florence-2 adapter that talks to an isolated worker`

---

### Task 4: 运行时安装器

**Files:**
- Create: `engine/pipeline/detect/runtime.py`
- Create: `engine/pipeline/detect/weights.py`（固定清单 + 下载校验）
- Test: `tests/test_detect_runtime.py`

**Interfaces:**
- Produces:
  - `weights.FILES: list[WeightFile]`（§2 的 9 项）
  - `weights.download_all(destination, progress=None, cancel=None) -> None`
  - `runtime.state() -> dict`（`phase` / `python` / `cuda` / `error` / `model_id`）
  - `runtime.is_ready() -> bool`、`runtime.worker_python() -> Path`、`runtime.weights_dir() -> Path`
  - `runtime.install(progress=None, cancel=None) -> dict`
- 遵循 SeedVR2 的既有做法：状态文件 + 安装锁 + `_kill_tree` + 进度回调；**不复制**其代码，把可复用的 `_run`/`_download`/`_kill_tree` 提升到 `engine/pipeline/detect/runtime.py` 里独立实现（SeedVR2 保持不动，避免回归）。

- [ ] **Step 1: 写失败测试**（不联网：用 `file://` URL 和临时目录）

```python
class WeightDownloadTests(unittest.TestCase):
    def test_verifies_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.bin"
            source.write_bytes(b"payload")
            dest = Path(tmp) / "dest"
            digest = hashlib.sha256(b"payload").hexdigest()
            download_weight(source.as_uri(), dest / "src.bin", expected_sha256=digest, expected_bytes=7)
            self.assertEqual((dest / "src.bin").read_bytes(), b"payload")

    def test_rejects_a_bad_hash_and_leaves_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.bin"
            source.write_bytes(b"payload")
            with self.assertRaises(DetectorInstallError):
                download_weight(source.as_uri(), Path(tmp) / "x.bin", expected_sha256="0" * 64, expected_bytes=7)
            self.assertFalse((Path(tmp) / "x.bin").exists())

    def test_manifest_is_pinned(self) -> None:
        self.assertEqual(len(weights.FILES), 9)
        for item in weights.FILES:
            self.assertEqual(len(item.sha256), 64)
            self.assertGreater(item.bytes, 0)


class RuntimeStateTests(unittest.TestCase):
    def test_missing_state_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ERASER_DETECTOR_HOME": tmp}):
            self.assertFalse(runtime.is_ready())

    def test_ready_state_requires_the_weights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ERASER_DETECTOR_HOME": tmp}):
            runtime.write_state({"phase": "ready", "python": str(Path(tmp) / "venv" / "python.exe")})
            self.assertFalse(runtime.is_ready(), "a state file alone must not count as installed")
```

- [ ] **Step 2: 运行确认失败** → `.venv/Scripts/python.exe -m unittest tests.test_detect_runtime -v`
- [ ] **Step 3: 实现**

`weights.py`：`WeightFile(name, bytes, sha256)` 列表 + `download_weight(url, dest, *, expected_sha256, expected_bytes, timeout=600)`（`.part` 文件 + 原子改名 + 校验失败删除）。

`runtime.py`：
1. `detector_home()`：`ERASER_DETECTOR_HOME` 覆盖，否则 `paths.models_dir()/"florence2"`。
2. `install()`：状态写 `installing` → 建 venv（`find_python(3, 13)` 找不到就用 `sys.executable`）→ 装 `torch`（CUDA 判定同 SeedVR2）→ `transformers==4.46.3 timm==1.0.31 einops pillow` → 下载权重 → 写 `ready` + `cuda` 标记；任一步失败写 `failed` + 原因并抛 `DetectorInstallError`。
3. 安装锁：复用 `engine/pipeline/seedvr/runtime.py` 的 `_InstallLock` 思路（pid 文件 + 存活检查）；这里实现一份同语义的 `InstallLock`。
4. `is_ready()`：state.phase == "ready" **且** `weights/model.safetensors` 存在 **且** `worker_python()` 存在。

- [ ] **Step 4: 运行确认通过**
- [ ] **Step 5: 提交** `feat(detect): on-demand runtime installer for the Florence detector`

---

### Task 5: 契约与 RPC

**Files:**
- Modify: `contract/methods.json`（`model.install`、`model.installStatus` 改 `implemented: true`；`DetectorEntry` 增 `engine`）
- Modify: `engine/methods/model_download.py` 或新建 `engine/methods/model_install.py`
- Modify: `engine/pipeline/detect/__init__.py`（`available_detectors` 返回 Florence）
- Modify: `tests/test_contract.py`
- Test: `tests/test_model_install.py`

**Interfaces:**
- `model.install {model_id, force?}` → `{task_id}`；任务结果 = 运行时的 state
- `model.installStatus {task_id}` → `TaskStatus`
- `analyze.capabilities` 的 `detectors[]` 带 `engine: "florence2"`、`installed`、`available`、`blocked_reason`

- [ ] **Step 1: 写失败测试**

```python
class ModelInstallTests(unittest.TestCase):
    def test_install_is_rejected_for_an_unknown_model(self) -> None:
        frame = _Harness().call("model.install", {"model_id": "nope"})
        self.assertEqual(frame["error"]["data"]["code"], "INVALID_PARAMS")

    def test_install_returns_a_task(self) -> None:
        with patch("engine.methods.model_install.runtime") as fake:
            fake.is_ready.return_value = False
            frame = _Harness().call("model.install", {"model_id": "florence2"})
        self.assertIn("task_id", frame["result"])

    def test_capabilities_report_the_detector_and_why_it_is_blocked(self) -> None:
        with patch.dict(os.environ, {"ERASER_DETECT_STUB": ""}), \
             patch("engine.pipeline.detect.florence_detector_available", return_value=(False, "runtime not installed")):
            result = _Harness().call("analyze.capabilities")["result"]
        entry = next(item for item in result["detectors"] if item["model_id"] == "florence2")
        self.assertFalse(entry["available"])
        self.assertIn("runtime not installed", entry["blocked_reason"])
```

- [ ] **Step 2: 运行确认失败**
- [ ] **Step 3: 实现** handler、注册、契约改动、`python contract/codegen/generate.py`、更新 `test_contract.py` 的已实现集合
- [ ] **Step 4: 跑 `bash scripts/check.sh` 全绿**
- [ ] **Step 5: 提交** `feat(engine): install and advertise the Florence detector`

---

### Task 6: 真机验证（手动，不进单测）

- [ ] `model.install {"model_id":"florence2"}` 跑通（会下载 444MB 权重 + torch）
- [ ] 用 `.cache/fixture.mp4`（或一条带水印的真实短片）跑 `analyze.start`，确认：角落文字被找到、`motion` 判定合理、耗时记录
- [ ] 用一条**无水印**视频跑一次，确认 `watermarks: []` + `no_watermark`（验证 99.6% 整幅框确实被过滤）
- [ ] 把实测数字写进 `docs/13-quality-baseline.md`

---

### Task 7: 评测脚本与 7 组素材

**Files:**
- Create: `scripts/make-analysis-fixtures.py`（ffmpeg 合成 7 组素材 + 真值 JSON）
- Create: `scripts/eval-analysis.py`
- Create: `golden/analysis/<case>.json`（首轮结果，作为回归基线）

**7 组素材（每组同时给出真值 rect 与帧区间）：**

| 名称 | 内容 | 期望 |
|---|---|---|
| `corner_static` | 右下角文字，全程 | 1 track，static，1 区间 |
| `center_logo` | 中心大字，全程 | 1 track，static |
| `small_mark` | 24px 小字 | 1 track（检验小目标） |
| `moving_mark` | 每帧右移 4px | 1 track，moving |
| `blink` | 出现 2s → 消失 3s → 再现 | 1 track，2 区间 |
| `multi_shot` | 3 个镜头，同一角标 | 1 track，3 区间，3 个 segment |
| `clean` | 无水印 | 0 track，`no_watermark` |

评测指标（`eval-analysis.py` 输出）：

```
case            recall  precision  tracks(gt)  tracks  interval_err
corner_static    1.00      1.00        1          1        0
...
```

- `recall`：真值覆盖的采样帧里，被 IoU≥0.5 的检测命中的比例
- `precision`：检测框中与真值 IoU≥0.5 的比例（整幅误报会被算作误检）
- `tracks`：与真值水印数量的差
- `interval_err`：区间端点误差（秒）

- [ ] **Step 1: 写 fixture 生成器并生成 7 组（`--check` 模式只校验不重生成）**
- [ ] **Step 2: 写 `eval-analysis.py`，先对脚本桩跑（应得满分，证明指标本身正确）**
- [ ] **Step 3: 对真实 Florence 跑，记录首轮结果到 `golden/analysis/`**
- [ ] **Step 4: 提交** `feat(eval): measure detector recall and false positives on 7 synthetic cases`

---

### Task 8: 文档收口

- [ ] `docs/13-quality-baseline.md`：真实检测器的召回/误报/耗时，CPU 与 GPU 各一列
- [ ] `docs/09` §11：W3 改 ✅；§13 Q1 记录"M2 已完成"
- [ ] `CHANGELOG.md`：M2 小节
- [ ] `bash scripts/check.sh` 全绿；提交

---

## 已知风险

| # | 风险 | 处置 |
|---|---|---|
| R1 | 无水印素材返回整幅框，若面积过滤被放宽就会变成假水印 | 过滤阈值 25% 写进测试；评测集必须有 `clean` 用例 |
| R2 | GPU 不可用时 CPU 每帧 ~1s，用户以为卡死 | capabilities 明示 + 进度条按帧推进 + 文档给出耗时预期 |
| R3 | transformers 升级破坏 Florence-2 remote code | 钉死 4.46.3，安装后写入状态文件，`pip check` 失败即报错 |
| R4 | 权重下载被墙/中断 | `.part` + sha256 + 可重试；失败状态带原因，不写 ready |
| R5 | 文字之外的图形水印（半透明 logo 图）召回未知 | 评测集先只覆盖文字类；图形类在 M2 后按真实素材补充 |
| R6 | 批处理把 240 帧一次塞给 worker 导致显存峰值 | 默认 batch=8，结果每帧回吐 |

## Self-Review

- **Spec coverage**：§4.1 检测层 → Task 2/3；§6 运行时与权重 → Task 4；§7.2 `analyze.capabilities` → Task 5；§9.2 分析指标 → Task 7；§11 W3 → 全部。
- **Placeholder scan**：无 TBD；所有接口给了签名，worker 给了可直接落地的实现骨架。
- **Type consistency**：`Detection`/`BBox` 复用 M1；worker 的 `boxes` 是 `[[x1,y1,x2,y2]]`，`collect_frames` 转成 `BBox(x, y, w, h)`，与 `rescale_detections` 的输入一致。
- **Review Focus 覆盖**：无水印 → Task 3 `test_missing_weights` + Task 7 `clean`；worker 崩溃 → Task 3 `test_worker_error`；权重缺失 → Task 4 `test_ready_state_requires_the_weights`；批内存 → 默认 batch=8 + 每帧回吐；坐标 → Task 1 Step 4 复用 1600 宽用例；CPU 降级 → Task 3 `get_warning()` + Task 8 文档。

## 执行方式

Native 执行（与 M1 相同）：任务间共享协议与运行时状态，不宜拆给多个 subagent。

---

## 执行后记（2026-11-22）

8 个任务全部执行完毕，7/7 评测通过。执行中发现并修正的与计划不符之处：

- **worker 的 HF 缓存**：`trust_remote_code` 会往 `~/.cache/huggingface` 写模型自带的
  Python，本机该目录不可写。改为 `HF_HOME=<模型目录>/hf` + 离线模式，推理不再触网。
- **fp16 输入类型**：processor 给的是 float32，模型是 fp16，首次推理报
  `Input type (float) and bias type (Half)`；worker 现在按模型 dtype 转换。
- **Windows 探活**：计划沿用了 SeedVR2 的 `os.kill(pid, 0)`，但 Windows 上这不是探活，
  是 `TerminateProcess`——第二次安装会杀掉第一次。改为 `OpenProcess/GetExitCodeProcess`。
- **真值并非手写坐标**：用「带水印帧 − 干净帧」差分得到；前提是两次渲染完全一致，
  所以背景必须是同一张静态图（动画 `gradients` 会让整幅画面都算成水印）。
- **切点阈值**：深色纯色之间的切点 scene score 只有 0.06，检不出来；夹具改用亮度差大的三镜。
