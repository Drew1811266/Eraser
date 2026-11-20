"""Platform key parity.

These values are not arbitrary: desktop/electron/services/ffmpeg.ts produces the
same strings, and both ends resolve assets from vendor/ffmpeg/<key>/. A drift
here is exactly the defect that made the bundled ffmpeg unreachable on Windows
in the reference repository.
"""

from __future__ import annotations

import unittest

from engine.platform import current_platform_key, normalize_arch


class NormalizeArchTests(unittest.TestCase):
    def test_vendor_spellings_collapse(self) -> None:
        for raw in ("arm64", "aarch64", "ARM64"):
            self.assertEqual(normalize_arch(raw), "arm64")
        for raw in ("x86_64", "AMD64", "x64", " amd64 "):
            self.assertEqual(normalize_arch(raw), "x86_64")

    def test_unknown_values_pass_through_lowercased(self) -> None:
        self.assertEqual(normalize_arch("riscv64"), "riscv64")
        self.assertEqual(normalize_arch(None), "unknown")
        self.assertEqual(normalize_arch(""), "unknown")


class PlatformKeyTests(unittest.TestCase):
    def test_matches_electron_keys(self) -> None:
        cases = [
            (("darwin", "arm64"), "darwin-arm64"),
            (("darwin", "x86_64"), "darwin-x86_64"),
            # Electron spells this win32-x86_64, not win32-x64.
            (("win32", "AMD64"), "win32-x86_64"),
            (("win32", "x86_64"), "win32-x86_64"),
            (("win32", "arm64"), "win32-arm64"),
            (("linux", "x86_64"), "linux-x86_64"),
        ]
        for (system, machine), expected in cases:
            with self.subTest(system=system, machine=machine):
                self.assertEqual(current_platform_key(system, machine), expected)

    def test_windows_system_aliases_map_to_win32(self) -> None:
        for system in ("win32", "windows", "Windows", "win"):
            with self.subTest(system=system):
                self.assertTrue(current_platform_key(system, "AMD64").startswith("win32-"))

    def test_linux_kernel_name_is_normalized(self) -> None:
        self.assertEqual(current_platform_key("linux2", "x86_64"), "linux-x86_64")

    def test_unknown_system_is_reported_not_hidden(self) -> None:
        self.assertEqual(current_platform_key("freebsd", "amd64"), "freebsd-x86_64")

    def test_real_host_produces_a_usable_key(self) -> None:
        key = current_platform_key()
        self.assertRegex(key, r"^[a-z0-9]+-[a-z0-9_]+$")


if __name__ == "__main__":
    unittest.main()
