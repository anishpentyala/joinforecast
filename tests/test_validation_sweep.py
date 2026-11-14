"""Validation and aggregation share scans without accepting partial profiles."""

import csv
import tempfile
import unittest
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

from joinforecast import InputError, Profile, forecast
from joinforecast.disk import _DiskCounts, sqlite_csv


class OnceItems(Mapping):
    def __init__(self, values):
        self.values_map = dict(values)
        self.scans = 0

    def __getitem__(self, key):
        return self.values_map[key]

    def __iter__(self):
        return iter(self.values_map)

    def __len__(self):
        return len(self.values_map)

    def items(self):
        self.scans += 1
        if self.scans > 1:
            raise AssertionError("frequency items scanned more than once")
        return self.values_map.items()


def profile(counts, rows=None, null_rows=None):
    values = counts.values_map if isinstance(counts, OnceItems) else counts
    return Profile(
        counts,
        sum(values.values()) if rows is None else rows,
        sum(n for k, n in values.items() if None in k) if null_rows is None else null_rows,
        ("key",),
    )


class ValidationSweepTests(unittest.TestCase):
    def test_each_frequency_mapping_is_scanned_once(self):
        for mode in ("never", "match"):
            for top in (0, 1, 100):
                left = OnceItems({("a",): 3, (None,): 4, ("unmatched",): 2})
                right = OnceItems({("a",): 2, (None,): 5, ("right-only",): 7})
                result = forecast(profile(left), profile(right), nulls=mode, top=top)
                self.assertEqual(result["predicted_rows"]["inner"], 26 if mode == "match" else 6)
                self.assertEqual((left.scans, right.scans), (1, 1))
                self.assertEqual(
                    result["right_duplicate_joinable_keys"], 3 if mode == "match" else 2
                )

    def test_sqlite_uses_two_complete_frequency_selects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keys.csv"
            with path.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerows([["id"], ["a"], ["a"], [""], ["z"]])
            with sqlite_csv(path, ("id",)) as left, sqlite_csv(path, ("id",)) as right:
                scans = []
                original = _DiskCounts._execute

                def execute(counts, statement, parameters=()):
                    if statement == "SELECT key, n FROM counts ORDER BY rowid":
                        scans.append(counts)
                    return original(counts, statement, parameters)

                with patch.object(_DiskCounts, "_execute", execute):
                    result = forecast(left, right, nulls="match", top=0)
                self.assertEqual(result["predicted_rows"]["inner"], 6)
                self.assertEqual(len(scans), 2)
                self.assertEqual(sum(counts is left.counts for counts in scans), 1)
                self.assertEqual(sum(counts is right.counts for counts in scans), 1)

    def test_invalid_tail_is_rejected_on_both_sides(self):
        valid = profile(Counter({("match",): 2}))
        for top in (0, 1):
            for invalid in (0, -1, True, 1.0):
                bad = Profile({("match",): 2, ("tail",): invalid}, 3, 0, ("key",))
                with self.subTest(top=top, invalid=invalid):
                    with self.assertRaisesRegex(InputError, "Invalid key frequency"):
                        forecast(bad, valid, top=top)
                    with self.assertRaisesRegex(InputError, "Invalid key frequency"):
                        forecast(valid, bad, top=top)

    def test_invalid_tail_key_is_rejected_on_both_sides(self):
        valid = profile(Counter({("match",): 2}))
        for key in ("not-tuple", ("too", "wide"), (42,)):
            bad = Profile({("match",): 2, key: 1}, 3, 0, ("key",))
            with self.subTest(key=key):
                with self.assertRaisesRegex(InputError, "Invalid key frequency"):
                    forecast(bad, valid, top=0)
                with self.assertRaisesRegex(InputError, "Invalid key frequency"):
                    forecast(valid, bad, top=0)

    def test_exhausted_totals_are_checked_before_return(self):
        valid = profile(Counter({("match",): 2, (None,): 3}))
        for rows, null_rows in ((6, 3), (5, 2)):
            bad = Profile(Counter({("match",): 2, (None,): 3}), rows, null_rows, ("key",))
            for top in (0, 1):
                for mode in ("never", "match"):
                    with self.subTest(rows=rows, null_rows=null_rows, top=top, mode=mode):
                        with self.assertRaisesRegex(InputError, "totals do not match"):
                            forecast(bad, valid, top=top, nulls=mode)
                        with self.assertRaisesRegex(InputError, "totals do not match"):
                            forecast(valid, bad, top=top, nulls=mode)


if __name__ == "__main__":
    unittest.main()
