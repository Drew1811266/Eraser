"""Whether SeedVR2 can run here, and if not, why.

The reference app answers this question in the capability probe the UI calls
before offering the engine, and the answer has to be specific: "not available"
with no reason makes a user reinstall the app.

There are five ways it can be unavailable, and they need different fixes:

    no_python   - no Python 3.12 interpreter on the machine
    no_runtime  - the isolated virtualenv was never installed
    no_repo     - the pinned inference CLI is missing
    no_model    - the weights for the selected model were not downloaded
    no_memory   - the machine has less unified/explicit memory than the model needs
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.pipeline.logging import logger
from engine.pipeline.seedvr import manifest


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str = ""
    detail: str = ""
    #: What to do about it, in one sentence the UI can show as-is.
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"available": self.available}
        if not self.available:
            payload.update({"reason": self.reason, "detail": self.detail, "hint": self.hint})
        return payload


@dataclass
class RuntimeState:
    phase: str = "absent"
    progress: float = 0.0
    message: str = ""
    error: str = ""
    model_id: str = ""
    python: str = ""
    installed_models: list[str] = field(default_factory=list)


def read_state() -> RuntimeState:
    path = manifest.state_file()
    if not path.is_file():
        return RuntimeState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable SeedVR2 state file: %s", exc)
        return RuntimeState()
    return RuntimeState(
        phase=str(payload.get("phase") or "absent"),
        progress=float(payload.get("progress") or 0.0),
        message=str(payload.get("message") or ""),
        error=str(payload.get("error") or ""),
        model_id=str(payload.get("model_id") or ""),
        python=str(payload.get("python") or ""),
        installed_models=list(payload.get("installed_models") or []),
    )


def write_state(state: RuntimeState) -> None:
    path = manifest.state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "phase": state.phase,
                    "progress": round(state.progress, 4),
                    "message": state.message,
                    "error": state.error,
                    "model_id": state.model_id,
                    "python": state.python,
                    "installed_models": sorted(set(state.installed_models)),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("cannot write the SeedVR2 state file: %s", exc)


def find_python(major: int = 3, minor: int = 12) -> str:
    """Locate an interpreter of the version the upstream CLI targets.

    Checked in order: the explicit override, an already-installed runtime venv,
    the Windows launcher, then common per-user install locations. `python` on
    PATH is deliberately not first — it is usually the newest version, and the
    pinned requirements are the reason for pinning.
    """
    override = os.environ.get("ERASER_SEEDVR_PYTHON")
    if override and Path(override).is_file():
        return override

    venv_python = manifest.venv_python()
    if venv_python.is_file():
        return str(venv_python)

    candidates: list[str] = []
    if sys.platform == "win32":
        launcher = shutil.which("py")
        if launcher:
            try:
                completed = subprocess.run(
                    [launcher, f"-{major}.{minor}", "-c", "import sys; print(sys.executable)"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=20, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode == 0:
                    found = completed.stdout.strip()
                    if found and Path(found).is_file():
                        candidates.append(found)
            except (OSError, subprocess.SubprocessError):
                pass
        for root in (
            os.environ.get("LOCALAPPDATA", ""),
            os.environ.get("APPDATA", ""),
            os.environ.get("ProgramFiles", ""),
        ):
            if not root:
                continue
            candidates.append(str(Path(root) / "Programs" / "Python" / f"Python{major}{minor}" / "python.exe"))
            # `uv` keeps a patch version in the directory name, so glob it.
            for pattern in (
                f"uv/python/cpython-{major}.{minor}.*-windows-x86_64-none/python.exe",
                f"uv/python/cpython-{major}.{minor}.*-windows-x86_64-none/install/python.exe",
            ):
                candidates.extend(sorted([str(path) for path in Path(root).glob(pattern)], reverse=True))

        # `uv python find` knows about its own downloads and shims.
        uv_executable = shutil.which("uv")
        if uv_executable:
            try:
                completed = subprocess.run(
                    [uv_executable, "python", "find", f"{major}.{minor}"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=20, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                found = completed.stdout.strip()
                if completed.returncode == 0 and found and Path(found).is_file():
                    candidates.insert(0, found)
            except (OSError, subprocess.SubprocessError):
                pass
    else:
        for name in (f"python{major}.{minor}", f"python{major}", "python3"):
            found = shutil.which(name)
            if found:
                candidates.append(found)

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return ""


def runtime_installed() -> bool:
    return manifest.venv_python().is_file() and manifest.inference_script().is_file()


def check(model_id: str, *, total_memory_gb: float | None = None) -> Availability:
    """Can the given model run here? The answer is the UI's whole story."""
    try:
        manifest.get_spec(model_id)
    except KeyError:
        return Availability(False, "unknown_model", str(model_id), "选择一个支持的 SeedVR2 模型。")

    python = find_python()
    if not python:
        return Availability(
            False, "no_python", "需要 Python 3.12",
            "安装 Python 3.12 后重试；运行时会安装在用户目录，不影响系统 Python。",
        )

    if not manifest.venv_python().is_file():
        return Availability(
            False, "no_runtime", "SeedVR2 运行时未安装",
            "在放大页点击「下载模型」安装运行时（约 2–3GB，包含 PyTorch）。",
        )

    if not manifest.inference_script().is_file():
        return Availability(
            False, "no_repo", "SeedVR2 推理脚本缺失",
            "重新安装运行时以修复。",
        )

    if not manifest.model_installed(model_id):
        return Availability(
            False, "no_model", f"模型未下载：{manifest.get_spec(model_id).display_name}",
            "在放大页点击「下载模型」。",
        )

    spec = manifest.get_spec(model_id)
    if total_memory_gb is not None and total_memory_gb < spec.min_memory_gb:
        return Availability(
            False, "no_memory",
            f"需要约 {spec.min_memory_gb:.0f}GB 可用显存，当前 {total_memory_gb:.1f}GB",
            "换用量化更小的模型，或用 Real-ESRGAN 引擎。",
        )

    return Availability(True)


def describe() -> dict[str, Any]:
    """The runtime block the capability probe hands to the UI."""
    state = read_state()
    python = find_python()
    return {
        "engine": manifest.ENGINE_ID,
        "display_name": manifest.ENGINE_DISPLAY_NAME,
        "runtime_installed": runtime_installed(),
        "python": python,
        "root": str(manifest.runtime_root()),
        "phase": state.phase,
        "progress": round(state.progress, 4),
        "message": state.message,
        "error": state.error,
        "installed_models": [model for model in manifest.UPSCALE_MODELS if manifest.model_installed(model)],
        "models": [
            {
                "model_id": spec.model_id,
                "display_name": spec.display_name,
                "installed": manifest.model_installed(spec.model_id),
                "size_bytes": spec.size_bytes,
                "min_memory_gb": spec.min_memory_gb,
                "hint": spec.install_hint,
            }
            for spec in (manifest.MODEL_SPECS[key] for key in manifest.UPSCALE_MODELS)
        ],
    }
