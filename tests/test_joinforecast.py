import contextlib
import io
import itertools
import json
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from joinforecast import InputError, Profile, forecast, read_csv
from joinforecast.__main__ import main


def profile(keys):
    counts = Counter(tuple(k) for k in keys)
    return Profile(
        counts, sum(counts.values()), sum(n for k, n in counts.items() if None in k), ("k", "q")
    )


class ForecastTests(unittest.TestCase):
    def test_hand_computed_fanout(self):
        left = profile([("a", "x")] * 3 + [("b", "x"), (None, "x")])
        right = profile([("a", "x")] * 4 + [("c", "x"), (None, "x")])
        result = forecast(left, right)
        self.assertEqual(
            result["predicted_rows"],
            dict(inner=12, left=14, right=14, full=16, left_anti=2, right_anti=2),
        )
        self.assertEqual((result["matched_left_rows"], result["matched_right_rows"]), (3, 4))
        self.assertEqual(result["many_to_many_keys"], 1)
        self.assertEqual(result["top_contributors"][0]["joined_rows"], 12)

    def test_null_modes(self):
        left = profile([(None, "a"), ("a", None), (None, None), (None, "a")])
        right = profile([(None, "a"), ("a", None), (None, None)])
        self.assertEqual(forecast(left, right)["predicted_rows"]["inner"], 0)
        result = forecast(left, right, nulls="match")
        self.assertEqual(result["predicted_rows"]["inner"], 4)
        self.assertEqual(result["matched_left_rows"], 4)

    def test_sqlite_oracle(self):
        # Independent relational execution, not a copy of frequency arithmetic.
        rng = random.Random(4237)
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE l(k TEXT, q TEXT)")
            db.execute("CREATE TABLE r(k TEXT, q TEXT)")
            for trial in range(240):
                left = [
                    (rng.choice([None, "", "a", "b", "01"]), rng.choice([None, "x", "y"]))
                    for _ in range(rng.randrange(14))
                ]
                right = [
                    (rng.choice([None, "", "a", "b", "01"]), rng.choice([None, "x", "y"]))
                    for _ in range(rng.randrange(14))
                ]
                db.execute("DELETE FROM l")
                db.execute("DELETE FROM r")
                db.executemany("INSERT INTO l VALUES (?,?)", left)
                db.executemany("INSERT INTO r VALUES (?,?)", right)
                for mode, op in [("never", "="), ("match", "IS")]:
                    condition = "l.k %s r.k AND l.q %s r.q" % (op, op)

                    def count(sql):
                        return db.execute(sql).fetchone()[0]

                    inner = count("SELECT count(*) FROM l JOIN r ON " + condition)
                    lanti = count(
                        "SELECT count(*) FROM l WHERE NOT EXISTS (SELECT 1 FROM r WHERE "
                        + condition
                        + ")"
                    )
                    ranti = count(
                        "SELECT count(*) FROM r WHERE NOT EXISTS (SELECT 1 FROM l WHERE "
                        + condition
                        + ")"
                    )
                    expected = dict(
                        inner=inner,
                        left=inner + lanti,
                        right=inner + ranti,
                        full=inner + lanti + ranti,
                        left_anti=lanti,
                        right_anti=ranti,
                    )
                    with self.subTest(trial=trial, mode=mode):
                        result = forecast(profile(left), profile(right), nulls=mode)
                        self.assertEqual(result["predicted_rows"], expected)
                        self.assertEqual(
                            result["predicted_rows"]["left"],
                            count("SELECT count(*) FROM l LEFT JOIN r ON " + condition),
                        )

    def test_exact_large_counts_no_join_materialization(self):
        left = Profile(Counter({("x",): 10**12}), 10**12, 0, ("k",))
        right = Profile(Counter({("x",): 10**12}), 10**12, 0, ("id",))
        result = forecast(left, right, max_output_rows=10**20)
        self.assertEqual(result["predicted_rows"]["inner"], 10**24)
        self.assertFalse(result["ok"])

    def test_contracts(self):
        for lc, rc, expectation in itertools.product(
            [1, 2], [1, 2], ["one-to-one", "one-to-many", "many-to-one", "many-to-many"]
        ):
            result = forecast(
                profile([("a", "x")] * lc), profile([("a", "x")] * rc), expectation=expectation
            )
            expected = not (
                (lc > 1 and expectation in ["one-to-one", "one-to-many"])
                or (rc > 1 and expectation in ["one-to-one", "many-to-one"])
            )
            self.assertEqual(result["ok"], expected)

    def test_unmatched_duplicates_also_break_contract(self):
        result = forecast(
            profile([("a", "x")] * 2), profile([("b", "x")]), expectation="one-to-one"
        )
        self.assertFalse(result["ok"])

    def test_unjoinable_nulls_not_uniqueness_violation(self):
        p = profile([(None, "x")] * 2)
        self.assertTrue(forecast(p, p, expectation="one-to-one")["ok"])
        self.assertFalse(forecast(p, p, expectation="one-to-one", nulls="match")["ok"])

    def test_require_match_and_limits(self):
        p, q = profile([("a", "x")]), profile([("b", "x")])
        for side in ["left", "right", "both"]:
            self.assertFalse(forecast(p, q, require_match=side)["ok"])
        self.assertTrue(forecast(p, q, max_output_rows=2)["ok"])
        self.assertFalse(forecast(p, q, max_output_rows=1)["ok"])

    def test_top_zero_order_and_ties(self):
        p = profile([("b", "x")] * 2 + [("a", "x")] * 3 + [("c", "x")] * 3)
        self.assertEqual(forecast(p, p, top=0)["top_contributors"], [])
        self.assertEqual(
            [r["key"][0] for r in forecast(p, p, top=2)["top_contributors"]], ["a", "c"]
        )
        self.assertEqual(len(forecast(p, p, top=100)["top_contributors"]), 3)

    def test_empty_profiles(self):
        self.assertEqual(
            forecast(profile([]), profile([]))["predicted_rows"],
            dict(inner=0, left=0, right=0, full=0, left_anti=0, right_anti=0),
        )

    def test_arity_mismatch(self):
        with self.assertRaises(InputError):
            forecast(profile([]), Profile(Counter(), 0, 0, ("id",)))

    def test_invalid_options(self):
        p = profile([])
        for options in [
            dict(nulls="guess"),
            dict(top=-1),
            dict(top=True),
            dict(expectation="guess"),
            dict(require_match="maybe"),
            dict(max_output_rows=-1),
            dict(max_output_rows=2.5),
            dict(max_output_rows=True),
        ]:
            with self.subTest(options=options), self.assertRaises(InputError):
                forecast(p, p, **options)

    def test_invalid_profiles(self):
        p = profile([])
        for bad in [
            None,
            Profile(Counter({("x",): 1}), 1, 0, ("a", "b")),
            Profile(Counter({("x",): -1}), -1, 0, ("a",)),
            Profile(Counter({("x",): True}), 1, 0, ("a",)),
            Profile(Counter({(3,): 1}), 1, 0, ("a",)),
            Profile(Counter({(None,): 1}), 1, 0, ("a",)),
        ]:
            with self.subTest(bad=bad), self.assertRaises(InputError):
                forecast(bad, p)


class CsvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data.csv"

    def read(self, text, keys=("id",), **kwargs):
        with self.path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        return read_csv(self.path, keys, **kwargs)

    def test_preserves_strings(self):
        p = self.read("id,unused\n001,x\n1,y\n 1,z\nNA,q\n")
        self.assertEqual(set(p.counts), {("001",), ("1",), (" 1",), ("NA",)})

    def test_bom_crlf_quoted_composite(self):
        p = self.read('\ufeffid,other\r\n"a,b","line1\nline2"\r\n', keys=["id", "other"])
        self.assertEqual(p.counts, Counter({("a,b", "line1\nline2"): 1}))

    def test_custom_delimiter(self):
        self.assertEqual(self.read("id;v\nx;y\n", delimiter=";").rows, 1)

    def test_null_tokens_replace_default(self):
        p = self.read('id\n""\nNA\n', null_values=["NA"])
        self.assertEqual(p.counts, Counter({("",): 1, (None,): 1}))

    def test_empty_field_null_default(self):
        self.assertEqual(self.read('id\n""\n').null_rows, 1)

    def test_header_only(self):
        self.assertEqual(self.read("id,value\n").rows, 0)

    def test_invalid_csv(self):
        for text in [
            "",
            "\n",
            "id,id\na,b\n",
            "id,\na,b\n",
            "other\nx\n",
            "id,value\nx\n",
            "id\nx,y\n",
            "id\n\n",
            'id\n"unfinished',
        ]:
            with self.subTest(text=text), self.assertRaises(InputError):
                self.read(text)

    def test_limits_never_truncate(self):
        for kwargs in [dict(max_rows=0), dict(max_rows=1), dict(max_keys=0), dict(max_keys=1)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(InputError):
                self.read("id\na\nb\n", **kwargs)
        self.assertEqual(self.read("id\na\na\n", max_rows=2, max_keys=1).rows, 2)

    def test_bad_options(self):
        for kwargs in [
            dict(delimiter="xx"),
            dict(delimiter="\n"),
            dict(max_rows=-1),
            dict(max_keys=-1),
            dict(keys=[]),
            dict(keys=["id", "id"]),
        ]:
            keys = kwargs.pop("keys", ["id"])
            with self.subTest(kwargs=kwargs), self.assertRaises(InputError):
                self.read("id\na\n", keys=keys, **kwargs)

    def test_missing_and_invalid_utf8(self):
        with self.assertRaises(InputError):
            read_csv(self.path, ["id"])
        self.path.write_bytes(b"id\n\xff\n")
        with self.assertRaises(InputError):
            read_csv(self.path, ["id"])

    def test_cli_codes_json_and_no_mutation(self):
        self.path.write_text("id\na\na\n")
        before = self.path.read_bytes()
        for tail, code in [([], 0), (["--expect", "one-to-one"], 1), (["--max-keys", "0"], 2)]:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "joinforecast",
                    str(self.path),
                    str(self.path),
                    "--left-key",
                    "id",
                ]
                + tail,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, code, result.stderr)
            if code < 2:
                report = json.loads(result.stdout)
                self.assertEqual(report["predicted_rows"]["inner"], 4)
                self.assertEqual(report["ok"], code == 0)
            else:
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"no complete forecast", result.stderr)
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_direct_error_has_no_forecast(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([str(self.path), str(self.path), "--left-key", "id"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(err.getvalue().startswith("joinforecast: "))

    def test_cli_different_column_names(self):
        self.path.write_text("id,name\na,a\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(
                [
                    str(self.path),
                    str(self.path),
                    "--left-key",
                    "id",
                    "--right-key",
                    "name",
                    "--null-value",
                    "NA",
                    "--top",
                    "0",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["csv_null_values"], ["NA"])


if __name__ == "__main__":
    unittest.main()
