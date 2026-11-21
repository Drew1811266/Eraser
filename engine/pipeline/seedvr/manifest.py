"""SeedVR2: what it is, where it goes, and what it needs.

SeedVR2 is not another ONNX file. It is a one-step diffusion model that needs a
PyTorch runtime, so the reference implementation keeps it in its own Python 3.12
virtualenv next to a pinned checkout of the upstream inference CLI, and downloads
the weights on demand — a ~2GB download that has no business being inside an
installer.

This module is only the *description*: pinned commits and checksums, the paths
those artefacts live at, and the sizes and memory floors used to explain to a
user whether their machine can run it. Nothing here downloads or executes.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

#: The upstream project the CLI comes from. Pinned: `main` drifts, and a runtime
#: that silently changes behaviour between installs is not a runtime.
REPO_REF = "4490bd1f482e026674543386bb2a4d176da245b9"
REPO_URL = f"https://codeload.github.com/numz/ComfyUI-SeedVR2_VideoUpscaler/tar.gz/{REPO_REF}"

ENGINE_ID = "seedvr2"
ENGINE_DISPLAY_NAME = "SeedVR2"
UPSCALE_ENGINES = (ENGINE_ID,)

DEFAULT_MODEL_ID = "seedvr2_3b_q4_k_m_gguf"


@dataclass(frozen=True)
class SeedVRModelSpec:
    model_id: str
    display_name: str
    filename: str
    url: str
    sha256: str
    size_bytes: int
    min_memory_gb: float
    install_hint: str


MODEL_SPECS: dict[str, SeedVRModelSpec] = {
    "seedvr2_3b_q4_k_m_gguf": SeedVRModelSpec(
        model_id="seedvr2_3b_q4_k_m_gguf",
        display_name="SeedVR2 3B Q4_K_M (GGUF)",
        filename="seedvr2_ema_3b-Q4_K_M.gguf",
        url="https://huggingface.co/AInVFX/SeedVR2_comfyUI/resolve/main/seedvr2_ema_3b-Q4_K_M.gguf",
        sha256="e665e3909de1a8c88a69c609bca9d43ff5a134647face2ce4497640cc3597f0e",
        size_bytes=2_100_000_000,
        min_memory_gb=12.0,
        install_hint="默认推荐：更省内存，适合 16GB/24GB 显存日常使用。",
    ),
    "seedvr2_3b_q8_0_gguf": SeedVRModelSpec(
        model_id="seedvr2_3b_q8_0_gguf",
        display_name="SeedVR2 3B Q8_0 (GGUF)",
        filename="seedvr2_ema_3b-Q8_0.gguf",
        url="https://huggingface.co/AInVFX/SeedVR2_comfyUI/resolve/main/seedvr2_ema_3b-Q8_0.gguf",
        sha256="be0d60083a2051a265eb4b77f28edf494e6db67ffc250216f32b72292e5cbd96",
        size_bytes=3_600_000_000,
        min_memory_gb=16.0,
        install_hint="画质更高但更重，建议 24GB 以上显存。",
    ),
}

UPSCALE_MODELS = tuple(MODEL_SPECS)

#: Python the upstream CLI is written for. 3.13 tends to work as well, but the
#: reference pins 3.12 and mixing versions across a 40-package install is how
#: "it worked yesterday" happens.
PREFERRED_PYTHON = (3, 12)


def runtime_root() -> Path:
    """Where the isolated runtime lives.

    Next to the models when running from source; under the user's application
    data when installed, because `Program Files` is not writable and a 2GB
    download must survive a reinstall.
    """
    override = os.environ.get("ERASER_SEEDVR_ROOT")
    if override:
        return Path(override)

    data_dir = os.environ.get("ERASER_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "seedvr"

    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "Eraser" / "seedvr"

    return Path(__file__).resolve().parents[3] / "models" / "runtime" / "seedvr-py312"


def repo_dir() -> Path:
    return runtime_root() / "seedvr2_repo"


def venv_dir() -> Path:
    return runtime_root() / "venv"


def venv_python() -> Path:
    if sys.platform == "win32":
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def models_dir() -> Path:
    return runtime_root() / "models" / "SEEDVR2"


def model_path(model_id: str) -> Path:
    return models_dir() / get_spec(model_id).filename


def inference_script() -> Path:
    return repo_dir() / "inference_cli.py"


def requirements_file() -> Path:
    return repo_dir() / "requirements.txt"


def state_file() -> Path:
    return runtime_root() / "install_state.json"


def get_spec(model_id: str) -> SeedVRModelSpec:
    key = str(model_id or "").strip()
    if key not in MODEL_SPECS:
        raise KeyError(f"unknown SeedVR2 model id: {key}")
    return MODEL_SPECS[key]


def model_installed(model_id: str) -> bool:
    path = model_path(model_id)
    return path.is_file() and path.stat().st_size > 0
