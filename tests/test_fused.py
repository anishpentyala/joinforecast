"""Full-report and operation-count probes for the fused scan."""

import csv
import json
import random
import tempfile
import unittest
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch

from joinforecast import InputError, Profile, forecast, read_csv
from joinforecast.disk import _DiskCounts, sqlite_csv


class CountingMapping(Mapping):
    def __init__(self, values):
        self.values_map = dict(values)
        self.probes = 0
        self.scans = 0

    def __getitem__(self, key):
        self.probes += 1
        return self.values_map[key]

    def __iter__(self):
        return iter(self.values_map)

    def __len__(self):
        return len(self.values_map)

    def items(self):
        self.scans += 1
        return self.values_map.items()


def prof(counts):
    return Profile(
        counts, sum(counts.values()), sum(n for k, n in counts.items() if None in k), ("id",)
    )


def literal(left, right, *, mode, top):
    # Deliberate brute expanded row join and stable full sort, independent of
    # frequency aggregation/heap internals. Small synthetic inputs only.
    def joinable(key):
        return mode == "match" or None not in key

    lp = [k for k, n in left.items() for _ in range(n)]
    rp = [k for k, n in right.items() for _ in range(n)]
    pairs = [(a, b) for a in lp for b in rp if joinable(a) and a == b]
    la = sum(not any(joinable(a) and a == b for b in rp) for a in lp)
    ra = sum(not any(joinable(b) and a == b for a in lp) for b in rp)
    contributors = []
    for key in left:
        joined = sum(a == key for a, b in pairs)
        if joined:
            contributors.append(
                dict(
                    key=list(key),
                    left_rows=left[key],
                    right_rows=right[key],
                    joined_rows=joined,
                    many_to_many=left[key] > 1 and right[key] > 1,
                )
            )
    return dict(
        predicted_rows=dict(
            inner=len(pairs),
            left=len(pairs) + la,
            right=len(pairs) + ra,
            full=len(pairs) + la + ra,
            left_anti=la,
            right_anti=ra,
        ),
        top_contributors=sorted(contributors, key=lambda r: r["joined_rows"], reverse=True)[:top],
        matched_left_rows=len(lp) - la,
        matched_right_rows=len(rp) - ra,
        left_duplicate_joinable_keys=sum(n > 1 and joinable(k) for k, n in left.items()),
        right_duplicate_joinable_keys=sum(n > 1 and joinable(k) for k, n in right.items()),
    )


class FusedTests(unittest.TestCase):
    def test_single_right_probe_per_joinable_left_key(self):
        for top in (0, 1, 4, 99):
            left = CountingMapping({("a",): 3, ("b",): 1, (None,): 4, ("unmatched",): 2})
            right = CountingMapping({("a",): 2, ("b",): 3, (None,): 1})
            lp, rp = prof(left), prof(right)
            left.scans = right.scans = 0
            left.probes = right.probes = 0
            result = forecast(lp, rp, top=top)
            self.assertEqual(right.probes, 3)
            self.assertEqual(left.scans, 2)  # validation + aggregation, not top scan
            self.assertEqual(right.scans, 2)  # validation + right duplicates
            self.assertEqual(result["predicted_rows"]["inner"], 9)

    def test_stable_ties_at_heap_boundary_and_mixed_null(self):
        left = Counter({(None,): 2, ("z",): 3, ("a",): 1, ("x",): 4})
        right = Counter({("a",): 6, ("z",): 2, (None,): 3, ("x",): 3})
        for top in (0, 1, 2, 3, 4, 99):
            row = forecast(prof(left), prof(right), nulls="match", top=top)
            expected = literal(left, right, mode="match", top=top)
            self.assertEqual(row["top_contributors"], expected["top_contributors"])

    def test_top_zero_still_counts_and_checks_all_keys(self):
        left = Counter({("u",): 5, ("x",): 2})
        right = Counter({("x",): 3, ("v",): 4})
        row = forecast(
            prof(left),
            prof(right),
            top=0,
            expectation="one-to-one",
            require_match="both",
            max_output_rows=0,
        )
        self.assertEqual(row["predicted_rows"]["full"], 15)
        self.assertEqual(row["top_contributors"], [])
        self.assertEqual(len(row["violations"]), 8)

    def test_expanded_join_oracle_counts_top_and_contracts(self):
        rng = random.Random(2584)
        choices = [(None,), ("",), ("01",), ("a",), ("x,y",), ("❄",)]
        for case in range(180):
            left = Counter(rng.choices(choices, k=rng.randrange(20)))
            right = Counter(rng.choices(choices, k=rng.randrange(20)))
            for mode in ("never", "match"):
                top = rng.randrange(9)
                row = forecast(
                    prof(left),
                    prof(right),
                    nulls=mode,
                    top=top,
                    expectation="one-to-one",
                    require_match="both",
                    max_output_rows=17,
                )
                expected = literal(left, right, mode=mode, top=top)
                with self.subTest(case=case, mode=mode):
                    for name, value in expected.items():
                        self.assertEqual(row[name], value)
                    violations = []
                    if expected["left_duplicate_joinable_keys"]:
                        violations.append("Left joinable keys are not unique")
                    if expected["right_duplicate_joinable_keys"]:
                        violations.append("Right joinable keys are not unique")
                    if row["predicted_rows"]["left_anti"]:
                        violations.append("Left rows have no match")
                    if row["predicted_rows"]["right_anti"]:
                        violations.append("Right rows have no match")
                    for how in ("inner", "left", "right", "full"):
                        if row["predicted_rows"][how] > 17:
                            violations.append(how + " join exceeds output row limit")
                    self.assertEqual(row["violations"], violations)
                    self.assertEqual(row["ok"], not violations)


class IncrementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "data.csv"

    def write(self, values):
        with self.path.open("w", encoding="utf8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["id"])
            writer.writerows([[v] for v in values])

    def test_private_parser_default_does_not_dispatch_mapping_attributes(self):
        from joinforecast import _read_csv

        class IncidentalCounter(Counter):
            def _increment(self, key):
                raise AssertionError("Unrelated mapping attribute must not be called")

        self.write(["a", "a", "b"])
        profile = _read_csv(self.path, ["id"], IncidentalCounter(), ("",), ",", 3, 2)
        self.assertEqual(profile.counts, {("a",): 2, ("b",): 1})

    def test_increment_statement_count_and_first_seen_order(self):
        self.write(["b", "a", "b", "b", "c", "a", ""])
        statements = []
        original = _DiskCounts._execute

        def traced(obj, statement, parameters=()):
            statements.append(statement)
            return original(obj, statement, parameters)

        with patch.object(_DiskCounts, "_execute", traced):
            with sqlite_csv(self.path, ["id"]) as disk:
                self.assertEqual(len(disk.counts), 4)
                self.assertEqual(
                    list(disk.counts.items()), [(("b",), 3), (("a",), 2), (("c",), 1), ((None,), 1)]
                )
        self.assertEqual(sum(s.startswith("INSERT OR IGNORE") for s in statements), 7)
        self.assertEqual(sum(s.startswith("UPDATE") for s in statements), 3)
        self.assertEqual(sum(s.startswith("SELECT n") for s in statements), 0)

    def test_generic_mapping_assignment_still_sets_not_increments(self):
        self.write([])
        with sqlite_csv(self.path, ["id"]) as disk:
            disk.counts[("a",)] = 17
            disk.counts[("a",)] = 3
            disk.counts[("b",)] = 8
            self.assertEqual(len(disk.counts), 2)
            self.assertEqual(dict(disk.counts), {("a",): 3, ("b",): 8})

    def test_exact_caps_duplicates_nulls_cleanup(self):
        self.write(["a", "a", "", "b"])
        with sqlite_csv(self.path, ["id"], max_rows=4, max_keys=3, temp_dir=self.root) as disk:
            self.assertEqual(disk.rows, 4)
            self.assertEqual(disk.null_rows, 1)
            self.assertEqual(
                forecast(disk, disk),
                forecast(read_csv(self.path, ["id"]), read_csv(self.path, ["id"])),
            )
        for kwargs in (dict(max_rows=3), dict(max_keys=2)):
            with self.assertRaises(InputError):
                with sqlite_csv(self.path, ["id"], temp_dir=self.root, **kwargs):
                    self.fail("must not yield over-budget input")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["data.csv"])

    def test_increment_storage_failure_no_partial_profile(self):
        self.write(["a", "a"])
        original = _DiskCounts._execute

        def fail_update(obj, statement, parameters=()):
            if statement.startswith("UPDATE"):
                raise InputError("injected update failure")
            return original(obj, statement, parameters)

        with patch.object(_DiskCounts, "_execute", fail_update):
            with self.assertRaisesRegex(InputError, "update failure"):
                with sqlite_csv(self.path, ["id"], temp_dir=self.root):
                    self.fail("must not yield failed increment profile")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["data.csv"])

    def test_cli_memory_disk_report_bytes(self):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from joinforecast.__main__ import main

        self.write(["a", "b", "b", "", "01"])
        reports = []
        for backend in ("memory", "sqlite"):
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(
                    [
                        str(self.path),
                        str(self.path),
                        "--left-key",
                        "id",
                        "--storage",
                        backend,
                        "--expect",
                        "one-to-one",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertEqual(err.getvalue(), "")
            reports.append(out.getvalue())
            self.assertEqual(json.loads(out.getvalue())["predicted_rows"]["inner"], 6)
        self.assertEqual(*reports)
