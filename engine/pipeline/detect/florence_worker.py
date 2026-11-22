"""Florence-2 watermark detection, executed inside the detector runtime.

This file is **not imported by the engine**: it needs torch and transformers,
which belong to the on-demand runtime, not to the shipped engine. The engine
starts it as a subprocess and reads NDJSON from stdout.

    python florence_worker.py --job job.json

Job (written by the engine, see worker_protocol.encode_job):
    {"weights": "...", "prompt": "watermark", "task": "<OPEN_VOCABULARY_DETECTION>",
     "batch": 8, "images": [{"frame": 0, "path": "00000.jpg"}, ...]}

Stdout, one JSON object per line:
    {"type": "frame", "frame": 0, "boxes": [[x1, y1, x2, y2]], "seconds": 0.12}
    {"type": "progress", "done": 8, "total": 60}
    {"type": "error", "message": "..."}   (then exit 1)
    {"type": "done"}

Boxes are in the pixel space of the JPEG that was read (the sampled frame), which
is what the engine's `rescale_detections` expects.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _fail(message: str, code: int = 1) -> int:
    _emit({"type": "error", "message": message})
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="florence-worker")
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)

    job_path = Path(args.job)
    if not job_path.is_file():
        return _fail(f"job file not found: {job_path}")
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(f"bad job file: {exc}")

    images: list[dict[str, Any]] = job.get("images") or []
    if not images:
        _emit({"type": "done"})
        return 0

    try:
        import torch
        from PIL import Image
        from transformers import AutoModelForCausalLM, AutoProcessor
    except Exception as exc:  # noqa: BLE001 - the runtime is misprovisioned
        return _fail(f"cannot import the detector runtime: {exc}")

    weights = str(job.get("weights") or "")
    task = str(job.get("task") or "<OPEN_VOCABULARY_DETECTION>")
    prompt = str(job.get("prompt") or "watermark")
    batch_size = max(1, int(job.get("batch") or 8))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    try:
        model = (
            AutoModelForCausalLM.from_pretrained(
                weights, trust_remote_code=True, torch_dtype=dtype
            )
            .to(device)
            .eval()
        )
        processor = AutoProcessor.from_pretrained(weights, trust_remote_code=True)
    except Exception as exc:  # noqa: BLE001 - weights are wrong or incomplete
        return _fail(f"cannot load Florence-2 from {weights}: {exc}")

    total = len(images)
    done = 0
    for start in range(0, total, batch_size):
        chunk = images[start : start + batch_size]
        try:
            pictures = [Image.open(item["path"]).convert("RGB") for item in chunk]
        except OSError as exc:
            return _fail(f"cannot read a sampled frame: {exc}")

        started = time.perf_counter()
        try:
            inputs = processor(
                text=[task + prompt] * len(pictures),
                images=pictures,
                return_tensors="pt",
            )
            # The processor hands back float32 pixel values; the model may be
            # loaded in half precision. Cast every float tensor to the model's
            # dtype or torch refuses the matmul.
            inputs = {
                key: (
                    value.to(device=device, dtype=dtype)
                    if value.is_floating_point()
                    else value.to(device)
                )
                for key, value in inputs.items()
            }
            with torch.inference_mode():
                generated = model.generate(
                    **inputs, max_new_tokens=256, num_beams=1, do_sample=False
                )
            texts = processor.batch_decode(generated, skip_special_tokens=False)
        except Exception as exc:  # noqa: BLE001 - inference must report, not hang
            return _fail(f"inference failed: {type(exc).__name__}: {exc}")

        elapsed = time.perf_counter() - started
        per_image = elapsed / max(1, len(chunk))
        if len(texts) != len(chunk):
            # Truncating here would look exactly like "the last frames had no
            # watermark"; refuse instead of inventing an answer.
            return _fail(
                f"the model returned {len(texts)} outputs for {len(chunk)} frames"
            )
        for item, picture, text in zip(chunk, pictures, texts):
            try:
                parsed = processor.post_process_generation(
                    text, task=task, image_size=picture.size
                )
                boxes = (parsed.get(task) or {}).get("bboxes") or []
            except Exception as exc:  # noqa: BLE001 - a broken runtime must say so
                return _fail(
                    f"cannot parse the model output for frame {item.get('frame')}: "
                    f"{type(exc).__name__}: {exc}"
                )
            _emit(
                {
                    "type": "frame",
                    "frame": int(item["frame"]),
                    "boxes": boxes,
                    "seconds": round(per_image, 3),
                }
            )

        done += len(chunk)
        _emit({"type": "progress", "done": done, "total": total})

    _emit({"type": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
