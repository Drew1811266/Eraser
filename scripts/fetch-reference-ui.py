"""Fetch the reference frontend sources so the port can copy them locally.

The reference app's real UI is a React app (`frontend/src`) served by pywebview.
Only the Python half was snapshotted earlier, which is why the Windows shell was
designed from scratch instead of ported. This pulls the whole frontend down so
the same components, styles and copy can be reused.

    python scripts/fetch-reference-ui.py
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / ".reference" / "frontend"
BASE = "https://raw.githubusercontent.com/Drew1811266/Mac-Remover/main/frontend"

FILES = [
    "package.json",
    "tsconfig.json",
    "index.html",
    "vite.config.ts",
    "src/App.tsx",
    "src/main.tsx",
    "src/styles.css",
    "src/vite-env.d.ts",
    "src/views/ManualView.tsx",
    "src/views/ProcessView.tsx",
    "src/views/ResultView.tsx",
    "src/views/SettingsView.tsx",
    "src/views/UpscaleView.tsx",
    "src/components/FrameComparePreview.tsx",
    "src/workspace/AnnotationWorkspace.tsx",
    "src/services/desktop.ts",
    "src/material/index.tsx",
    "src/material/snackbar.tsx",
    "src/material/material-elements.d.ts",
    "src/store/app.ts",
    "src/store/workspace.ts",
    "src/design/theme.ts",
    "src/i18n/messages.ts",
    "src/i18n/useI18n.ts",
    "src/types/annotation.ts",
    "src/types/app.ts",
]


def main() -> int:
    failures = 0
    for relative in FILES:
        url = f"{BASE}/{relative}"
        destination = TARGET / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "eraser-port/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                destination.write_bytes(response.read())
            print(f"  {relative}  ({destination.stat().st_size} bytes)")
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            failures += 1
            print(f"  FAILED {relative}: {exc}")

    print()
    print(f"{len(FILES) - failures}/{len(FILES)} files fetched into {TARGET.relative_to(ROOT)}")

    package = TARGET / "package.json"
    if package.is_file():
        data = json.loads(package.read_text(encoding="utf-8"))
        print("reference dependencies:")
        for name, version in {**data.get("dependencies", {}), **data.get("devDependencies", {})}.items():
            print(f"  {name} {version}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
