"""Task lifecycle: the notification a finished task emits is the whole story.

The shell learns about progress from notifications, not by polling — polling is
only the resync path. So the final notification must carry the result, otherwise
a client that trusts it sees "success" with no output path and has no reason to
ask again. That exact bug shipped once; this pins it.
"""

from __future__ import annotations

import unittest

from engine.tasks import TaskManager


class TaskSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        # Matches the real emit signature: (event name, payload).
        self.events: list[dict] = []
        self.manager = TaskManager(lambda _event, payload: self.events.append(payload))
        self.task = self.manager.create("process")

    def test_start_emits_nothing_until_something_changes(self) -> None:
        self.assertEqual(self.events, [])
        self.assertEqual(self.task.state, "running")

    def test_progress_is_monotonic(self) -> None:
        self.manager.update(self.task, progress=0.5)
        self.manager.update(self.task, progress=0.2)
        self.assertEqual(self.task.progress, 0.5)

    def test_finish_notification_carries_the_result(self) -> None:
        self.manager.finish(self.task, result={"output_path": "C:/out/clip.mp4"})

        self.assertTrue(self.events, "finish must emit a notification")
        payload = self.events[-1]
        self.assertEqual(payload["state"], "success")
        self.assertEqual(payload["result"], {"output_path": "C:/out/clip.mp4"})
        self.assertNotIn("error", payload)

    def test_finish_notification_carries_the_error(self) -> None:
        self.manager.finish(self.task, error="MODEL_MISSING: big-lama.onnx")

        payload = self.events[-1]
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["error"], "MODEL_MISSING: big-lama.onnx")
        self.assertNotIn("result", payload)

    def test_snapshot_matches_the_notification(self) -> None:
        self.manager.update(self.task, progress=0.4, processed=4, total=10)
        snapshot = self.manager.snapshot(self.task)
        self.assertEqual(snapshot, self.events[-1])

    def test_cancelled_task_reports_cancelled(self) -> None:
        self.manager.cancel(self.task.task_id)
        self.manager.finish(self.task, error="Cancelled")
        self.assertEqual(self.events[-1]["state"], "cancelled")

    def test_eta_needs_a_rate_to_estimate(self) -> None:
        self.assertIsNone(self.manager.snapshot(self.task)["eta_seconds"])
        self.manager.update(self.task, processed=2, total=10)
        self.assertIsNotNone(self.manager.snapshot(self.task)["eta_seconds"])


if __name__ == "__main__":
    unittest.main()
