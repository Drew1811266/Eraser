"""Install and drive the SeedVR2 runtime.

SeedVR2 needs PyTorch and a multi-gigabyte model, so it cannot live inside the
frozen engine. The reference solves this with an isolated Python virtualenv, a
pinned checkout of the upstream inference CLI, and weights downloaded on demand;
this is the same design, with the pieces that are Windows-specific (no process
groups, no `select` on pipes, `CREATE_NO_WINDOW`) handled explicitly.

Two operations:

    install(model_id)   venv -> pinned CLI -> requirements -> weights
    upscale(...)        run the CLI per segment, parse its progress, mux audio

Both report progress through a callback and honour a cancel event; cancelling
during the dependency install kills the pip process tree, which is the only way
to stop a pip mid-download.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from engine.pipeline.logging import logger
from engine.pipeline.seedvr import manifest
from engine.pipeline.seedvr.capability import RuntimeState, find_python, read_state, write_state

ProgressCallback = Callable[[dict[str, Any]], None]
CancelEvent = Any

#: How much of the progress bar each install step is worth. The dependency
#: install dominates because it is a torch download.
STEP_WEIGHTS = {
    "venv": 0.05,
    "repo": 0.10,
    "requirements": 0.55,
    "model": 0.30,
}

PROGRESS_LINE = re.compile(r"^PROGRESS\s+chunk=(\d+)\s*/\s*(\d+)", re.IGNORECASE)
STAGE_LINE = re.compile(r"PROGRESS\s+stage=warmup\s+step=([a-zA-Z0-9_\-]+)", re.IGNORECASE)


class SeedVRError(RuntimeError):
    """The runtime could not be installed or used."""


class _Cancelled(RuntimeError):
    """Raised inside the worker when the task is cancelled."""


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _emit(progress: ProgressCallback | None, **payload: Any) -> None:
    if progress is None:
        return
    try:
        progress(payload)
    except Exception as exc:  # noqa: BLE001 - progress must never break a run
        logger.warning("seedvr progress callback failed: %s", exc)


def _run(command: list[str], *, timeout: float | None = None,
         cancel: CancelEvent = None) -> subprocess.CompletedProcess:
    """Run a command, checking the cancel flag while it works."""
    creation = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_no_window() | creation,
    )

    chunks: list[str] = []

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            chunks.append(line)

    reader = threading.Thread(target=pump, name="seedvr-output", daemon=True)
    reader.start()

    try:
        while True:
            try:
                process.wait(timeout=0.4)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    _kill_tree(process)
                    raise _Cancelled()
                if timeout is not None:
                    timeout -= 0.4
                    if timeout <= 0:
                        _kill_tree(process)
                        raise SeedVRError("command timed out")
    finally:
        reader.join(timeout=2)

    return subprocess.CompletedProcess(command, process.returncode, "".join(chunks), "")


def _kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        # taskkill is the only reliable way to stop `pip` and its children;
        # terminating the parent alone leaves the download running.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False, creationflags=_no_window())
    else:
        process.kill()


def _download(url: str, destination: Path, *, expected_sha256: str = "",
              on_progress: Callable[[int, int], None] | None = None,
              cancel: CancelEvent = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")

    digest = hashlib.sha256()
    received = 0
    request = urllib.request.Request(url, headers={"User-Agent": "eraser-engine/1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as sink:
            total = int(response.headers.get("Content-Length") or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise _Cancelled()
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                sink.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if on_progress is not None:
                    on_progress(received, total)

        if expected_sha256 and digest.hexdigest() != expected_sha256:
            partial.unlink(missing_ok=True)
            raise SeedVRError(
                f"checksum mismatch: expected {expected_sha256}, got {digest.hexdigest()}"
            )
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _extract_repo(archive: Path, destination: Path) -> None:
    """Unpack the pinned CLI and flatten the single top-level directory."""
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="seedvr-repo-") as scratch_name:
        scratch = Path(scratch_name)
        with tarfile.open(archive) as tar:
            tar.extractall(scratch)  # noqa: S202 - the archive is pinned by commit
        entries = [path for path in scratch.iterdir() if path.is_dir()]
        if len(entries) != 1:
            raise SeedVRError("unexpected archive layout")
        for item in entries[0].iterdir():
            target = destination / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))


@dataclass
class InstallResult:
    python: str
    model_id: str
    files: list[str]


class SeedVRRuntime:
    """Install and run SeedVR2. One instance per engine process is fine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    # -- install -------------------------------------------------------
    def install(self, model_id: str, *, progress: ProgressCallback | None = None,
                cancel: CancelEvent = None) -> InstallResult:
        spec = manifest.get_spec(model_id)
        python = find_python()
        if not python:
            raise SeedVRError("no Python 3.12 interpreter found")

        with self._lock:
            state = RuntimeState(phase="installing", model_id=model_id, python=python,
                                 installed_models=list(read_state().installed_models))
            write_state(state)

        def stage(name: str, local: float, message: str) -> None:
            offset = sum(STEP_WEIGHTS[key] for key in list(STEP_WEIGHTS)[:list(STEP_WEIGHTS).index(name)])
            state.phase = "installing"
            state.progress = min(0.99, offset + STEP_WEIGHTS[name] * max(0.0, min(1.0, local)))
            state.message = message
            write_state(state)
            _emit(progress, phase="install", progress=state.progress, message=message, step=name)

        try:
            stage("venv", 0.0, "创建独立运行环境")
            if not manifest.venv_python().is_file():
                result = _run([python, "-m", "venv", str(manifest.venv_dir())], cancel=cancel)
                if result.returncode != 0:
                    raise SeedVRError(f"venv creation failed: {result.stdout[-400:]}")
            stage("venv", 1.0, "独立运行环境就绪")

            stage("repo", 0.0, "下载 SeedVR2 推理代码")
            if not manifest.inference_script().is_file():
                with tempfile.TemporaryDirectory(prefix="seedvr-dl-") as scratch_name:
                    archive = Path(scratch_name) / "repo.tar.gz"
                    _download(manifest.REPO_URL, archive, cancel=cancel)
                    _extract_repo(archive, manifest.repo_dir())
            stage("repo", 1.0, "推理代码就绪")

            stage("requirements", 0.0, "安装依赖（含 PyTorch，较慢）")
            if not (manifest.runtime_root() / ".deps_ready").is_file():
                if not manifest.requirements_file().is_file():
                    raise SeedVRError("the pinned CLI has no requirements.txt")
                result = _run(
                    [str(manifest.venv_python()), "-m", "pip", "install", "--upgrade", "pip"],
                    cancel=cancel,
                )
                if result.returncode != 0:
                    raise SeedVRError(f"pip upgrade failed: {result.stdout[-400:]}")
                result = _run(
                    [str(manifest.venv_python()), "-m", "pip", "install", "-r",
                     str(manifest.requirements_file())],
                    cancel=cancel,
                )
                if result.returncode != 0:
                    raise SeedVRError(f"dependency install failed: {result.stdout[-600:]}")
                (manifest.runtime_root() / ".deps_ready").write_text("ok", encoding="utf-8")
            stage("requirements", 1.0, "依赖安装完成")

            stage("model", 0.0, f"下载模型 {spec.display_name}")
            target = manifest.model_path(model_id)
            if not manifest.model_installed(model_id):
                _download(
                    spec.url, target,
                    expected_sha256=spec.sha256,
                    on_progress=lambda received, total: stage(
                        "model",
                        (received / total) if total else 0.0,
                        f"下载模型 {received / 1e9:.1f}GB" + (f" / {total / 1e9:.1f}GB" if total else ""),
                    ),
                    cancel=cancel,
                )
            stage("model", 1.0, "模型下载完成")

            installed = list(dict.fromkeys(read_state().installed_models + [model_id]))
            state = RuntimeState(phase="ready", progress=1.0, message="运行时就绪",
                                 model_id=model_id, python=str(manifest.venv_python()),
                                 installed_models=installed)
            write_state(state)
            return InstallResult(python=str(manifest.venv_python()), model_id=model_id,
                                 files=[str(target)])
        except _Cancelled:
            state = RuntimeState(phase="absent", model_id=model_id, message="已取消",
                                 installed_models=read_state().installed_models)
            write_state(state)
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            state = RuntimeState(phase="failed", model_id=model_id, error=str(exc)[:500],
                                 message="安装失败", installed_models=read_state().installed_models)
            write_state(state)
            logger.warning("SeedVR2 install failed: %s", exc)
            raise

    # -- inference -----------------------------------------------------
    def upscale(
        self,
        input_path: str | Path,
        output_dir: Path,
        *,
        model_id: str,
        resolution: int = 1080,
        progress: ProgressCallback | None = None,
        cancel: CancelEvent = None,
    ) -> Path:
        """Run the CLI over one file and return the produced video."""
        spec = manifest.get_spec(model_id)
        if not manifest.model_installed(model_id):
            raise SeedVRError(f"model not installed: {spec.display_name}")

        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            str(manifest.venv_python()), "-u", str(manifest.inference_script()),
            "--output", str(output_dir),
            "--model_dir", str(manifest.models_dir()),
            "--dit_model", spec.filename,
            "--resolution", str(max(256, int(resolution))),
            "--batch_size", "1",
            "--chunk_size", "0",
            "--temporal_overlap", "0",
            "--max_resolution", "0",
            "--input_noise_scale", "0.000",
            "--latent_noise_scale", "0.000",
            "--video_backend", "ffmpeg",
            "--dit_offload_device", "none",
            "--vae_offload_device", "none",
            "--tensor_offload_device", "none",
            str(input_path),
        ]

        logger.info("starting SeedVR2 inference: %s", " ".join(command[-8:]))
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_no_window(),
        )

        def pump() -> None:
            assert process.stdout is not None
            tail: list[str] = []
            for line in process.stdout:
                tail.append(line.rstrip())
                if len(tail) > 40:
                    tail.pop(0)
                match = PROGRESS_LINE.match(line.strip())
                if match:
                    done, total = int(match.group(1)), max(1, int(match.group(2)))
                    _emit(progress, phase="upscale", progress=done / total,
                          message=f"SeedVR2 {done}/{total}", processed=done, total=total)
                    continue
                stage = STAGE_LINE.search(line)
                if stage:
                    _emit(progress, phase="prepare", message=f"准备：{stage.group(1)}")
            self._last_output = tail

        reader = threading.Thread(target=pump, name="seedvr-progress", daemon=True)
        reader.start()

        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                _kill_tree(process)
                reader.join(timeout=2)
                raise _Cancelled()
            process.wait(timeout=0.3)
        reader.join(timeout=3)

        if process.returncode != 0:
            detail = "\n".join(getattr(self, "_last_output", [])[-15:])
            raise SeedVRError(f"SeedVR2 exited with {process.returncode}: {detail[-600:]}")

        produced = sorted(output_dir.glob("*.mp4"), key=lambda path: path.stat().st_mtime)
        if not produced:
            raise SeedVRError("SeedVR2 produced no output file")
        return produced[-1]


def install(model_id: str, *, progress: ProgressCallback | None = None,
            cancel: CancelEvent = None) -> InstallResult:
    return SeedVRRuntime().install(model_id, progress=progress, cancel=cancel)
