"""Benchmark ONNX Runtime execution providers against the LaMa model.

Run with the project venv:

    .venv/Scripts/python.exe scripts/bench-provider.py

Reports, for every provider this build can actually initialise, the wall time
of a single 512x512 inference after a warm-up.  Providers that fail to load
(missing CUDA DLL, unsupported op, ...) are reported as failures rather than
crashing the run, because that is exactly what the engine has to survive.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

DEFAULT_MODEL = ROOT / "models" / "big-lama" / "1.0.0" / "big-lama.onnx"


def preload() -> list[str]:
    """Best-effort preload of the CUDA/cuDNN DLLs shipped by the nvidia wheels."""
    if not hasattr(ort, "preload_dlls"):
        return ["onnxruntime has no preload_dlls()"]
    try:
        ort.preload_dlls()
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return [f"preload_dlls failed: {exc}"]
    return []


def make_input(size: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 256, (size, size, 3), dtype=np.uint8).astype(np.float32) / 255.0
    mask = np.zeros((size, size), dtype=np.float32)
    mask[size // 4 : size // 2, size // 4 : size // 2] = 1.0
    return image, mask


def to_nchw(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        arr = arr[None, None, :, :]
    else:
        arr = np.transpose(arr, (2, 0, 1))[None, ...]
    return np.ascontiguousarray(arr, dtype=np.float32)


def bench(provider: str, model: Path, size: int, runs: int, threads: int) -> dict:
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.intra_op_num_threads = threads if provider == "CPUExecutionProvider" else 1

    started = time.perf_counter()
    session = ort.InferenceSession(str(model), sess_options=opts, providers=[provider])
    init = time.perf_counter() - started

    inputs = session.get_inputs()
    names = [i.name for i in inputs]
    shapes = [i.shape for i in inputs]
    image, mask = make_input(size, seed=7)
    feeds = {}
    for name, shape in zip(names, shapes):
        rank = len(shape)
        feeds[name] = to_nchw(mask if rank == 4 and shape[1] == 1 else image)
    if len(names) == 1:
        feeds[names[0]] = np.concatenate(
            [to_nchw(image), to_nchw(mask)], axis=1
        )

    session.run(None, feeds)  # warm-up (also pays CUDA kernel autotuning)

    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        session.run(None, feeds)
        timings.append(time.perf_counter() - started)

    return {
        "provider": session.get_providers()[0],
        "init_s": init,
        "mean_s": sum(timings) / len(timings),
        "min_s": min(timings),
        "runs": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--cpu-threads", type=int, default=8)
    args = parser.parse_args()

    model = Path(args.model)
    if not model.is_file():
        print(f"model not found: {model}")
        return 2

    print(f"onnxruntime {ort.__version__}")
    print(f"available providers: {ort.get_available_providers()}")
    for note in preload():
        print(f"preload: {note}")
    print(f"model: {model} ({model.stat().st_size / 1e6:.1f} MB)")
    print()

    wants = []
    for provider in ort.get_available_providers():
        if provider == "CPUExecutionProvider":
            wants.append(provider)
        elif provider in {"CUDAExecutionProvider", "DmlExecutionProvider"}:
            wants.append(provider)

    failures = 0
    for provider in wants:
        label = provider
        if provider == "CPUExecutionProvider":
            label += f" (threads={args.cpu_threads})"
        try:
            result = bench(provider, model, args.size, args.runs, args.cpu_threads)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            failures += 1
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            print(f"{label:<40} FAILED: {first[:160]}")
            continue
        print(
            f"{label:<40} init={result['init_s']:6.2f}s "
            f"mean={result['mean_s'] * 1000:8.1f}ms min={result['min_s'] * 1000:8.1f}ms "
            f"active={result['provider']}"
        )

    return 1 if failures and failures == len(wants) else 0


if __name__ == "__main__":
    raise SystemExit(main())
