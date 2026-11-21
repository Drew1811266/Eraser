"""preview.* handlers: one frame at a time, for the annotation canvas.

The reference implementation decoded frames in Python with OpenCV and pushed
base64 JPEGs to the UI. The same contract is kept here, but each frame is
extracted by ffmpeg from the nearest keyframe and cached on disk: the canvas
scrubs back and forth over the same handful of positions, and re-decoding the
whole video for each one would make dragging the slider unusable.

Sessions are deliberately small — path, geometry, cache directory — so a stale
session cannot pin a decoder or a file handle.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import uuid
from pathlib import Path
from typing import Any

from engine.pipeline.logging import logger
from engine.platform.files import clean_stale, remove_tree
from engine.platform.ffmpeg import resolve
from engine.platform.paths import work_dir
from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer

DEFAULT_PREVIEW_WIDTH = 1280
JPEG_QUALITY = 4  # ffmpeg's -q:v scale, 2 (best) .. 31 (worst)

_sessions: dict[str, "PreviewSession"] = {}
_lock = threading.Lock()


class PreviewSession:
    def __init__(self, path: Path, width: int, height: int, fps: float, frames: int, max_width: int) -> None:
        self.session_id = uuid.uuid4().hex
        self.path = path
        self.width = width
        self.height = height
        self.fps = fps
        self.frame_count = frames

        scale = min(1.0, max_width / width) if width else 1.0
        self.preview_width = max(2, int(round(width * scale)))
        self.preview_height = max(2, int(round(height * scale)))

        self.directory = work_dir(f"preview-{self.session_id}")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: dict[int, bytes] = {}
        self._lock = threading.Lock()

    def frame(self, index: int) -> bytes:
        clamped = max(0, min(index, max(0, self.frame_count - 1)))
        with self._lock:
            cached = self._cache.get(clamped)
            if cached is not None:
                return cached

        data = self._extract(clamped)
        with self._lock:
            self._cache[clamped] = data
            # Bound the in-memory cache; the canvas rarely revisits more than a
            # few dozen positions and JPEGs add up quickly at 1280px.
            if len(self._cache) > 96:
                self._cache.pop(next(iter(self._cache)))
        return data

    def _extract(self, index: int) -> bytes:
        ffmpeg = resolve("ffmpeg").path
        if not ffmpeg:
            raise RpcError("FFMPEG_MISSING", "ffmpeg not found")

        target = self.directory / f"{index:06d}.jpg"
        if not target.is_file():
            timestamp = index / self.fps if self.fps > 0 else 0.0
            command = [
                ffmpeg, "-y", "-v", "error",
                # Seeking before -i lands on the keyframe before the timestamp
                # and decodes forward, which is far cheaper than scanning.
                "-ss", f"{timestamp:.6f}",
                "-i", str(self.path),
                "-frames:v", "1",
                "-vf", f"scale={self.preview_width}:-2",
                "-q:v", str(JPEG_QUALITY),
                str(target),
            ]
            completed = _run(command)
            if completed != 0 or not target.is_file():
                raise RpcError("INTERNAL_ERROR", f"cannot extract frame {index}",
                               {"path": str(self.path), "frame_index": index})
        return target.read_bytes()

    def close(self) -> None:
        with self._lock:
            self._cache.clear()
        remove_tree(self.directory)


def _run(command: list[str]) -> int:
    import subprocess

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("frame extraction failed: %s", exc)
        return 1
    if completed.returncode != 0:
        logger.warning("ffmpeg: %s", completed.stderr.decode("utf-8", "replace")[-300:])
    return completed.returncode


def _get(session_id: str) -> PreviewSession:
    with _lock:
        session = _sessions.get(session_id)
    if session is None:
        raise RpcError("TASK_NOT_FOUND", f"Unknown preview session: {session_id}", {"session_id": session_id})
    return session


def _open(params: dict[str, Any]) -> dict[str, Any]:
    from engine.pipeline.watermark import VideoProcessor

    path = Path(str(params.get("path", "")))
    if not path.is_file():
        raise RpcError("INVALID_PARAMS", f"File not found: {path}", {"path": str(path)})

    try:
        info = VideoProcessor(remover=None).get_video_info(str(path))
    except Exception as exc:  # noqa: BLE001 - surface as a structured error
        raise RpcError("INTERNAL_ERROR", f"Cannot read video metadata: {exc}", {"path": str(path)}) from exc

    max_width = int(params.get("max_width") or DEFAULT_PREVIEW_WIDTH)
    session = PreviewSession(path, int(info.width), int(info.height), float(info.fps),
                             int(info.frame_count), max_width)
    with _lock:
        _sessions[session.session_id] = session
        # A crashed window leaves sessions behind; keep the oldest few only.
        while len(_sessions) > 8:
            oldest = next(iter(_sessions))
            _sessions.pop(oldest).close()

    logger.info("preview session %s for %s (%dx%d, %d frames)",
                session.session_id, path.name, session.width, session.height, session.frame_count)
    return {
        "session_id": session.session_id,
        "width": session.width,
        "height": session.height,
        "fps": session.fps,
        "frame_count": session.frame_count,
        "preview_width": session.preview_width,
        "preview_height": session.preview_height,
    }


def _frame(params: dict[str, Any]) -> dict[str, Any]:
    session = _get(str(params.get("session_id", "")))
    index = int(params.get("frame_index", 0))
    data = session.frame(index)
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "session_id": session.session_id,
        "frame_index": max(0, min(index, max(0, session.frame_count - 1))),
        "data_url": f"data:image/jpeg;base64,{encoded}",
        "width": session.preview_width,
        "height": session.preview_height,
    }


def _close(params: dict[str, Any]) -> dict[str, Any]:
    session_id = str(params.get("session_id", ""))
    with _lock:
        session = _sessions.pop(session_id, None)
    if session is not None:
        session.close()
    return {"ok": True}


def register(server: RpcServer) -> None:
    server.register("preview.open", _open)
    server.register("preview.frame", _frame)
    server.register("preview.close", _close)


def sweep_stale_sessions(max_age_seconds: int = 24 * 3600) -> None:
    """Remove preview scratch directories left by a previous run."""
    clean_stale("preview-", max_age_seconds)
