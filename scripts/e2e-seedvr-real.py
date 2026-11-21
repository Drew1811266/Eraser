#!/usr/bin/env python3
"""SeedVR2 with the real model, weights and all.

The other SeedVR2 test drives a stand-in CLI, which verifies everything except
the model. This one runs the actual model, so it needs ~2.5GB of weights and a
PyTorch runtime already installed — if they are not there it says so and exits 0,
because "not installed on this machine" is a legitimate state, not a failure.

    python scripts/e2e-seedvr-real.py [--seconds 30]

Install first if needed:

    python -c "from engine.pipeline.seedvr.runtime import install; \\
               install('seedvr2_3b_q4_k_m_gguf', progress=print)"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffmpeg.exe"
FFPROBE = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffprobe.exe"
WORK = ROOT / ".cache" / "seedvr-real"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}" + (f" ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" ({detail})" if detail else ""))


def probe(path: Path) -> dict:
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "stream=codec_type,width,height,nb_frames",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    streams = json.loads(result.stdout or "{}").get("streams", [])
    video = next((entry for entry in streams if entry.get("codec_type") == "video"), {})
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "frames": int(video.get("nb_frames") or 0),
        "has_audio": any(entry.get("codec_type") == "audio" for entry in streams),
    }


def make_input(destination: Path) -> None:
    """Something with detail but small enough that a CPU run would also finish."""
    subprocess.run(
        [str(FFMPEG), "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=8:duration=0.5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(destination)],
        check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=120, help="fail if a run takes longer")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from engine.pipeline.seedvr import capability, manifest
    from engine.pipeline.seedvr.runtime import SeedVRRuntime
    from engine.pipeline.upscale_plan import UpscaleConfig
    from engine.methods.upscale import _run_seedvr_task
    from engine.tasks import TaskManager

    model_id = manifest.DEFAULT_MODEL_ID
    availability = capability.check(model_id)
    if not availability.available:
        print(f"SeedVR2 is not installed here ({availability.reason}: {availability.detail})")
        print("install it with: python -c \"from engine.pipeline.seedvr.runtime import install; "
              "install('seedvr2_3b_q4_k_m_gguf', progress=print)\"")
        return 0

    WORK.mkdir(parents=True, exist_ok=True)
    source = WORK / "input.mp4"
    make_input(source)
    source_info = probe(source)
    print(f"input: {source_info['width']}x{source_info['height']}, {source_info['frames']} frames, "
          f"audio={source_info['has_audio']}")

    print("\n1. the runtime speaks to the real CLI")
    runtime = SeedVRRuntime()
    direct = WORK / "direct"
    direct.mkdir(parents=True, exist_ok=True)
    updates: list[float] = []
    started = time.time()
    produced = runtime.upscale(source, direct, model_id=model_id, resolution=256,
                               progress=lambda payload: updates.append(float(payload.get("progress", 0))))
    elapsed = time.time() - started
    check("the model produced a file", produced.is_file(), produced.name)
    check("it is a real video", probe(produced)["frames"] > 0, f"{probe(produced)['width']}x{probe(produced)['height']}")
    check("progress reached the model's own reporter", len(updates) > 0, f"{len(updates)} updates")
    check("the run finished inside the budget", elapsed < args.seconds, f"{elapsed:.0f}s")

    print("\n2. the engine task produces what the plan promises")
    output_dir = WORK / "task"
    output_dir.mkdir(parents=True, exist_ok=True)
    manager = TaskManager(lambda _event, _payload: None)
    task = manager.create("upscale")
    config = UpscaleConfig.from_params({
        "mode": "upscale_resolution", "target_preset": "1080p", "keep_audio": True,
    })
    started = time.time()
    _run_seedvr_task(manager, task, str(source), str(output_dir), config, model_id)
    snapshot = manager.snapshot(task)
    elapsed = time.time() - started

    check("the task succeeds", snapshot["state"] == "success", (snapshot.get("error") or "")[:80])
    result = snapshot.get("result") or {}
    output = Path(result.get("output_path") or "")
    check("the result points at a file", output.is_file(), output.name)
    if output.is_file():
        info = probe(output)
        check("the output is 1080p as planned",
              (info["width"], info["height"]) == (result.get("width"), result.get("height")),
              f"{info['width']}x{info['height']}")
        check("every frame survived", info["frames"] == source_info["frames"],
              f"{info['frames']} vs {source_info['frames']}")
        check("the audio track came along", info["has_audio"] == source_info["has_audio"],
              f"audio={info['has_audio']}")
    check("the result names the engine", result.get("engine") == "seedvr2", str(result.get("engine")))
    check("the run finished inside the budget", elapsed < args.seconds, f"{elapsed:.0f}s")

    print()
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
