import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from joinforecast.__main__ import cli, main


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.left = Path(self.temporary.name) / "left.csv"
        self.right = Path(self.temporary.name) / "right.csv"
        self.left.write_text("k\na\na\nb\n", encoding="utf-8")
        self.right.write_text("k\na\na\nc\n", encoding="utf-8")
        self.args = [str(self.left), str(self.right), "--left-key", "k"]

    def test_short_or_invalid_write_counts_are_not_success(self):
        class Short(io.StringIO):
            def write(self, text):
                super().write(text[:1])
                return count

        for count in (0, 1, -1, None, True, 1.5, 1000000):
            with self.subTest(count=count):
                out, err = Short(), io.StringIO()
                with patch("sys.stdout", out), patch("sys.stderr", err):
                    self.assertEqual(main(self.args), 2)
                self.assertFalse(out.closed)
                self.assertFalse(err.closed)

    def test_flush_write_and_closed_failures_leave_caller_streams_alone(self):
        class Flush(io.StringIO):
            def flush(self):
                raise OSError("flush failed")

        class Write(io.StringIO):
            def write(self, text):
                raise BrokenPipeError("reader gone")

        closed = io.StringIO()
        closed.close()
        for out in (Flush(), Write(), closed, None):
            for storage in ("memory", "sqlite"):
                with self.subTest(output=type(out).__name__, storage=storage):
                    err = io.StringIO()
                    with patch("sys.stdout", out), patch("sys.stderr", err):
                        self.assertEqual(main(self.args + ["--storage", storage]), 2)
                        self.assertIs(sys.stdout, out)
                        self.assertIs(sys.stderr, err)
                    self.assertFalse(err.closed)

    def test_failed_diagnostics_and_absent_optional_stderr(self):
        closed = io.StringIO()
        closed.close()
        for err in (closed, None):
            with self.subTest(stderr=err):
                out = io.StringIO()
                with patch("sys.stdout", out), patch("sys.stderr", err):
                    self.assertEqual(main(self.args), 0)
                    self.assertEqual(main(self.args + ["--expect", "one-to-one"]), 1)
                    self.left.write_text("k\na,b\n", encoding="utf-8")
                    self.assertEqual(main(self.args), 2)
                self.left.write_text("k\na\na\nb\n", encoding="utf-8")

    def test_help_version_and_usage_delivery(self):
        class Short(io.StringIO):
            def write(self, text):
                return 0

        for args in (["--help"], ["--version"], []):
            for failed in ("stdout", "stderr"):
                out, err = io.StringIO(), io.StringIO()
                if failed == "stdout":
                    out = Short()
                else:
                    err = Short()
                with self.subTest(args=args, failed=failed):
                    with patch("sys.stdout", out), patch("sys.stderr", err):
                        if args and failed == "stderr":
                            with self.assertRaises(SystemExit) as raised:
                                main(args)
                            self.assertEqual(raised.exception.code, 0)
                        elif not args and failed == "stdout":
                            with self.assertRaises(SystemExit) as raised:
                                main(args)
                            self.assertEqual(raised.exception.code, 2)
                        else:
                            self.assertEqual(main(args), 2)

    def test_absent_stdout_cannot_redirect_help_or_version_to_stderr(self):
        for args in (["--help"], ["--version"]):
            err = io.StringIO()
            with self.subTest(args=args):
                with patch("sys.stdout", None), patch("sys.stderr", err):
                    self.assertEqual(main(args), 2)
                self.assertNotIn("usage:", err.getvalue())
                self.assertNotIn("0.2.2", err.getvalue())

    def test_cli_prevents_shutdown_retry_only_for_failing_stream(self):
        class Flush(io.StringIO):
            def flush(self):
                raise OSError("flush failed")

        out, err = Flush(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            self.assertEqual(cli(self.args), 2)
            self.assertIsNone(sys.stdout)
            self.assertIs(sys.stderr, err)
        self.assertFalse(out.closed)
        self.assertFalse(err.closed)

    def test_shutdown_flush_failure_overrides_success(self):
        class Flush(io.StringIO):
            def flush(self):
                raise OSError("late flush failed")

        out = Flush()
        with (
            patch("joinforecast.__main__.main", return_value=0),
            patch("sys.stdout", out),
            patch("sys.stderr", io.StringIO()),
        ):
            self.assertEqual(cli([]), 2)
            self.assertIsNone(sys.stdout)

    @unittest.skipUnless(os.path.exists("/dev/full"), "requires POSIX full device")
    def test_process_full_device_help_version_and_report(self):
        for unbuffered in (False, True):
            for args in (self.args, ["--help"], ["--version"]):
                with self.subTest(args=args, unbuffered=unbuffered):
                    with open("/dev/full", "wb") as output:
                        result = subprocess.run(
                            [
                                sys.executable,
                                *(["-u"] if unbuffered else []),
                                "-m",
                                "joinforecast",
                                *args,
                            ],
                            stdout=output,
                            stderr=subprocess.PIPE,
                            check=False,
                        )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertNotIn(b"Traceback", result.stderr)
                    self.assertNotIn(b"Exception ignored", result.stderr)

    def test_success_bytes_stable_and_statuses_preserved(self):
        for storage in ("memory", "sqlite"):
            for extra, expected in (([], 0), (["--expect", "one-to-one"], 1)):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(main(self.args + ["--storage", storage] + extra), expected)
                self.assertTrue(out.getvalue().endswith("\n"))
                self.assertEqual(err.getvalue(), "")
