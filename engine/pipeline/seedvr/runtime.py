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
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from engine.pipeline.logging import logger
from engine.pipeline.seedvr import manifest
from engine.pipeline.seedvr.capability import RuntimeState, find_python, read_state, write_state
from engine.pipeline.seedvr.progress import ProgressReader

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


def _cuda_available() -> bool:
    """Whether this machine has an NVIDIA GPU the runtime could use."""
    try:
        from engine.platform import ort_runtime

        # The same answer the ONNX engine uses: asking onnxruntime is one thing,
        # but whether CUDA actually loads next to these DLLs is another.
        return bool(ort_runtime.cuda_usable())
    except Exception:  # noqa: BLE001 - "cannot tell" means "do not try"
        logger.warning("cannot detect CUDA; installing the CPU build of torch")
        return False


def _torch_index() -> str:
    """Which PyTorch wheel index to install from.

    cu128 rather than cu126 on purpose: cu126 has no kernels for sm_120, so on a
    Blackwell card (RTX 50xx) it installs cleanly and then fails at the first
    tile with "no kernel image is available for execution on the device". That
    is a confusing failure to debug from a UI, so the default is the index that
    works on both old and new cards. Override with ERASER_TORCH_INDEX when the
    driver needs something else.
    """
    return os.environ.get("ERASER_TORCH_INDEX") or "https://download.pytorch.org/whl/cu128"


def _install_cuda_torch(cancel: CancelEvent) -> None:
    """Replace torch/torchvision with CUDA builds. Best effort by design."""
    index = _torch_index()
    uv = _uv_executable()
    if uv:
        command = [uv, "pip", "install", "--python", str(manifest.venv_python()),
                   "--reinstall-package", "torch", "--reinstall-package", "torchvision",
                   "torch", "torchvision", "--index-url", index]
    else:
        command = [str(manifest.venv_python()), "-m", "pip", "install",
                   "--force-reinstall",
                   "torch", "torchvision", "--index-url", index]
    try:
        result = _run(command, stall_timeout=1800.0, cancel=cancel)
    except SeedVRError as exc:
        logger.warning("CUDA torch install failed, keeping the CPU build: %s", exc)
        return
    if result.returncode != 0:
        logger.warning("CUDA torch install failed, keeping the CPU build: %s",
                       result.stdout[-300:])


def _uv_executable() -> str:
    """`uv` if the machine has it, otherwise empty.

    Deliberately optional: it is a speed and reliability improvement, not a
    dependency. A machine with only Python still installs, just slower.
    """
    if os.environ.get("ERASER_NO_UV") == "1":
        return ""
    found = shutil.which("uv")
    return found or ""


def _child_env() -> dict[str, str]:
    """Environment for every child process in the runtime.

    Upstream prints emoji and box drawing; on a Chinese-locale Windows the console
    code page is GBK, and the first `⚠️` kills the process with
    `UnicodeEncodeError: 'gbk' codec can't encode character`. Forcing UTF-8 on
    the child is the fix, and it has to be on the environment rather than the
    command because the crash happens during import, before any argument is read.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # The CLI shells out to ffmpeg and insists on finding it on PATH. The engine
    # ships its own copy, so point the child at it rather than asking the user to
    # install ffmpeg separately.
    try:
        from engine.platform.ffmpeg import resolve

        found = resolve("ffmpeg")
        if found.path:
            directory = str(Path(found.path).parent)
            existing = env.get("PATH", "")
            if directory not in existing.split(os.pathsep):
                env["PATH"] = directory + (os.pathsep + existing if existing else "")
    except Exception:  # noqa: BLE001 - a missing ffmpeg surfaces later, clearly
        logger.warning("could not add the bundled ffmpeg to PATH")

    return env


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _emit(callback: ProgressCallback | None, **payload: Any) -> None:
    # Named `callback`, not `progress`: the payload carries its own `progress`
    # key and the two would collide.
    if callback is None:
        return
    try:
        callback(payload)
    except Exception as exc:  # noqa: BLE001 - progress must never break a run
        logger.warning("seedvr progress callback failed: %s", exc)


def _run(
    command: list[str],
    *,
    timeout: float | None = None,
    stall_timeout: float | None = None,
    on_line: Callable[[str], None] | None = None,
    cancel: CancelEvent = None,
) -> subprocess.CompletedProcess:
    """Run a command, watching for cancellation and for a silent stall.

    `stall_timeout` exists because pip can fail in the least helpful way
    possible: not an error, just no bytes, forever. Without it the install hangs
    at "installing dependencies" with no explanation and no end.
    """
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
    last_output = [time.monotonic()]

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            chunks.append(line)
            last_output[0] = time.monotonic()
            if on_line is not None:
                try:
                    on_line(line.rstrip())
                except Exception:  # noqa: BLE001 - a callback must not kill the run
                    pass

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
                if stall_timeout is not None and time.monotonic() - last_output[0] > stall_timeout:
                    _kill_tree(process)
                    last_line = chunks[-1].strip()[:120] if chunks else "(nothing)"
                    raise SeedVRError(
                        f"no output for {int(stall_timeout)}s; last line: {last_line}"
                    )
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


def _lock_path() -> Path:
    return manifest.runtime_root() / ".install.lock"


def _holder_pid() -> int:
    try:
        return int(_lock_path().read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _process_alive(pid: int) -> bool:
    from engine.platform.watchdog import parent_is_alive

    return pid > 0 and parent_is_alive(pid)


class _InstallLock:
    """One install at a time, across processes.

    Two installs into the same virtualenv do not fail — they deadlock, one
    holding the other's lock, and the pair looks like a hung download. A user
    who clicks the button twice must get "already running", not silence.
    """

    def __init__(self) -> None:
        self.acquired = False

    def __enter__(self) -> "_InstallLock":
        path = _lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        holder = _holder_pid()
        if holder and holder != os.getpid() and _process_alive(holder):
            raise SeedVRError(f"another install is already running (pid {holder})")
        path.write_text(str(os.getpid()), encoding="utf-8")
        self.acquired = True
        return self

    def __exit__(self, *_exc) -> None:
        if self.acquired:
            try:
                _lock_path().unlink()
            except OSError:
                pass


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

        with self._lock, _InstallLock():
            return self._install_locked(model_id, spec, python, progress, cancel)

    def _install_locked(
        self,
        model_id: str,
        spec: Any,
        python: str,
        progress: ProgressCallback | None,
        cancel: CancelEvent,
    ) -> "InstallResult":
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
                # Best effort, with a short leash: a venv created by the stdlib
                # already has a working pip, and a proxy that stalls the version
                # lookup must not stop the install that follows it.
                try:
                    _run(
                        [str(manifest.venv_python()), "-m", "pip", "install", "--upgrade", "pip"],
                        stall_timeout=90.0,
                        cancel=cancel,
                    )
                except SeedVRError as exc:
                    logger.warning("pip upgrade skipped: %s", exc)
                    stage("requirements", 0.0, "跳过 pip 升级（网络不通），继续安装依赖")
                def pip_line(line: str) -> None:
                    text = line.strip()
                    if not text:
                        return
                    # pip's own words are the only honest progress there is.
                    stage("requirements", 0.5, text[:120])

                # Prefer `uv` when it exists: the same install takes minutes
                # instead of tens of minutes, and its resolver does not stall on
                # a slow index the way pip's does. Falling back keeps this
                # working on a machine that has only Python.
                installer = _uv_executable()
                if installer:
                    command = [installer, "pip", "install", "--python",
                               str(manifest.venv_python()), "-r", str(manifest.requirements_file())]
                else:
                    command = [str(manifest.venv_python()), "-m", "pip", "install", "-r",
                               str(manifest.requirements_file())]

                result = _run(
                    command,
                    # Either installer prints nothing while it downloads a 2.5GB
                    # wheel, so the leash has to tell "slow" from "dead".
                    stall_timeout=1200.0,
                    on_line=pip_line,
                    cancel=cancel,
                )
                if result.returncode != 0:
                    raise SeedVRError(f"dependency install failed: {result.stdout[-600:]}")
                (manifest.runtime_root() / ".deps_ready").write_text("ok", encoding="utf-8")
            # The wheels on PyPI are the CPU build. On a machine with an NVIDIA
            # GPU that would silently run 30x slower, so the CUDA build is
            # installed over it — and a failure here is a warning, not a stop:
            # CPU inference is slow, not broken.
            if _cuda_available():
                stage("requirements", 0.9, "安装 CUDA 版 PyTorch")
                _install_cuda_torch(cancel)

            stage("requirements", 1.0, "依赖安装完成")

            files = manifest.required_files(model_id)
            total_weight = sum(entry.size_bytes for entry in files) or 1
            completed_bytes = 0
            for entry in files:
                target = manifest.models_dir() / entry.filename
                if target.is_file() and target.stat().st_size > 0:
                    completed_bytes += entry.size_bytes
                    continue
                stage("model", completed_bytes / total_weight, f"下载模型 {entry.display_name}")
                _download(
                    entry.url, target,
                    expected_sha256=entry.sha256,
                    on_progress=lambda received, total, base=completed_bytes: stage(
                        "model",
                        (base + received) / total_weight,
                        f"下载模型 {(base + received) / 1e9:.1f}GB / {total_weight / 1e9:.1f}GB",
                    ),
                    cancel=cancel,
                )
                completed_bytes += entry.size_bytes
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
            env=_child_env(),
            creationflags=_no_window(),
        )

        progress_reader = ProgressReader()

        def pump() -> None:
            assert process.stdout is not None
            tail: list[str] = []
            for chunk in process.stdout:
                # The CLI writes tqdm bars with carriage returns, so one "line"
                # can contain several updates.
                for line in chunk.replace("\r", "\n").splitlines():
                    tail.append(line.rstrip())
                    if len(tail) > 40:
                        tail.pop(0)
                    update = progress_reader.feed(line)
                    if update is not None:
                        _emit(progress, phase="upscale", progress=update.progress,
                              message=update.message, stage=update.phase)
            self._last_output = tail

        reader = threading.Thread(target=pump, name="seedvr-progress", daemon=True)
        reader.start()

        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                _kill_tree(process)
                reader.join(timeout=2)
                raise _Cancelled()
            try:
                # A timeout here is the normal case: it is how the loop gets to
                # re-check the cancel flag while the model works.
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                continue
        reader.join(timeout=3)

        if process.returncode != 0:
            detail = "\n".join(getattr(self, "_last_output", [])[-15:])
            raise SeedVRError(f"SeedVR2 exited with {process.returncode}: {detail[-600:]}")

        produced = sorted(output_dir.glob("*.mp4"), key=lambda path: path.stat().st_mtime)
        if not produced:
            raise SeedVRError("SeedVR2 produced no output file")

        # The process exited cleanly and the file is there; whatever the CLI's
        # last printed line was, the run is finished.
        _emit(progress, phase="finalize", progress=1.0, message="完成")
        return produced[-1]


def install(model_id: str, *, progress: ProgressCallback | None = None,
            cancel: CancelEvent = None) -> InstallResult:
    return SeedVRRuntime().install(model_id, progress=progress, cancel=cancel)
