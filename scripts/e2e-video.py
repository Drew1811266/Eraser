"""End-to-end video test: the closest thing to a golden sample we can have.

The reference project's real test clips are not available on this machine, so
this script substitutes a *synthetic* clip whose ground truth is known by
construction:

* a smooth gradient background (inpainting has coherent context to pull from),
* an opaque, constant "watermark" rectangle with a white glyph inside it,
* 48 frames at 24 fps, encoded with libx264.

It then drives the real engine over stdio exactly like the Electron shell does
(`process.start` / `process.status`), and checks the result numerically:

  L1  the watermark region actually changed, and changed *away* from the
      watermark colour;
  L2  pixels outside the mask kept their colour (no collateral damage);
  L3  geometry survived: same width, height, fps and frame count.

Usage::

    python scripts/e2e-video.py [--keep]
"""

from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import sys

import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffmpeg.exe"
FFPROBE = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffprobe.exe"

WIDTH, HEIGHT, FPS, FRAMES = 640, 360, 24, 48
WATERMARK = (480, 20, 120, 60)  # x, y, w, h
WATERMARK_RGB = (230, 40, 60)
EXPAND_PX = 5

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


# -- media helpers ---------------------------------------------------------
def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        **kwargs,
    )


def background(width: int = WIDTH, height: int = HEIGHT, phase: float = 0.0) -> np.ndarray:
    """Smooth two-axis gradient; `phase` slides it so frames are not identical."""
    xs = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    ys = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    r = 45 + 110 * xs + 8 * phase
    g = 65 + 95 * ys + 6 * phase
    b = 95 + 70 * (xs + ys) / 2 - 5 * phase
    frame = np.stack(np.broadcast_arrays(r, g, b), axis=-1)
    return np.clip(frame, 0, 255)


def paint_watermark(frame: np.ndarray, rect: tuple[int, int, int, int] = WATERMARK) -> np.ndarray:
    x, y, w, h = rect
    frame[y : y + h, x : x + w] = WATERMARK_RGB
    # A white glyph, so the region is not a single flat colour: a model that
    # merely blurs the area would keep a visible blob here.
    frame[y + 12 : y + h - 12, x + 14 : x + w - 14] = (245, 245, 250)
    return frame


def make_fixture(
    path: Path,
    width: int = WIDTH,
    height: int = HEIGHT,
    frames: int = FRAMES,
    fps: int = FPS,
    rect: tuple[int, int, int, int] = WATERMARK,
    audio: bool = False,
) -> None:
    """Encode the synthetic clip; optionally mux a tone so audio survives the trip."""
    silent = path.with_name(f"{path.stem}_silent.mp4") if audio else path
    command = [
        str(FFMPEG), "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(silent),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    try:
        for index in range(frames):
            frame = background(width, height, 6.0 * index / frames)
            frame = paint_watermark(frame, rect)
            process.stdin.write(frame.astype(np.uint8).tobytes())
        process.stdin.close()
    finally:
        stderr = process.stderr.read().decode("utf-8", "replace")
        code = process.wait()
    if code != 0:
        raise SystemExit(f"fixture encode failed: {stderr[-800:]}")

    if audio:
        duration = frames / fps
        mixed = run([
            str(FFMPEG), "-y", "-v", "error",
            "-i", str(silent),
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration:.3f}",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
            str(path),
        ])
        silent.unlink(missing_ok=True)
        if mixed.returncode != 0:
            raise SystemExit(f"audio mux failed: {mixed.stderr.decode('utf-8', 'replace')[-500:]}")


def probe(path: Path) -> dict:
    result = run([
        str(FFPROBE), "-v", "error",
        "-show_entries", "stream=codec_type,width,height,r_frame_rate,nb_frames",
        "-of", "json", str(path),
    ])
    streams = json.loads(result.stdout.decode("utf-8", "replace"))["streams"]
    video = next(stream for stream in streams if stream.get("codec_type") == "video")
    numerator, denominator = video["r_frame_rate"].split("/")
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": float(numerator) / float(denominator or 1),
        "frames": int(video.get("nb_frames") or 0),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


def decode(path: Path) -> np.ndarray:
    result = run([
        str(FFMPEG), "-v", "error", "-i", str(path),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    if result.returncode != 0:
        raise SystemExit(f"decode failed: {result.stderr.decode('utf-8', 'replace')[-500:]}")
    buffer = np.frombuffer(result.stdout, dtype=np.uint8)
    count = buffer.size // (WIDTH * HEIGHT * 3)
    return buffer[: count * WIDTH * HEIGHT * 3].reshape(count, HEIGHT, WIDTH, 3)


# -- engine client ---------------------------------------------------------
class Engine:
    """Minimal stdio JSON-RPC client, mirroring the Electron client's contract."""

    def __init__(self, python: str, cwd: Path) -> None:
        self.process = subprocess.Popen(
            [python, "-u", "-m", "engine.main"],
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._ids = 0
        self._responses: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self.notifications: list[dict] = []
        self.stderr: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for raw in self.process.stdout:
            self._queue.put(raw)
        self._queue.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for raw in self.process.stderr:
            self.stderr.append(raw.decode("utf-8", "replace").rstrip())

    def pump(self, timeout: float) -> bool:
        """Read one frame from stdout; False when the stream closed."""
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return True  # nothing pending yet, keep waiting
        if item is None:
            return False
        frame = json.loads(item.decode("utf-8"))
        if "id" in frame and ("result" in frame or "error" in frame):
            with self._lock:
                self._responses[frame["id"]] = frame
        else:
            self.notifications.append(frame)
        return True

    def request(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        with self._lock:
            self._ids += 1
            request_id = self._ids
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.process.stdin.flush()

        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                frame = self._responses.pop(request_id, None)
            if frame is not None:
                if "error" in frame:
                    raise RuntimeError(f"{method} failed: {frame['error']}")
                return frame["result"]
            if time.monotonic() > deadline:
                raise TimeoutError(f"{method} timed out after {timeout}s")
            if not self.pump(timeout=0.25):
                raise RuntimeError(f"{method} failed: engine exited")

    def close(self) -> None:
        try:
            self.request("system.shutdown", timeout=10)
        except Exception:  # noqa: BLE001 - shutdown races the exit by design
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


# -- assertions ------------------------------------------------------------
def region_diff(left: np.ndarray, right: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x, y, w, h = box
    a = left[y : y + h, x : x + w].astype(np.float32)
    b = right[y : y + h, x : x + w].astype(np.float32)
    return float(np.abs(a - b).mean())



def wait_for_task(engine: Engine, task_id: str, timeout: float) -> dict:
    """Poll until the task reaches a terminal state."""
    deadline = time.monotonic() + timeout
    status: dict = {}
    while time.monotonic() < deadline:
        status = engine.request("process.status", {"task_id": task_id}, timeout=30)
        if status.get("state") in {"success", "failed", "cancelled"}:
            return status
        engine.pump(timeout=0.2)
    return status


def scenario_audio(workdir: Path, python: str) -> None:
    """A portrait clip with an audio track: the mux path, not just the encoder."""
    print("5. portrait clip with audio survives the trip")
    width, height, frames = 360, 640, 24
    rect = (210, 30, 100, 50)
    source = workdir / "portrait.mp4"
    output_dir = workdir / "portrait-out"
    output_dir.mkdir(exist_ok=True)
    make_fixture(source, width=width, height=height, frames=frames, rect=rect, audio=True)

    info = probe(source)
    check("fixture is portrait", (info["width"], info["height"]) == (width, height),
          f"{info['width']}x{info['height']}")
    check("fixture carries an audio track", info["has_audio"])

    engine = Engine(python, ROOT)
    try:
        task = engine.request("process.start", {
            "input_path": str(source),
            "output_dir": str(output_dir),
            "segments": [{
                "start_frame": 0,
                "end_frame": frames - 1,
                "rect": {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]},
                "expand_px": EXPAND_PX,
                "feather_px": 3,
                "enabled": True,
            }],
        }, timeout=30)
        status = wait_for_task(engine, task["task_id"], timeout=300)
    finally:
        engine.close()

    check("portrait job succeeded", status.get("state") == "success", str(status.get("state")))
    output = output_dir / "portrait_no_watermark.mp4"
    check("portrait output exists", output.is_file())
    if not output.is_file():
        return
    result = probe(output)
    check("portrait geometry preserved", (result["width"], result["height"]) == (width, height),
          f"{result['width']}x{result['height']}")
    check("portrait frame count preserved", result["frames"] == frames, str(result["frames"]))
    check("audio track survived the pipeline", result["has_audio"])


def scenario_cancel(workdir: Path, python: str, source: Path) -> None:
    """Cancelling must stop the work, not just relabel it."""
    print("6. cancel actually stops the run")
    output_dir = workdir / "cancel-out"
    output_dir.mkdir(exist_ok=True)

    engine = Engine(python, ROOT)
    try:
        started = time.monotonic()
        task = engine.request("process.start", {
            "input_path": str(source),
            "output_dir": str(output_dir),
            "segments": [{
                "start_frame": 0,
                "end_frame": FRAMES - 1,
                "rect": {"x": WATERMARK[0], "y": WATERMARK[1],
                         "width": WATERMARK[2], "height": WATERMARK[3]},
                "expand_px": EXPAND_PX,
                "feather_px": 3,
                "enabled": True,
            }],
        }, timeout=30)
        time.sleep(1.5)
        engine.request("process.cancel", {"task_id": task["task_id"]}, timeout=30)
        status = wait_for_task(engine, task["task_id"], timeout=120)
        elapsed = time.monotonic() - started
    finally:
        engine.close()

    check("task reports cancelled", status.get("state") == "cancelled", str(status.get("state")))
    check("cancel returns control quickly", elapsed < 90, f"{elapsed:.1f}s")
    output = output_dir / "fixture_no_watermark.mp4"
    partial = output.is_file() and output.stat().st_size > 0
    check("no half-written result is left behind", not partial,
          f"{output.stat().st_size} bytes" if output.is_file() else "absent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="keep the work directory")
    parser.add_argument("--python", default=str(ROOT / ".venv" / "Scripts" / "python.exe"))
    parser.add_argument("--fixture-only", action="store_true",
                        help="only build the synthetic clip, then exit (used by other tests)")
    parser.add_argument("--fixture", default=None, help="where to write the fixture")
    args = parser.parse_args()

    if not FFMPEG.is_file():
        print(f"ffmpeg not found at {FFMPEG}")
        return 2

    if args.fixture_only:
        target = Path(args.fixture) if args.fixture else ROOT / ".cache" / "fixture.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        make_fixture(target)
        print(target)
        return 0

    workdir = (ROOT / ".cache" / "e2e-video").resolve()
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "fixture.mp4"
    output_dir = workdir / "out"
    output_dir.mkdir()

    print(f"work directory: {workdir}")
    print("1. build a synthetic clip with a known watermark")
    make_fixture(source)
    source_info = probe(source)
    check("fixture has the expected geometry",
          source_info["width"] == WIDTH and source_info["height"] == HEIGHT,
          f"{source_info['width']}x{source_info['height']}")
    check("fixture frame count", source_info["frames"] == FRAMES, str(source_info["frames"]))
    source_frames = decode(source)
    check("fixture decodes", len(source_frames) == FRAMES, f"{len(source_frames)} frames")

    print("2. run the real pipeline over the RPC channel")
    engine = Engine(args.python, ROOT)
    started_at = time.monotonic()
    try:
        capabilities = engine.request("system.capabilities", timeout=30)
        check("engine is up", isinstance(capabilities.get("engine_version"), str))
        runtime = capabilities.get("runtime") or {}
        print(f"   provider: {runtime.get('available_providers')} cuda_usable={runtime.get('cuda_usable')}")

        x, y, w, h = WATERMARK
        task = engine.request("process.start", {
            "input_path": str(source),
            "output_dir": str(output_dir),
            "segments": [{
                "start_frame": 0,
                "end_frame": FRAMES - 1,
                "rect": {"x": x, "y": y, "width": w, "height": h},
                "expand_px": EXPAND_PX,
                "feather_px": 3,
                "enabled": True,
            }],
        }, timeout=30)
        task_id = task["task_id"]
        check("process.start returns a task id", isinstance(task_id, str) and len(task_id) > 8)

        status = {}
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status = engine.request("process.status", {"task_id": task_id}, timeout=30)
            if status["state"] in {"success", "failed", "cancelled"}:
                break
            engine.pump(timeout=0.2)
        elapsed = time.monotonic() - started_at

        check("task finished", status.get("state") == "success", str(status.get("state")))
        if status.get("state") != "success":
            print(f"   error: {status.get('error')}")
            for line in engine.stderr[-25:]:
                print(f"   stderr| {line}")
            return 1
        check("progress reached 1.0", float(status.get("progress", 0)) >= 0.999, str(status.get("progress")))

        progress_events = [
            n["params"] for n in engine.notifications
            if isinstance(n.get("params"), dict) and "progress" in n["params"]
        ]
        check("progress was streamed, not silent", len(progress_events) >= 3, f"{len(progress_events)} events")
        non_monotonic = [
            (a["progress"], b["progress"])
            for a, b in zip(progress_events, progress_events[1:])
            if b["progress"] < a["progress"]
        ]
        check("progress never goes backwards", not non_monotonic, str(non_monotonic[:3]))

        # A progress bar that only ever shows 0% and then 100% is a bug: the
        # pipeline must report intermediate work.
        intermediate = [
            event for event in progress_events if 0.01 < float(event["progress"]) < 0.99
        ]
        check("progress reports intermediate work", len(intermediate) >= 3,
              f"{len(intermediate)} of {len(progress_events)} events")
        counted = [event for event in progress_events if int(event.get("processed") or 0) > 0]
        check("frame counter advances", bool(counted),
              f"max processed {max((int(e.get('processed') or 0) for e in progress_events), default=0)}")
        print(f"   processed {FRAMES} frames in {elapsed:.1f}s "
              f"({elapsed / FRAMES * 1000:.0f} ms/frame)")
    finally:
        engine.close()

    output = output_dir / "fixture_no_watermark.mp4"
    print("3. verify the result numerically")
    check("output file exists", output.is_file(), str(output))
    if not output.is_file():
        return 1

    out_info = probe(output)
    check("geometry preserved",
          out_info["width"] == WIDTH and out_info["height"] == HEIGHT,
          f"{out_info['width']}x{out_info['height']}")
    check("frame rate preserved", abs(out_info["fps"] - FPS) < 0.01, f"{out_info['fps']:.3f}")
    check("frame count preserved", out_info["frames"] == FRAMES, str(out_info["frames"]))

    out_frames = decode(output)
    check("output decodes", len(out_frames) == FRAMES, f"{len(out_frames)} frames")
    if len(out_frames) != FRAMES:
        return 1

    mask_box = (x - EXPAND_PX, y - EXPAND_PX, w + 2 * EXPAND_PX, h + 2 * EXPAND_PX)
    samples = [0, FRAMES // 2, FRAMES - 1]

    for index in samples:
        before = source_frames[index]
        after = out_frames[index]
        changed = region_diff(before, after, mask_box)
        check(f"frame {index}: watermark region changed", changed > 20.0, f"mean |delta| {changed:.1f}")

        inset = (x + 8, y + 8, w - 16, h - 16)
        mean_colour = out_frames[index][inset[1] : inset[1] + inset[3],
                                        inset[0] : inset[0] + inset[2]].reshape(-1, 3).mean(axis=0)
        distance = float(np.linalg.norm(mean_colour - np.array(WATERMARK_RGB, dtype=np.float32)))
        check(f"frame {index}: watermark colour is gone", distance > 60.0,
              f"{mean_colour.round(1)} vs {WATERMARK_RGB} -> distance {distance:.0f}")

    ring = mask_box[1] - 40, mask_box[0] - 40, mask_box[2] + 80, mask_box[3] + 80
    collateral = []
    for index in range(0, FRAMES, 8):
        pad = 40
        inner_x, inner_y = mask_box[0], mask_box[1]
        inner_w, inner_h = mask_box[2], mask_box[3]
        outer = out_frames[index].astype(np.float32)
        reference = source_frames[index].astype(np.float32)
        delta = np.abs(outer - reference).mean(axis=2)
        keep = np.ones((HEIGHT, WIDTH), dtype=bool)
        keep[inner_y : inner_y + inner_h, inner_x : inner_x + inner_w] = False
        # ignore the outermost 2 px: x264 edge padding differs at the border
        keep[:2, :] = keep[-2:, :] = False
        keep[:, :2] = keep[:, -2:] = False
        collateral.append(float(delta[keep].mean()))
    worst = max(collateral)
    check("pixels outside the mask are untouched", worst < 8.0,
          f"worst mean |delta| {worst:.2f} over {len(collateral)} frames")

    scenario_audio(workdir, args.python)
    print()
    scenario_cancel(workdir, args.python, source)

    print()
    print(f"{passed} passed, {failed} failed")
    if args.keep:
        print(f"artifacts kept in {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
