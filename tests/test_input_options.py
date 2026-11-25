"""Reject API options whose iteration silently changes CSV key semantics."""

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from joinforecast import InputError, forecast, read_csv
from joinforecast.disk import sqlite_csv


class InputOptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.csv = self.root / "input.csv"
        self.csv.write_text("i,d,id\na,b,NA\na,b,N\na,b,A\na,b,\n", encoding="utf8")
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    @contextlib.contextmanager
    def profile(self, disk, columns, **options):
        if disk:
            with sqlite_csv(self.csv, columns, temp_dir=self.scratch, **options) as result:
                yield result
        else:
            yield read_csv(self.csv, columns, **options)

    def test_bare_strings_and_invalid_column_members(self):
        values = ["id", "i", b"id", (), [""], ["id", "id"], [1], [None], [["id"]], None, 1]
        for disk in (False, True):
            for columns in values:
                with self.subTest(disk=disk, columns=columns):
                    with patch("builtins.open", side_effect=AssertionError("CSV must not open")):
                        with self.assertRaises(InputError):
                            with self.profile(disk, columns):
                                self.fail("invalid columns returned a profile")
                    self.assertEqual(list(self.scratch.iterdir()), [])

    def test_bare_null_strings_and_invalid_members(self):
        for disk in (False, True):
            for tokens in ("NA", "", b"NA", [None], [1], [["NA"]], None, 1):
                with self.subTest(disk=disk, tokens=tokens):
                    with patch("builtins.open", side_effect=AssertionError("CSV must not open")):
                        with self.assertRaises(InputError):
                            with self.profile(disk, ["id"], null_values=tokens):
                                self.fail("invalid null tokens returned a profile")
                    self.assertEqual(list(self.scratch.iterdir()), [])

    def test_delimiter_types_fail_before_read(self):
        for disk in (False, True):
            for delimiter in (None, 1, b",", [], "", "::", "\n", "\r", "\0"):
                with self.subTest(disk=disk, delimiter=delimiter):
                    with patch("builtins.open", side_effect=AssertionError("CSV must not open")):
                        with self.assertRaises(InputError):
                            with self.profile(disk, ["id"], delimiter=delimiter):
                                self.fail("invalid delimiter returned a profile")
                    self.assertEqual(list(self.scratch.iterdir()), [])

    def test_generators_and_exact_tokens_have_backend_parity(self):
        reports = []
        for disk in (False, True):
            with self.profile(
                disk, (name for name in ["id"]), null_values=(value for value in ["NA", "NA"])
            ) as profile:
                self.assertEqual(profile.rows, 4)
                self.assertEqual(profile.null_rows, 1)
                self.assertEqual(dict(profile.counts), {(None,): 1, ("N",): 1, ("A",): 1, ("",): 1})
                reports.append(forecast(profile, profile, nulls="match", unmatched_top=2))
            self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(reports[0], reports[1])

    def test_explicit_empty_and_single_character_tokens_remain_supported(self):
        for disk in (False, True):
            for tokens, nulls in (((), 0), ([""], 1), (["N", "A"], 2)):
                with self.subTest(disk=disk, tokens=tokens):
                    with self.profile(disk, ["id"], null_values=tokens) as profile:
                        self.assertEqual(profile.null_rows, nulls)
                        self.assertEqual(profile.columns, ("id",))
            with self.profile(disk, ["i"]) as profile:
                self.assertEqual(dict(profile.counts), {("a",): 4})


if __name__ == "__main__":
    unittest.main()
