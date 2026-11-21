"""Numerical check for the Real-ESRGAN stage.

Three things can go wrong when a super-resolution model is wired in, and none of
them raise an exception:

1. the output is not actually bigger (scale/tiling maths wrong);
2. the content is wrong (channel order, normalisation, tile assembly) — the
   picture looks like static or a mosaic;
3. the model is not doing anything, and the "upscale" is a bicubic resize
   wearing a 67 MB hat.

So this script asserts geometry, content fidelity, and *added detail*: the model
output must be measurably sharper than a plain bicubic resize of the same frame.

    python scripts/e2e-upscale.py [--scale 4] [--frames 12]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FFMPEG = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffmpeg.exe"
FFPROBE = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffprobe.exe"
WORK = ROOT / ".cache" / "e2e-upscale"

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


def run(command: list[str], input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, check=False, input=input_bytes,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def probe_size(path: Path) -> tuple[int, int, int]:
    result = run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
                  "-show_entries", "stream=width,height,nb_frames", "-of", "csv=p=0", str(path)])
    parts = result.stdout.decode("utf-8", "replace").strip().split(",")
    return int(parts[0]), int(parts[1]), int(parts[2] or 0)


def read_frames(path: Path, width: int, height: int, limit: int = 0) -> np.ndarray:
    args = [str(FFMPEG), "-v", "error", "-i", str(path)]
    if limit:
        args += ["-frames:v", str(limit)]
    args += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    result = run(args)
    buffer = np.frombuffer(result.stdout, dtype=np.uint8)
    count = buffer.size // (width * height * 3)
    return buffer[: count * width * height * 3].reshape(count, height, width, 3)


def laplacian_energy(frame: np.ndarray) -> float:
    """Mean absolute Laplacian of the luma channel: a proxy for acutance."""
    luma = frame.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    h, w = luma.shape
    out = np.zeros((h - 2, w - 2), dtype=np.float32)
    for dy in range(3):
        for dx in range(3):
            weight = kernel[dy, dx]
            if weight:
                out += weight * luma[dy : dy + h - 2, dx : dx + w - 2]
    return float(np.abs(out).mean())


def bicubic(frame: np.ndarray, factor: int) -> np.ndarray:
    """Bicubic upsample via ffmpeg, so the comparison uses the same resampler."""
    h, w = frame.shape[:2]
    result = run([
        str(FFMPEG), "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", "-",
        "-vf", f"scale={w * factor}:{h * factor}:flags=bicubic",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], input_bytes=frame.tobytes())
    buffer = np.frombuffer(result.stdout, dtype=np.uint8)
    return buffer[: (w * factor) * (h * factor) * 3].reshape(h * factor, w * factor, 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, default=4, choices=(2, 4))
    parser.add_argument("--frames", type=int, default=12)
    args = parser.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    source = WORK / "source.mp4"
    if not source.is_file():
        fixture = ROOT / ".cache" / "fixture.mp4"
        if not fixture.is_file():
            print("fixture missing; run scripts/e2e-video.py --fixture-only")
            return 2
        run([str(FFMPEG), "-y", "-v", "error", "-i", str(fixture),
             "-frames:v", str(args.frames), "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(source)])
    width, height, frames = probe_size(source)
    print(f"source: {width}x{height}, {frames} frames")

    output = WORK / f"source_x{args.scale}.mp4"
    from engine.pipeline.upscale_job import upscale_video

    print("running the upscaler…")
    result = upscale_video(source, output, scale=args.scale)
    print(f"  -> {result['output_path']} ({result['width']}x{result['height']})")

    out_w, out_h, out_frames = probe_size(output)
    factor = 4 if args.scale == 4 else 2
    check("output resolution is the requested multiple",
          (out_w, out_h) == (width * factor, height * factor),
          f"{out_w}x{out_h} vs {width * factor}x{height * factor}")
    check("frame count preserved", out_frames == frames, f"{out_frames} vs {frames}")

    original = read_frames(source, width, height, limit=4)
    enlarged = read_frames(output, out_w, out_h, limit=4)
    check("both clips decode", len(original) >= 1 and len(enlarged) >= 1,
          f"{len(original)} vs {len(enlarged)}")
    if len(original) == 0 or len(enlarged) == 0:
        return 1

    # Content: downscaling the model output must land back on the source.
    reference = original[0].astype(np.float32)
    recreated = _area_downscale(enlarged[0], width, height)
    delta = float(np.abs(recreated.astype(np.float32) - reference).mean())
    check("content matches the source after downscaling back", delta < 14.0,
          f"mean |delta| {delta:.2f}")

    model_energy = laplacian_energy(enlarged[0])
    bicubic_energy = laplacian_energy(bicubic(original[0], factor))
    check("model adds detail over bicubic", model_energy > bicubic_energy * 1.05,
          f"model {model_energy:.1f} vs bicubic {bicubic_energy:.1f}")

    print()
    main_config_scenarios(width, height, frames)

    print()
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1



def has_audio(path: Path) -> bool:
    result = run([str(FFPROBE), "-v", "error", "-show_entries", "stream=codec_type",
                  "-of", "json", str(path)])
    streams = json.loads(result.stdout.decode("utf-8", "replace") or "{}").get("streams", [])
    return any(stream.get("codec_type") == "audio" for stream in streams)


def build_cut_fixture(destination: Path, first: Path, frames: int) -> int:
    """A clip that changes shot half way, with an audio track. Returns its frames.

    Each shot is 2.5s on purpose. The reference merges anything under 1.2s and
    then anything under 2.0s into its neighbour, so shorter shots are correctly
    *not* split — an earlier version of this fixture passed for the wrong reason.
    """
    shot_frames = 60
    half = shot_frames
    frames = shot_frames * 2
    head = destination.parent / "cut-head.mp4"
    tail = destination.parent / "cut-tail.mp4"
    run([str(FFMPEG), "-y", "-v", "error", "-stream_loop", "-1", "-i", str(first),
         "-frames:v", str(half), "-an", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", str(head)])
    run([str(FFMPEG), "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=white:s=640x360:d={half / 24:.3f}:r=24",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(tail)])
    joined = destination.parent / "cut-joined.mp4"
    run([str(FFMPEG), "-y", "-v", "error", "-i", str(head), "-i", str(tail),
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(joined)])
    run([str(FFMPEG), "-y", "-v", "error", "-i", str(joined),
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={frames / 24:.3f}",
         "-c:v", "copy", "-c:a", "aac", "-shortest", str(destination)])
    for temporary in (head, tail, joined):
        temporary.unlink(missing_ok=True)
    return frames


def main_config_scenarios(width: int, height: int, frames: int) -> int:
    """The configuration surface the reference UI actually exposes."""
    from engine.pipeline.upscale_job import upscale_with_config
    from engine.pipeline.upscale_plan import UpscaleConfig, build_segments, detect_scene_cuts

    print("2. the full upscale configuration")

    preset = UpscaleConfig.from_params({"mode": "upscale_resolution", "target_preset": "1080p"})
    result = upscale_with_config(WORK / "source.mp4", None, preset, output_dir=WORK)
    out_w, out_h, out_frames = probe_size(Path(result["output_path"]))
    check("target preset reaches 1080p", (out_w, out_h) == (1920, 1080), f"{out_w}x{out_h}")
    check("preset keeps every frame", out_frames == frames, f"{out_frames} vs {frames}")

    enhance = UpscaleConfig.from_params({"mode": "enhance_same_resolution", "denoise_strength": 0.3})
    result = upscale_with_config(WORK / "source.mp4", None, enhance, output_dir=WORK)
    out_w, out_h, out_frames = probe_size(Path(result["output_path"]))
    check("same-resolution mode keeps the source size", (out_w, out_h) == (width, height),
          f"{out_w}x{out_h}")
    check("same-resolution mode keeps every frame", out_frames == frames, f"{out_frames} vs {frames}")
    check("same-resolution mode is reported as such", result["same_resolution"] is True)

    cut_source = WORK / "cuts.mp4"
    cut_frames = build_cut_fixture(cut_source, WORK / "source.mp4", frames)
    cuts = detect_scene_cuts(cut_source)
    check("a hard cut is detected", len(cuts) >= 1, str(cuts))
    info_duration = cut_frames / 24
    segments = build_segments(info_duration, cuts)
    check("the clip splits into the detected shots", len(segments) >= 2, str(segments))

    split = UpscaleConfig.from_params({
        "mode": "upscale_resolution", "target_preset": "1080p",
        "scene_split": "rule", "keep_audio": True,
    })
    result = upscale_with_config(cut_source, None, split, segments=segments, output_dir=WORK)
    out_w, out_h, out_frames = probe_size(Path(result["output_path"]))
    check("a split upscale keeps the frame count", out_frames == cut_frames, f"{out_frames} vs {cut_frames}")
    check("a split upscale reports its segments", result["segments"] >= 2, str(result["segments"]))
    check("audio survives by default", has_audio(Path(result["output_path"])))

    silent = UpscaleConfig.from_params({
        "mode": "upscale_resolution", "target_preset": "1080p",
        "scene_split": "rule", "keep_audio": False,
    })
    result = upscale_with_config(cut_source, None, silent, segments=segments, output_dir=WORK)
    check("keep_audio=false drops the track", not has_audio(Path(result["output_path"])))
    return 0


def _area_downscale(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """Box-filter resize, matching ffmpeg's `flags=area`."""
    factor = frame.shape[1] // width
    if factor <= 1 or frame.shape[1] % width:
        factor = 1
    if factor == 1:
        return frame[:height, :width]
    trimmed = frame[: height * factor, : width * factor].astype(np.float32)
    blocks = trimmed.reshape(height, factor, width, factor, 3)
    return blocks.mean(axis=(1, 3)).round().astype(np.uint8)


if __name__ == "__main__":
    sys.exit(main())
