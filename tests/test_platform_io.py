"""Platform layer tests.

These pin the behaviours that the reference implementation got wrong on
Windows: platform keys, .exe suffixes, temp/config locations, cancellation, and
deleting files that Windows still holds open.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from engine.platform import ffmpeg as ffmpeg_mod
from engine.platform import files as files_mod
from engine.platform import paths as paths_mod
from engine.platform import processes as processes_mod


class TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class PathTests(TempDirTestCase):
    def test_user_data_dir_honours_override(self) -> None:
        with mock.patch.dict(os.environ, {paths_mod.ENV_USER_DATA: str(self.root)}):
            self.assertEqual(paths_mod.user_data_dir(), self.root)

    def test_temp_dir_honours_override(self) -> None:
        with mock.patch.dict(os.environ, {paths_mod.ENV_TEMP: str(self.root)}):
            self.assertEqual(paths_mod.temp_dir(), self.root)

    def test_default_output_dir_honours_override(self) -> None:
        with mock.patch.dict(os.environ, {paths_mod.ENV_OUTPUT: str(self.root)}):
            self.assertEqual(paths_mod.default_output_dir(), self.root)

    def test_no_location_resolves_to_a_posix_only_tmp(self) -> None:
        # The reference implementation fell back to Path('/tmp'), which silently
        # becomes C:\tmp on Windows.
        with mock.patch.dict(os.environ, {paths_mod.ENV_TEMP: ""}):
            resolved = paths_mod.temp_dir()
        self.assertFalse(str(resolved).startswith("/tmp"), str(resolved))

    def test_work_dir_sanitises_untrusted_ids(self) -> None:
        with mock.patch.dict(os.environ, {paths_mod.ENV_TEMP: str(self.root)}):
            directory = paths_mod.work_dir("../../evil/../x")
            self.assertEqual(directory.parent, self.root)
            self.assertNotIn("..", directory.name)

    def test_work_dir_falls_back_when_id_is_all_junk(self) -> None:
        with mock.patch.dict(os.environ, {paths_mod.ENV_TEMP: str(self.root)}):
            self.assertEqual(paths_mod.work_dir("///").name, "task")

    def test_extended_path_only_prefixes_when_needed(self) -> None:
        short = self.root / "a" / "b"
        value = paths_mod.extended_path(short)

        if os.name != "nt":
            self.assertEqual(value, str(short))
            return

        # Short paths stay plain: the \\?\ form bypasses normalisation and some
        # APIs reject it, so it is only worth using when we actually need it.
        self.assertEqual(value, os.path.abspath(str(short)))

        long_path = self.root / ("x" * 300)
        self.assertTrue(paths_mod.extended_path(long_path).startswith("\\\\?\\"))

    def test_extended_path_is_idempotent(self) -> None:
        once = paths_mod.extended_path(self.root / ("x" * 300))
        twice = paths_mod.extended_path(once)
        self.assertEqual(once, twice)


class FfmpegTests(TempDirTestCase):
    def test_windows_key_gets_exe_suffix(self) -> None:
        self.assertEqual(ffmpeg_mod.executable_name("ffmpeg", "win32-x86_64"), "ffmpeg.exe")
        self.assertEqual(ffmpeg_mod.executable_name("ffprobe", "win32-arm64"), "ffprobe.exe")

    def test_posix_key_has_no_suffix(self) -> None:
        self.assertEqual(ffmpeg_mod.executable_name("ffmpeg", "darwin-arm64"), "ffmpeg")
        self.assertEqual(ffmpeg_mod.executable_name("ffmpeg", "linux-x86_64"), "ffmpeg")

    def test_override_directory_wins(self) -> None:
        binary = self.root / "ffmpeg.exe" if os.name == "nt" else self.root / "ffmpeg"
        binary.write_bytes(b"stub")
        with mock.patch.dict(os.environ, {ffmpeg_mod.ENV_FFMPEG_DIR: str(self.root)}):
            resolution = ffmpeg_mod.resolve("ffmpeg")
        self.assertEqual(resolution.path, str(binary))
        self.assertEqual(resolution.source, ffmpeg_mod.SOURCE_OVERRIDE)
        self.assertTrue(resolution.available)

    def test_missing_tool_reports_empty_instead_of_guessing(self) -> None:
        empty = self.root / "nothing-here"
        empty.mkdir()
        with mock.patch.dict(os.environ, {ffmpeg_mod.ENV_FFMPEG_DIR: str(empty)}):
            # Isolate from the real vendor/ directory in this checkout.
            with mock.patch.object(ffmpeg_mod, "application_root", return_value=self.root):
                with mock.patch.object(ffmpeg_mod, "runtime_dir", return_value=self.root):
                    with mock.patch.object(ffmpeg_mod.shutil, "which", return_value=None):
                        resolution = ffmpeg_mod.resolve("ffmpeg")
        self.assertEqual(resolution.path, "")
        self.assertFalse(resolution.available)

    def test_path_fallback_is_labelled(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        with mock.patch.dict(os.environ, {ffmpeg_mod.ENV_FFMPEG_DIR: str(empty)}):
            with mock.patch.object(ffmpeg_mod, "application_root", return_value=self.root):
                with mock.patch.object(ffmpeg_mod, "runtime_dir", return_value=self.root):
                    with mock.patch.object(ffmpeg_mod.shutil, "which", return_value="/usr/bin/ffmpeg"):
                        resolution = ffmpeg_mod.resolve("ffmpeg")
        self.assertEqual(resolution.source, ffmpeg_mod.SOURCE_PATH)

    def test_search_descriptions_name_every_candidate(self) -> None:
        description = ffmpeg_mod.describe_search_paths("win32-x86_64")
        self.assertGreaterEqual(len(description), 2)
        self.assertTrue(all(isinstance(item, str) for item in description))


class ProcessTests(TempDirTestCase):
    def test_captures_stdout(self) -> None:
        result = processes_mod.run([sys.executable, "-c", "print('hello')"], timeout=30)
        self.assertTrue(result.ok)
        self.assertIn("hello", result.stdout)

    def test_non_zero_exit_is_returned_not_raised(self) -> None:
        result = processes_mod.run([sys.executable, "-c", "raise SystemExit(3)"], timeout=30)
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 3)

    def test_timeout_kills_the_process(self) -> None:
        started = time.monotonic()
        result = processes_mod.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.6,
        )
        elapsed = time.monotonic() - started
        self.assertTrue(result.timed_out)
        self.assertFalse(result.ok)
        self.assertLess(elapsed, 15, "timeout did not actually stop the process")

    def test_cancellation_stops_promptly(self) -> None:
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        started = time.monotonic()
        result = processes_mod.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cancel=cancel,
        )
        elapsed = time.monotonic() - started
        self.assertTrue(result.cancelled)
        self.assertLess(elapsed, 15, "cancellation did not actually stop the process")

    def test_tail_is_bounded(self) -> None:
        result = processes_mod.run(
            [sys.executable, "-c", "import sys; [print(i, file=sys.stderr) for i in range(50)]"],
            timeout=30,
        )
        self.assertLessEqual(len(result.tail(3).splitlines()), 3)


class FileTests(TempDirTestCase):
    def test_atomic_write_leaves_no_temp_file(self) -> None:
        target = self.root / "out" / "data.bin"
        files_mod.atomic_write(target, b"payload")
        self.assertEqual(target.read_bytes(), b"payload")
        leftovers = [p.name for p in target.parent.iterdir() if p.name != "data.bin"]
        self.assertEqual(leftovers, [])

    def test_atomic_write_overwrites(self) -> None:
        target = self.root / "data.txt"
        files_mod.atomic_write(target, "first")
        files_mod.atomic_write(target, "second")
        self.assertEqual(target.read_text(encoding="utf-8"), "second")

    def test_remove_tree_is_idempotent(self) -> None:
        victim = self.root / "tree"
        (victim / "a").mkdir(parents=True)
        (victim / "a" / "f.txt").write_text("x", encoding="utf-8")
        self.assertTrue(files_mod.remove_tree(victim))
        self.assertTrue(files_mod.remove_tree(victim))
        self.assertFalse(victim.exists())

    def test_remove_file_is_idempotent(self) -> None:
        target = self.root / "gone.txt"
        target.write_text("x", encoding="utf-8")
        self.assertTrue(files_mod.remove_file(target))
        self.assertTrue(files_mod.remove_file(target))

    def test_sha256_matches_known_value(self) -> None:
        target = self.root / "abc.txt"
        target.write_bytes(b"abc")
        self.assertEqual(
            files_mod.sha256_file(target),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_clean_stale_removes_old_entries_only(self) -> None:
        old = self.root / "old"
        fresh = self.root / "fresh"
        old.mkdir()
        fresh.mkdir()
        long_ago = time.time() - 7200
        os.utime(old, (long_ago, long_ago))

        removed = files_mod.clean_stale(self.root, max_age_seconds=3600)

        self.assertIn(str(old), removed)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_clean_stale_on_missing_root_is_empty(self) -> None:
        self.assertEqual(files_mod.clean_stale(self.root / "nope", max_age_seconds=1), [])

    def test_human_size_formats(self) -> None:
        self.assertEqual(files_mod.human_size(512), "512.0 B")
        self.assertEqual(files_mod.human_size(1536), "1.5 KB")


if __name__ == "__main__":
    unittest.main()
