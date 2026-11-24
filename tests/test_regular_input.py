"""Reject special files before parsing, sharing the check across storage modes."""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from joinforecast import InputError, read_csv
from joinforecast.disk import sqlite_csv


class RegularInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.csv = self.root / "input.csv"
        self.csv.write_bytes(b"\xef\xbb\xbfid,v\r\na,1\r\na,2\r\n")
        self.indexes = self.root / "indexes"
        self.indexes.mkdir()

    def load(self, path, storage):
        if storage == "sqlite":
            with sqlite_csv(path, ["id"], temp_dir=self.indexes) as result:
                return result.rows, list(result.counts.items())
        result = read_csv(path, ["id"])
        return result.rows, list(result.counts.items())

    @unittest.skipUnless(hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK"), "POSIX FIFO")
    def test_writerless_fifo_cli_both_sides_and_storage_modes(self):
        fifo = self.root / "fifo.csv"
        os.mkfifo(fifo)
        for storage in ("memory", "sqlite"):
            for side in (0, 1):
                inputs = [str(self.csv), str(self.csv)]
                inputs[side] = str(fifo)
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "joinforecast",
                        *inputs,
                        "--left-key",
                        "id",
                        "--storage",
                        storage,
                        *(["--temp-dir", str(self.indexes)] if storage == "sqlite" else []),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("regular file", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(list(self.indexes.iterdir()), [])

    @unittest.skipUnless(hasattr(os, "mkfifo") and hasattr(os, "O_NONBLOCK"), "POSIX FIFO")
    def test_library_fifo_and_fifo_symlink_deadline(self):
        fifo = self.root / "fifo.csv"
        os.mkfifo(fifo)
        alias = self.root / "alias.csv"
        alias.symlink_to(fifo)
        program = "import sys;from joinforecast import read_csv,InputError\ntry:read_csv(sys.argv[1],['id'])\nexcept InputError as e:print(e);sys.exit(2)"
        for path in (fifo, alias):
            result = subprocess.run(
                [sys.executable, "-c", program, str(path)],
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("regular file", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_descriptor_controls_admission_and_all_storage_is_cleaned(self):
        original = os.fstat
        for storage in ("memory", "sqlite"):
            with (
                patch(
                    "joinforecast.os.fstat",
                    side_effect=lambda fd: os.stat_result((stat.S_IFIFO, *original(fd)[1:])),
                ),
                self.assertRaisesRegex(InputError, "regular file"),
            ):
                self.load(self.csv, storage)
            self.assertEqual(list(self.indexes.iterdir()), [])

    def test_nonblocking_flag_and_normal_bom_crlf_bytes(self):
        original = os.open
        with patch("joinforecast.os.open", wraps=original) as opened:
            self.assertEqual(self.load(self.csv, "memory"), (2, [(("a",), 2)]))
        self.assertEqual(opened.call_count, 1)
        if hasattr(os, "O_NONBLOCK"):
            self.assertTrue(opened.call_args.args[1] & os.O_NONBLOCK)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks")
    def test_regular_symlinks_allowed(self):
        alias = self.root / "alias.csv"
        try:
            alias.symlink_to(self.csv)
        except OSError as exc:
            self.skipTest(str(exc))
        for storage in ("memory", "sqlite"):
            self.assertEqual(self.load(alias, storage), self.load(self.csv, storage))

    def test_missing_directory_and_device_rejected_cleanly(self):
        paths = [self.root, self.root / "missing"]
        if Path("/dev/null").exists():
            paths.append(Path("/dev/null"))
        for storage in ("memory", "sqlite"):
            for path in paths:
                with self.assertRaises(InputError):
                    self.load(path, storage)
                self.assertEqual(list(self.indexes.iterdir()), [])

    def test_repeated_refusal_and_descriptor_check_error_do_not_leak(self):
        descriptor_dir = Path("/proc/self/fd")
        if not descriptor_dir.exists():
            self.skipTest("requires descriptor accounting")
        before = len(list(descriptor_dir.iterdir()))
        for _ in range(50):
            with (
                patch("joinforecast.os.fstat", side_effect=OSError("check failed")),
                self.assertRaisesRegex(InputError, "check failed"),
            ):
                self.load(self.csv, "memory")
        self.assertEqual(len(list(descriptor_dir.iterdir())), before)
