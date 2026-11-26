import contextlib
import csv
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from joinforecast import InputError, forecast, read_csv
from joinforecast.__main__ import main
from joinforecast.disk import _DiskCounts, sqlite_csv


class KeyByteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    def csv(self, rows, header=("a", "b")):
        path = self.root / "input.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def check_limit(self, rows, budget, accepted, **options):
        path = self.csv(rows)
        original = path.read_bytes()
        for backend in ("memory", "sqlite"):
            with self.subTest(backend=backend):

                def load():
                    if backend == "memory":
                        return contextlib.nullcontext(
                            read_csv(path, ("a", "b"), max_key_bytes=budget, **options)
                        )
                    return sqlite_csv(
                        path, ("a", "b"), temp_dir=self.scratch, max_key_bytes=budget, **options
                    )

                if accepted:
                    with load() as profile:
                        expected = read_csv(path, ("a", "b"), **options)
                        self.assertEqual(forecast(profile, profile), forecast(expected, expected))
                else:
                    with self.assertRaisesRegex(InputError, "Distinct-key byte limit"):
                        with load():
                            self.fail("over-budget profile escaped")
                self.assertEqual(list(self.scratch.iterdir()), [])
                self.assertEqual(path.read_bytes(), original)

    def test_exact_boundary_and_repeated_keys(self):
        self.check_limit([("ab", "c")] * 30 + [("a", "bc")], 6, True)
        self.check_limit([("ab", "c")] * 30 + [("a", "bc")], 5, False)

    def test_utf8_not_character_count(self):
        self.check_limit([("雪", "😀")], 7, True)
        self.check_limit([("雪", "😀")], 6, False)

    def test_null_normalization_counts_once(self):
        self.check_limit([("NA", "x"), ("", "x"), ("NA", "x")], 1, True, null_values=("NA", ""))
        self.check_limit([("NA", "x"), ("", "x")], 1, False, null_values=())

    def test_zero_payload_still_uses_key_limit(self):
        self.check_limit([("", "")], 0, True)
        self.check_limit([], 0, True)
        path = self.csv([("", "")])
        with self.assertRaisesRegex(InputError, "Distinct-key limit"):
            read_csv(path, ("a", "b"), max_key_bytes=0, max_keys=0)

    def test_nonkey_fields_are_not_charged(self):
        path = self.csv([("x", "z" * 10000)], header=("a", "unused"))
        self.assertEqual(read_csv(path, ("a",), max_key_bytes=1).rows, 1)

    def test_no_unicode_normalization(self):
        self.check_limit([("é", ""), ("e\u0301", "")], 5, True)
        self.check_limit([("é", ""), ("e\u0301", "")], 4, False)

    def test_invalid_limits(self):
        path = self.csv([])
        for limit in (-1, True, False, 1.5, "3", [], {}):
            for loader in (read_csv, sqlite_csv):
                with self.subTest(limit=limit, loader=loader.__name__):
                    with self.assertRaisesRegex(InputError, "Key-byte limit"):
                        if loader is read_csv:
                            loader(path, ("a",), max_key_bytes=limit)
                        else:
                            with loader(path, ("a",), max_key_bytes=limit, temp_dir=self.scratch):
                                self.fail("invalid limit accepted")
                    self.assertEqual(list(self.scratch.iterdir()), [])

    def test_sqlite_refuses_before_increment(self):
        path = self.csv([("ab", "c"), ("xx", "y")])
        original = _DiskCounts._increment
        inserted = []

        def record(counts, key):
            inserted.append(key)
            return original(counts, key)

        with mock.patch.object(_DiskCounts, "_increment", record):
            with self.assertRaises(InputError):
                with sqlite_csv(path, ("a", "b"), max_key_bytes=5, temp_dir=self.scratch):
                    self.fail("over-budget profile escaped")
        self.assertEqual(inserted, [("ab", "c")])
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_default_does_not_add_membership_queries(self):
        path = self.csv([("a", "b")])
        with mock.patch.object(_DiskCounts, "__contains__", side_effect=AssertionError):
            with sqlite_csv(path, ("a", "b")) as profile:
                self.assertEqual(profile.rows, 1)

    def test_random_independent_payload_accounting(self):
        rng = random.Random(2501)
        alphabet = ["", "NA", "雪", "e\u0301", "x", "a,b", "\n"]
        for _ in range(25):
            rows = [tuple(rng.choices(alphabet, k=2)) for _ in range(20)]
            normalized = {tuple(None if v in ("", "NA") else v for v in row) for row in rows}
            payload = len(
                b"".join(v.encode("utf-8") for key in normalized for v in key if v is not None)
            )
            self.check_limit(rows, payload, True, null_values=("", "NA"))
            if payload:
                self.check_limit(rows, payload - 1, False, null_values=("", "NA"))

    def test_cli_both_backends_and_per_input_budget(self):
        path = self.csv([("abc", "")])
        for backend in ("memory", "sqlite"):
            for budget, status in ((3, 0), (2, 2), (-1, 2)):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    result = main(
                        [
                            str(path),
                            str(path),
                            "--left-key",
                            "a",
                            "--max-key-bytes",
                            str(budget),
                            "--storage",
                            backend,
                            "--temp-dir",
                            str(self.scratch),
                        ]
                        if backend == "sqlite"
                        else [
                            str(path),
                            str(path),
                            "--left-key",
                            "a",
                            "--max-key-bytes",
                            str(budget),
                        ]
                    )
                self.assertEqual(result, status)
                if status == 0:
                    self.assertEqual(json.loads(out.getvalue())["predicted_rows"]["inner"], 1)
                else:
                    self.assertEqual(out.getvalue(), "")
                    self.assertTrue(err.getvalue())
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_memory_refuses_before_assignment(self):
        from collections import Counter

        from joinforecast import _read_csv

        class RecordingCounter(Counter):
            def __setitem__(self, key, value):
                assigned.append(key)
                return super().__setitem__(key, value)

        assigned = []
        path = self.csv([("ab", "c"), ("xx", "y")])
        with self.assertRaises(InputError):
            _read_csv(path, ("a", "b"), RecordingCounter(), ("",), ",", 100, 100, max_key_bytes=5)
        self.assertEqual(assigned, [("ab", "c")])

    def test_validation_still_reaches_bad_later_row(self):
        path = self.root / "input.csv"
        path.write_text("a,b\nx,y\nx,y,z\n", encoding="utf-8")
        for loader in (read_csv, sqlite_csv):
            with self.assertRaisesRegex(InputError, "Ragged row"):
                if loader is read_csv:
                    loader(path, ("a", "b"), max_key_bytes=2)
                else:
                    with loader(path, ("a", "b"), max_key_bytes=2):
                        self.fail("invalid tail accepted")

    def test_repeated_refusals_close_files(self):
        import os

        fd_path = Path("/proc/self/fd")
        if not fd_path.is_dir():
            self.skipTest("requires /proc/self/fd")
        path = self.csv([("abc", "d")])
        before = len(os.listdir(fd_path))
        for _ in range(30):
            for loader in (read_csv, sqlite_csv):
                with self.assertRaises(InputError):
                    if loader is read_csv:
                        loader(path, ("a",), max_key_bytes=2)
                    else:
                        with loader(path, ("a",), max_key_bytes=2, temp_dir=self.scratch):
                            self.fail("over-budget input accepted")
        self.assertEqual(before, len(os.listdir(fd_path)))
        self.assertEqual(list(self.scratch.iterdir()), [])
