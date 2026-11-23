import importlib.metadata
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ConsoleOutputTests(unittest.TestCase):
    def test_installed_console_targets_terminating_wrapper(self):
        distribution = importlib.metadata.distribution("joinforecast")
        entries = [
            entry
            for entry in distribution.entry_points
            if entry.group == "console_scripts" and entry.name == "joinforecast"
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].value, "joinforecast.__main__:cli")

    @unittest.skipUnless(os.path.exists("/dev/full"), "requires POSIX full device")
    def test_installed_console_full_output_report_help_and_version(self):
        # The wrapper beside this interpreter belongs to this installed wheel,
        # not an unrelated executable that happens to be first on PATH.
        executable = Path(sys.executable).parent / "joinforecast"
        if not executable.exists():
            self.skipTest("installed console script is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text("k\na\na\n", encoding="utf-8")
            for arguments in ([str(path), str(path), "--left-key", "k"], ["--help"], ["--version"]):
                for unbuffered in (False, True):
                    with self.subTest(arguments=arguments, unbuffered=unbuffered):
                        environment = dict(os.environ)
                        environment.pop("PYTHONPATH", None)
                        environment.pop("PYTHONUNBUFFERED", None)
                        if unbuffered:
                            environment["PYTHONUNBUFFERED"] = "1"
                        with open("/dev/full", "wb") as output:
                            result = subprocess.run(
                                [str(executable), *arguments],
                                stdout=output,
                                stderr=subprocess.PIPE,
                                env=environment,
                                cwd=directory,
                                check=False,
                            )
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertNotIn(b"Traceback", result.stderr)
                        self.assertNotIn(b"Exception ignored", result.stderr)
