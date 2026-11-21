"""Turn the upstream CLI's narration into progress.

The pinned CLI does not emit a machine-readable progress protocol. It prints a
banner, four numbered phases, per-batch lines and occasionally a tqdm bar, all
decorated with emoji. The reference implementation parses `PROGRESS chunk=i/n`
lines, which this version does not produce — so this module does the honest
alternative: read the phases it does print.

What that means for the UI: the bar moves in four steps, not continuously. That
is the truth of what the process reports, and a bar that jumps is more honest
than one that creeps forward on a timer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PHASE_LINE = re.compile(r"Phase\s+(\d+)\s*:\s*(.+?)\s*[━─-]+\s*$")
BATCH_LINE = re.compile(r"batch\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)
TQDM_LINE = re.compile(r"(?P<label>[\w ]+):\s*(?P<percent>\d+)%\|[^|]*\|\s*(?P<done>\d+)/(?P<total>\d+)")
DONE_LINE = re.compile(r"All upscaling processes completed", re.IGNORECASE)
SAVED_LINE = re.compile(r"Output saved to:", re.IGNORECASE)

#: The four phases the CLI announces, as fractions of the run. Weighted by how
#: long they actually take on a GPU: the DiT pass dominates.
PHASE_WEIGHTS = {
    1: (0.00, 0.10),  # VAE encoding
    2: (0.10, 0.80),  # DiT upscaling
    3: (0.80, 0.95),  # VAE decoding
    4: (0.95, 1.00),  # post-processing
}

PHASE_LABELS = {
    1: "编码",
    2: "推理中",
    3: "解码",
    4: "后处理",
}

CHUNK_LINE = re.compile(r"^PROGRESS\s+chunk=(\d+)\s*/\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Update:
    progress: float
    message: str
    phase: str


class ProgressReader:
    """Stateful: the CLI's lines arrive one at a time, and progress only grows."""

    def __init__(self) -> None:
        self.phase = 0
        self.value = 0.0
        self.finished = False

    def feed(self, line: str) -> Update | None:
        text = line.strip()
        if not text:
            return None

        # Some builds do speak the documented protocol; prefer it when present.
        chunk = CHUNK_LINE.match(text)
        if chunk:
            done, total = int(chunk.group(1)), max(1, int(chunk.group(2)))
            return self._advance(done / total * 0.95, f"推理中 {done}/{total}", "inference")

        phase = PHASE_LINE.search(text)
        if phase:
            index = int(phase.group(1))
            start, _end = PHASE_WEIGHTS.get(index, (0.0, 0.95))
            self.phase = index
            return self._advance(start, PHASE_LABELS.get(index, phase.group(2)), f"phase{index}")

        batch = BATCH_LINE.search(text)
        if batch and self.phase:
            done, total = int(batch.group(1)), max(1, int(batch.group(2)))
            start, end = PHASE_WEIGHTS.get(self.phase, (0.0, 0.95))
            span = end - start
            value = start + span * (done / total)
            return self._advance(value, f"{PHASE_LABELS.get(self.phase, '处理')} {done}/{total}",
                                 f"phase{self.phase}")

        bar = TQDM_LINE.search(text)
        if bar and self.phase:
            start, end = PHASE_WEIGHTS.get(self.phase, (0.0, 0.95))
            share = int(bar.group("done")) / max(1, int(bar.group("total")))
            return self._advance(start + (end - start) * share, f"{PHASE_LABELS.get(self.phase, '处理')}",
                                 f"phase{self.phase}")

        if SAVED_LINE.search(text):
            return self._advance(0.99, "写出文件", "writing")

        if DONE_LINE.search(text):
            self.finished = True
            return self._advance(1.0, "完成", "done")

        return None

    def _advance(self, value: float, message: str, phase: str) -> Update | None:
        clamped = max(0.0, min(1.0, value))
        # A bar that goes backwards reads as a restart, which is worse than a
        # bar that stands still.
        if clamped <= self.value and not (clamped >= 1.0 and self.value >= 1.0):
            return None
        self.value = clamped
        return Update(progress=clamped, message=message, phase=phase)
