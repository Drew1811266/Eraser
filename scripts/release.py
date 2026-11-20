"""Build both installers from one code base.

    dist/installer/Eraser-Setup-<version>.exe       CPU,  ~600 MB
    dist/installer/Eraser-Setup-GPU-<version>.exe   +CUDA, ~2.3 GB

The split exists because the CUDA runtime is 1.2 GB of DLLs that only help
NVIDIA machines; shipping one installer four times larger than necessary for
everyone is worse than letting the user pick. The app itself is identical and
falls back to CPU when the CUDA DLLs are absent.

    python scripts/release.py            # both
    python scripts/release.py --cpu      # just the small one
    python scripts/release.py --gpu      # just the big one
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# The Windows console defaults to a legacy code page; electron-builder's output
# is UTF-8 and its progress glyphs are not representable there.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
INSTALLER_DIR = ROOT / "dist" / "installer"
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")


def run(command: list[str], cwd: Path = ROOT) -> None:
    started = time.monotonic()
    print("\n$ " + " ".join(command))
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env(),
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        print(output)
        raise SystemExit(f"failed ({completed.returncode}): {' '.join(command)}")
    for line in output.splitlines():
        if line.strip():
            print(f"  {line}")
    print(f"  took {time.monotonic() - started:.0f}s")


def app_version() -> str:
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    return str(package["version"])


def child_env() -> dict[str, str]:
    """Keep every temporary file inside the repository.

    electron-builder takes a lock file in the system temp directory while it
    downloads toolchains; under a restricted session that write can be denied
    (EPERM on mkdir) even though the user's own temp directory is writable.
    Pointing TMP/TEMP and the cache at `.cache/` removes the dependency.
    """
    scratch = ROOT / ".cache" / "build-tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    cache = ROOT / ".cache" / "electron-builder"
    cache.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ELECTRON_RUN_AS_NODE")}
    env.update(
        {
            "TMPDIR": str(scratch),
            "TMP": str(scratch),
            "TEMP": str(scratch),
            "ELECTRON_BUILDER_CACHE": str(cache),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def node_binary() -> str:
    found = shutil.which("node")
    if not found:
        raise SystemExit("node is not on PATH")
    return found


def build(*, cuda: bool) -> Path:
    label = "GPU" if cuda else "CPU"
    print(f"\n=== {label} build ===")

    stage = [PYTHON, str(ROOT / "scripts" / "stage-dist.py")]
    if cuda:
        stage.append("--cuda")
    run(stage)

    # Drive the CLI with node directly: `npx` is a shell wrapper, and spawning it
    # from subprocess is where the "command not found" surprises come from.
    shutil.rmtree(INSTALLER_DIR / "win-unpacked", ignore_errors=True)
    cli = DESKTOP / "node_modules" / "electron-builder" / "cli.js"
    command = [node_binary(), str(cli), "--win", "--x64"]
    if cuda:
        command.append("--config.nsis.artifactName=Eraser-Setup-GPU-${version}.${ext}")
    run(command, cwd=DESKTOP)

    suffix = "-GPU" if cuda else ""
    return INSTALLER_DIR / f"Eraser-Setup{suffix}-{app_version()}.exe"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu", action="store_true", help="only the CPU installer")
    parser.add_argument("--gpu", action="store_true", help="only the GPU installer")
    args = parser.parse_args()

    explicit = args.cpu or args.gpu
    want_cpu = args.cpu or not explicit
    want_gpu = args.gpu or not explicit

    print("== icon ==")
    run([PYTHON, str(ROOT / "scripts" / "make-icon.py")])

    print("\n== renderer + main process ==")
    # `npm` is a shell wrapper on Windows; spawning it needs shell=True.
    completed = subprocess.run("npm run build", cwd=str(DESKTOP), check=False, shell=True)
    if completed.returncode != 0:
        raise SystemExit("desktop build failed")

    artifacts: list[Path] = []
    if want_cpu:
        artifacts.append(build(cuda=False))
    if want_gpu:
        artifacts.append(build(cuda=True))

    print("\n== artifacts ==")
    missing = 0
    for path in artifacts:
        if path.is_file():
            print(f"  {path.relative_to(ROOT)}  ({path.stat().st_size / 1e6:.0f} MB)")
        else:
            print(f"  MISSING: {path.relative_to(ROOT)}")
            missing += 1
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
