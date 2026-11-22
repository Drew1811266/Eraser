"""The NDJSON wire format between the engine and the Florence worker.

Torch-free and importable from the engine on purpose: the protocol is the part
that can be wrong without anyone noticing, so it must be unit-testable without a
444 MB model or a GPU.
"""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import BBox
from engine.pipeline.detect.worker_protocol import (
    DEFAULT_BATCH,
    DEFAULT_PROMPT,
    collect_frames,
    encode_job,
    parse_line,
)


class JobTests(unittest.TestCase):
    def test_job_contains_weights_and_images(self) -> None:
        job = encode_job("C:/w", [(0, "a.jpg"), (15, "b.jpg")], batch=4)
        self.assertEqual(job["weights"], "C:/w")
        self.assertEqual(job["prompt"], DEFAULT_PROMPT)
        self.assertEqual(job["batch"], 4)
        self.assertEqual([item["frame"] for item in job["images"]], [0, 15])
        self.assertEqual(job["images"][0]["path"], "a.jpg")

    def test_batch_defaults_and_is_a_positive_int(self) -> None:
        job = encode_job("w", [(0, "a.jpg")], batch=0)
        self.assertEqual(job["batch"], DEFAULT_BATCH)


class ParseTests(unittest.TestCase):
    def test_reads_a_frame_line(self) -> None:
        frame = parse_line('{"type":"frame","frame":3,"boxes":[[1,2,3,4]]}')
        self.assertEqual(frame["type"], "frame")
        self.assertEqual(frame["boxes"], [[1, 2, 3, 4]])

    def test_blank_line_is_an_error_payload(self) -> None:
        self.assertEqual(parse_line("")["type"], "error")

    def test_broken_json_is_an_error_payload(self) -> None:
        self.assertEqual(parse_line("{nope")["type"], "error")

    def test_json_without_type_is_an_error_payload(self) -> None:
        self.assertEqual(parse_line('{"frame": 1}')["type"], "error")


class CollectTests(unittest.TestCase):
    def test_collects_boxes_by_frame(self) -> None:
        lines = [
            '{"type":"frame","frame":0,"boxes":[[1,2,3,4]]}',
            '{"type":"progress","done":1,"total":2}',
            '{"type":"frame","frame":15,"boxes":[]}',
        ]
        boxes = collect_frames(lines, total=2)
        self.assertEqual(boxes[0], [BBox(1, 2, 2, 2)])
        self.assertEqual(boxes[15], [])

    def test_xyxy_becomes_xywh(self) -> None:
        boxes = collect_frames(['{"type":"frame","frame":0,"boxes":[[10,20,40,60]]}'], total=1)
        self.assertEqual(boxes[0][0], BBox(10, 20, 30, 40))

    def test_degenerate_boxes_are_dropped(self) -> None:
        line = '{"type":"frame","frame":0,"boxes":[[10,10,10,10],[50,50,40,40],[1,2,3]]}'
        self.assertEqual(collect_frames([line], total=1)[0], [])

    def test_frames_missing_from_the_stream_are_absent(self) -> None:
        boxes = collect_frames(['{"type":"frame","frame":0,"boxes":[]}'], total=2)
        self.assertEqual(sorted(boxes), [0])


if __name__ == "__main__":
    unittest.main()
