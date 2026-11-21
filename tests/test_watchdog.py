"""The watchdog that stops an engine outliving its shell.

The interesting case is not a clean shutdown — stdin EOF already covers that.
It is the shell being killed, where the pipe is never closed and the engine would
otherwise sit on a CUDA context forever.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest

from engine.platform.watchdog import ParentWatchdog, parent_is_alive


class ParentLivenessTests(unittest.TestCase):
    def test_this_process_is_alive(self) -> None:
        self.assertTrue(parent_is_alive(os.getpid()))

    def test_a_finished_process_is_not(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=30)
        # Windows can keep the pid reusable, but immediately after exit the
        # handle is gone; a short grace covers the teardown.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not parent_is_alive(child.pid):
                return
            time.sleep(0.05)
        self.fail("an exited process still reported as alive")

    def test_an_impossible_pid_does_not_raise(self) -> None:
        # Just has to answer; either answer is defensible on a given platform.
        self.assertIsInstance(parent_is_alive(0), bool)


class WatchdogTests(unittest.TestCase):
    def test_starts_and_stops_without_a_parent(self) -> None:
        watchdog = ParentWatchdog(parent_pid=0, interval=0.05)
        watchdog.start()
        watchdog.stop()

    def test_does_not_start_thread_for_an_orphaned_process(self) -> None:
        watchdog = ParentWatchdog(parent_pid=1, interval=0.05)
        watchdog.start()
        self.assertIsNone(watchdog._thread)

    def test_reports_the_parent_it_was_given(self) -> None:
        watchdog = ParentWatchdog(parent_pid=4321, interval=0.05)
        self.assertEqual(watchdog.parent_pid, 4321)


if __name__ == "__main__":
    unittest.main()
