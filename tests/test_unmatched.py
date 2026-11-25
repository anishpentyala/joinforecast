import contextlib
import copy
import io
import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from joinforecast import InputError, Profile, forecast, read_csv
from joinforecast.__main__ import main
from joinforecast.disk import sqlite_csv


def profile(keys):
    counts = Counter(keys)
    return Profile(counts, len(keys), sum(None in k for k in keys), ("a", "b"))


class UnmatchedTests(unittest.TestCase):
    def test_opt_in_preserves_default_and_null_reasons(self):
        left = profile([("a", "x")] * 3 + [(None, "x")] * 2 + [("z", "x")] * 2)
        right = profile([("b", "x")] * 4 + [(None, "x")])
        original = copy.deepcopy((left, right))
        plain = forecast(left, right)
        r = forecast(left, right, unmatched_top=2)
        diagnostics = r.pop("unmatched_keys")
        self.assertEqual(r, plain)
        self.assertEqual((left, right), original)
        self.assertEqual(
            diagnostics["left"],
            {
                "key_groups": 3,
                "null_excluded_rows": 2,
                "top": [
                    {"key": ["a", "x"], "rows": 3, "reason": "NO_EQUAL_KEY"},
                    {"key": [None, "x"], "rows": 2, "reason": "NULLS_NEVER"},
                ],
            },
        )
        self.assertEqual(diagnostics["right"]["key_groups"], 2)
        matched = forecast(left, right, nulls="match", unmatched_top=10)["unmatched_keys"]
        self.assertEqual(matched["left"]["null_excluded_rows"], 0)
        self.assertEqual([x["key"] for x in matched["left"]["top"]], [["a", "x"], ["z", "x"]])

    def test_ties_first_seen_mixed_null_components_and_zero(self):
        left = profile([("a", None), (None, "a"), ("b", "a")])
        right = profile([])
        r = forecast(left, right, unmatched_top=2)["unmatched_keys"]
        self.assertEqual([x["key"] for x in r["left"]["top"]], [["a", None], [None, "a"]])
        zero = forecast(left, right, unmatched_top=0)["unmatched_keys"]
        self.assertEqual(zero["left"]["key_groups"], 3)
        self.assertEqual(zero["left"]["top"], [])
        self.assertEqual(zero["right"]["key_groups"], 0)

    def test_invalid_option(self):
        for value in [-1, True, 1.0, "2", []]:
            with self.subTest(value=value), self.assertRaises(InputError):
                forecast(profile([]), profile([]), unmatched_top=value)

    def test_seeded_nested_loop_row_oracle(self):
        rng = random.Random(622)
        domain = [("a", "x"), ("b", "x"), ("c", "y"), (None, "x"), ("a", None), ("", "x")]
        for _ in range(250):
            left = rng.choices(domain, k=rng.randrange(20))
            right = rng.choices(domain, k=rng.randrange(20))
            for nulls in ("never", "match"):
                for limit in (0, 1, 3, 20):
                    result = forecast(
                        profile(left), profile(right), nulls=nulls, unmatched_top=limit
                    )
                    for name, values, other in [("left", left, right), ("right", right, left)]:
                        unmatched = [
                            key
                            for key in values
                            if not any(
                                key == candidate and (nulls == "match" or None not in key)
                                for candidate in other
                            )
                        ]
                        counts = Counter(unmatched)
                        keys = sorted(counts, key=lambda k: (-counts[k], values.index(k)))
                        got = result["unmatched_keys"][name]
                        self.assertEqual(got["key_groups"], len(counts))
                        self.assertEqual(
                            got["null_excluded_rows"],
                            sum(None in k for k in unmatched) if nulls == "never" else 0,
                        )
                        self.assertEqual(
                            got["top"],
                            [
                                {
                                    "key": list(k),
                                    "rows": counts[k],
                                    "reason": "NULLS_NEVER"
                                    if nulls == "never" and None in k
                                    else "NO_EQUAL_KEY",
                                }
                                for k in keys[:limit]
                            ],
                        )
                        self.assertEqual(
                            sum(counts.values()), result["predicted_rows"][name + "_anti"]
                        )

    def test_sqlite_memory_parity_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            left, right = Path(directory) / "l.csv", Path(directory) / "r.csv"
            left.write_text("a,b\na,x\na,x\n,x\nz,x\n", encoding="utf-8")
            right.write_text("a,b\nb,x\n,x\n", encoding="utf-8")
            keys = ("a", "b")
            for nulls in ("never", "match"):
                memory = forecast(
                    read_csv(left, keys), read_csv(right, keys), nulls=nulls, unmatched_top=5
                )
                with sqlite_csv(left, keys, temp_dir=directory) as left_disk:
                    with sqlite_csv(right, keys, temp_dir=directory) as right_disk:
                        self.assertEqual(
                            forecast(left_disk, right_disk, nulls=nulls, unmatched_top=5), memory
                        )
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["l.csv", "r.csv"])

    def test_cli_and_checked_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            left, right = Path(directory) / "l.csv", Path(directory) / "r.csv"
            left.write_text("id\na\na\n")
            right.write_text("id\nb\n")
            args = [str(left), str(right), "--left-key", "id", "--unmatched-top", "2"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(args), 0)
            self.assertEqual(
                json.loads(output.getvalue())["unmatched_keys"]["left"]["top"][0]["rows"], 2
            )
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(main(args + ["--unmatched-top", "-1"]), 2)
                self.assertEqual(main(args + ["--require-match", "both"]), 1)

            class Short(io.StringIO):
                def write(self, text):
                    return 0

            with patch("sys.stdout", Short()), patch("sys.stderr", io.StringIO()):
                self.assertEqual(main(args), 2)
