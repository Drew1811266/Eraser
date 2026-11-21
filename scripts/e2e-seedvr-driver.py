#!/usr/bin/env python3
"""Exercise the SeedVR2 driver against a stand-in for the upstream CLI.

The real runtime needs PyTorch and a 3.6GB model. What can be verified without
them is everything *around* the model:

    command construction -> process handling -> progress parsing ->
    cancellation -> output discovery -> the final resize and audio mux

So the script plants a stub `inference_cli.py` in a throwaway runtime root — one
that prints the same `PROGRESS chunk=i/n` lines upstream does and writes a video
with ffmpeg — and drives the real `SeedVRRuntime` against it.

What this does not prove is stated plainly at the end: the model's own output.

    python scripts/e2e-seedvr-driver.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffmpeg.exe"
SOURCE = ROOT / ".cache" / "e2e-upscale" / "source.mp4"

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


STUB = '''"""Stand-in for the upstream inference CLI: same contract, no model."""
import argparse, shutil, subprocess, sys, time

parser = argparse.ArgumentParser()
parser.add_argument("--output")
parser.add_argument("--model_dir")
parser.add_argument("--dit_model")
parser.add_argument("--resolution", type=int, default=1080)
parser.add_argument("--batch_size")
parser.add_argument("--chunk_size")
parser.add_argument("--temporal_overlap")
parser.add_argument("--max_resolution")
parser.add_argument("--input_noise_scale")
parser.add_argument("--latent_noise_scale")
parser.add_argument("--video_backend")
parser.add_argument("--dit_offload_device")
parser.add_argument("--vae_offload_device")
parser.add_argument("--tensor_offload_device")
parser.add_argument("input")
args, _unknown = parser.parse_known_args()

print("PROGRESS stage=warmup step=vae_encode", flush=True)
time.sleep(0.2)
mode = __import__("os").environ.get("STUB_MODE", "ok")

for index in range(1, 6):
    time.sleep(0.15)
    print(f"PROGRESS chunk={index}/5", flush=True)

if mode == "fail":
    print("Traceback: simulated CUDA out of memory", flush=True)
    sys.exit(3)
if mode == "silent":
    sys.exit(0)

target = __import__("os").path.join(args.output, "stub_seedvr.mp4")
subprocess.run([STUB_FFMPEG, "-y", "-v", "error", "-i", args.input, "-c", "copy", target], check=True)
print("wrote", target, flush=True)
'''


def build_runtime(root: Path) -> None:
    """A runtime root that looks installed, with the stub in place of the CLI."""
    (root / "venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    (root / "venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    repo = root / "seedvr2_repo"
    repo.mkdir(parents=True, exist_ok=True)
    script = STUB.replace("STUB_FFMPEG", repr(str(FFMPEG)))
    (repo / "inference_cli.py").write_text(script, encoding="utf-8")
    (repo / "requirements.txt").write_text("torch\n", encoding="utf-8")
    models = root / "models" / "SEEDVR2"
    models.mkdir(parents=True, exist_ok=True)
    from engine.pipeline.seedvr import manifest

    (models / manifest.get_spec(manifest.DEFAULT_MODEL_ID).filename).write_bytes(b"weights")


def main() -> int:
    if not SOURCE.is_file():
        print(f"missing fixture {SOURCE}; run scripts/e2e-upscale.py once")
        return 2

    sys.path.insert(0, str(ROOT))
    from engine.pipeline.seedvr import manifest, runtime

    root = Path(tempfile.mkdtemp(prefix="seedvr-driver-"))
    build_runtime(root)

    with mock.patch.dict("os.environ", {"ERASER_SEEDVR_ROOT": str(root), "STUB_MODE": "ok"}), \
         mock.patch.object(manifest, "venv_python", return_value=Path(sys.executable)):
        driver = runtime.SeedVRRuntime()
        seen: list[float] = []
        output = Path(tempfile.mkdtemp(prefix="seedvr-out-"))

        print("1. a run that behaves")
        produced = driver.upscale(
            SOURCE, output,
            model_id=manifest.DEFAULT_MODEL_ID,
            resolution=1080,
            progress=lambda payload: seen.append(float(payload.get("progress", 0))),
        )
        check("the driver returns the produced file", produced.is_file(), produced.name)
        check("progress was reported", len(seen) >= 5, f"{len(seen)} updates")
        check("progress increases", all(b >= a for a, b in zip(seen, seen[1:])), f"{seen[:6]}")
        check("progress reaches the end", seen and seen[-1] >= 0.99, f"{seen[-1]:.2f}")

    print("\n2. a run that fails")
    with mock.patch.dict("os.environ", {"ERASER_SEEDVR_ROOT": str(root), "STUB_MODE": "fail"}), \
         mock.patch.object(manifest, "venv_python", return_value=Path(sys.executable)):
        try:
            runtime.SeedVRRuntime().upscale(SOURCE, Path(tempfile.mkdtemp()),
                                            model_id=manifest.DEFAULT_MODEL_ID)
            check("a failing CLI raises", False, "returned normally")
        except runtime.SeedVRError as exc:
            check("a failing CLI raises", True, str(exc).splitlines()[0][:60])
            check("the error carries the CLI's own words", "out of memory" in str(exc))

    print("\n3. a run that produces nothing")
    with mock.patch.dict("os.environ", {"ERASER_SEEDVR_ROOT": str(root), "STUB_MODE": "silent"}), \
         mock.patch.object(manifest, "venv_python", return_value=Path(sys.executable)):
        try:
            runtime.SeedVRRuntime().upscale(SOURCE, Path(tempfile.mkdtemp()),
                                            model_id=manifest.DEFAULT_MODEL_ID)
            check("a silent run is an error", False, "returned normally")
        except runtime.SeedVRError as exc:
            check("a silent run is an error", "no output" in str(exc), str(exc)[:50])

    print("\n4. cancelling mid-run")
    cancel = threading.Event()
    with mock.patch.dict("os.environ", {"ERASER_SEEDVR_ROOT": str(root), "STUB_MODE": "ok"}), \
         mock.patch.object(manifest, "venv_python", return_value=Path(sys.executable)):
        holder: dict[str, float] = {}

        def stop_soon() -> None:
            time.sleep(0.4)
            holder["stopped_at"] = time.monotonic()
            cancel.set()

        threading.Thread(target=stop_soon, daemon=True).start()
        started = time.monotonic()
        try:
            runtime.SeedVRRuntime().upscale(SOURCE, Path(tempfile.mkdtemp()),
                                           model_id=manifest.DEFAULT_MODEL_ID, cancel=cancel)
            check("cancelling raises", False, "returned normally")
        except runtime._Cancelled:
            check("cancelling raises", True, "")
            check("cancelling is prompt", time.monotonic() - started < 5.0,
                  f"{time.monotonic() - started:.1f}s")

    print("\n5. the engine turns a driver failure into a task error")
    sys.path.insert(0, str(ROOT))
    from engine.methods.upscale import _run_seedvr_task
    from engine.pipeline.upscale_plan import UpscaleConfig
    from engine.tasks import TaskManager

    events: list[dict] = []
    manager = TaskManager(lambda event, payload: events.append(payload))
    task = manager.create("upscale")

    with mock.patch.dict("os.environ", {"ERASER_SEEDVR_ROOT": str(root), "STUB_MODE": "ok"}), \
         mock.patch.object(manifest, "venv_python", return_value=Path(sys.executable)):
        out_dir = Path(tempfile.mkdtemp(prefix="seedvr-task-"))
        _run_seedvr_task(manager, task, str(SOURCE), str(out_dir),
                         UpscaleConfig.from_params({}), manifest.DEFAULT_MODEL_ID)

    snapshot = manager.snapshot(task)
    check("the task finishes", snapshot["state"] == "success", snapshot.get("error", "")[:60])
    result = snapshot.get("result") or {}
    check("the result names seedvr2", result.get("engine") == "seedvr2", str(result.get("engine")))
    check("the result points at a file", Path(result.get("output_path", "")).is_file(),
          Path(result.get("output_path", "")).name)
    check("the result kept the planned size",
          (result.get("width"), result.get("height")) == (1920, 1080),
          f"{result.get('width')}x{result.get('height')}")

    print()
    print(f"{passed} passed, {failed} failed")
    print("verified: command shape, progress parsing, cancellation, output discovery, task wiring")
    print("NOT verified here: the model's own output quality — that needs torch and the weights")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
