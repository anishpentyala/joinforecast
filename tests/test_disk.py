import contextlib
import csv
import io
import json
import random
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from joinforecast import InputError, forecast, read_csv
from joinforecast.__main__ import main
from joinforecast.disk import sqlite_csv


class DiskTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.parent = self.root / "temp"
        self.parent.mkdir()

    def csv(self, name, rows, header=("id", "part")):
        path = self.root / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def test_report_parity_random_options_and_keys(self):
        rng = random.Random(9141)
        values = ["", "NA", "01", "a", "x,y", "[null]", "\n", "❄", 'a"b', " 1"]
        for trial in range(45):
            left = self.csv("left.csv", [rng.choices(values, k=2) for _ in range(35)])
            right = self.csv("right.csv", [rng.choices(values, k=2) for _ in range(30)])
            for mode in ("never", "match"):
                for tokens in (("",), ("NA", ""), ()):
                    opts = dict(null_values=tokens)
                    lm = read_csv(left, ("id", "part"), **opts)
                    rm = read_csv(right, ("id", "part"), **opts)
                    with sqlite_csv(left, ("id", "part"), temp_dir=self.parent, **opts) as ld:
                        with sqlite_csv(right, ("id", "part"), temp_dir=self.parent, **opts) as rd:
                            kwargs = dict(
                                nulls=mode,
                                top=7,
                                expectation="one-to-one",
                                require_match="both",
                                max_output_rows=17,
                            )
                            self.assertEqual(forecast(lm, rm, **kwargs), forecast(ld, rd, **kwargs))
                    self.assertEqual(list(self.parent.iterdir()), [])

    def test_independent_relational_oracle(self):
        rng = random.Random(6198)
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE l(a TEXT, b TEXT)")
            db.execute("CREATE TABLE r(a TEXT, b TEXT)")
            for _ in range(100):
                values = [None, "", "01", "a", "x,y"]
                left = [tuple(rng.choices(values, k=2)) for _ in range(rng.randrange(12))]
                right = [tuple(rng.choices(values, k=2)) for _ in range(rng.randrange(12))]
                lp = self.csv("left.csv", [["NULL" if x is None else x for x in k] for k in left])
                rp = self.csv("right.csv", [["NULL" if x is None else x for x in k] for k in right])
                db.execute("DELETE FROM l")
                db.execute("DELETE FROM r")
                db.executemany("INSERT INTO l VALUES (?,?)", left)
                db.executemany("INSERT INTO r VALUES (?,?)", right)
                for mode, op in (("never", "="), ("match", "IS")):
                    on = "l.a %s r.a AND l.b %s r.b" % (op, op)

                    def count(sql):
                        return db.execute(sql).fetchone()[0]

                    inner = count("SELECT count(*) FROM l JOIN r ON " + on)
                    la = count(
                        "SELECT count(*) FROM l WHERE NOT EXISTS(SELECT 1 FROM r WHERE " + on + ")"
                    )
                    ra = count(
                        "SELECT count(*) FROM r WHERE NOT EXISTS(SELECT 1 FROM l WHERE " + on + ")"
                    )
                    expected = dict(
                        inner=inner,
                        left=inner + la,
                        right=inner + ra,
                        full=inner + la + ra,
                        left_anti=la,
                        right_anti=ra,
                    )
                    with sqlite_csv(lp, ("id", "part"), null_values=("NULL",)) as ld:
                        with sqlite_csv(rp, ("id", "part"), null_values=("NULL",)) as rd:
                            self.assertEqual(
                                forecast(ld, rd, nulls=mode)["predicted_rows"], expected
                            )

    def test_encoding_has_no_composite_delimiter_or_null_collisions(self):
        path = self.csv("keys.csv", [])
        keys = [
            ("a|b", "c"),
            ("a", "b|c"),
            (None, ""),
            ("None", ""),
            ("\0", "x"),
            ("\\u0000", "x"),
            ("❄", '"'),
        ]
        with sqlite_csv(path, ["id", "part"]) as disk:
            for n, key in enumerate(keys, 1):
                disk.counts[key] = n
            self.assertEqual(list(disk.counts.items()), list(zip(keys, range(1, len(keys) + 1))))
            for n, key in enumerate(keys, 1):
                self.assertEqual(disk.counts.get(key), n)

    def test_mapping_and_closed_iterator(self):
        path = self.csv("keys.csv", [("a", "1"), ("a", "2"), ("b", "1")])
        with sqlite_csv(path, ["id"]) as disk:
            self.assertEqual(dict(disk.counts), {("a",): 2, ("b",): 1})
            self.assertEqual(list(disk.counts.values()), [2, 1])
            self.assertNotIn(("missing",), disk.counts)
            with self.assertRaises(KeyError):
                disk.counts[("missing",)]
        with self.assertRaisesRegex(InputError, "closed"):
            list(disk.counts.items())

    def test_suspended_items_resume_after_context_reports_input_error(self):
        path = self.csv("keys.csv", [("a", "1"), ("b", "2")])
        with sqlite_csv(path, ["id"], temp_dir=self.parent) as disk:
            rows = disk.counts.items()
            self.assertEqual(next(rows), (("a",), 1))
        with self.assertRaisesRegex(InputError, "closed database"):
            next(rows)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_suspended_items_can_close_after_context(self):
        path = self.csv("keys.csv", [("a", "1"), ("b", "2")])
        with sqlite_csv(path, ["id"], temp_dir=self.parent) as disk:
            rows = disk.counts.items()
            next(rows)
        rows.close()
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_key_and_value_iterators_close_after_context(self):
        path = self.csv("keys.csv", [("a", "1"), ("b", "2")])
        with sqlite_csv(path, ["id"], temp_dir=self.parent) as disk:
            keys = iter(disk.counts)
            values = iter(disk.counts.values())
            self.assertEqual(next(keys), ("a",))
            self.assertEqual(next(values), 1)
        with self.assertRaises(InputError):
            next(keys)
        with self.assertRaises(InputError):
            next(values)
        keys.close()
        values.close()
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_live_cursor_is_closed_on_early_generator_close(self):
        from joinforecast.disk import _DiskCounts

        cursor = mock.Mock()
        cursor.__iter__ = mock.Mock(return_value=iter([('["a"]', 1), ('["b"]', 2)]))
        counts = _DiskCounts(None)
        with mock.patch.object(counts, "_execute", return_value=cursor):
            rows = counts.items()
            self.assertEqual(next(rows), (("a",), 1))
            rows.close()
        cursor.close.assert_called_once_with()

    def test_in_context_cursor_close_failure_is_wrapped(self):
        from joinforecast.disk import _DiskCounts

        cursor = mock.Mock()
        cursor.__iter__ = mock.Mock(return_value=iter([('["a"]', 1)]))
        cursor.close.side_effect = sqlite3.OperationalError("close test")
        counts = _DiskCounts(None)
        with mock.patch.object(counts, "_execute", return_value=cursor):
            with self.assertRaisesRegex(InputError, "close test"):
                list(counts.items())
        cursor.close.assert_called_once_with()

    def test_tie_order_matches_memory(self):
        path = self.csv("keys.csv", [(str(i), "x") for i in range(50, 0, -1)])
        memory = read_csv(path, ["id"])
        with sqlite_csv(path, ["id"]) as disk:
            for top in (0, 1, 7, 50, 500):
                self.assertEqual(forecast(memory, memory, top=top), forecast(disk, disk, top=top))

    def test_private_files_and_cleanup(self):
        path = self.csv("keys.csv", [("a", "b")])
        with sqlite_csv(path, ["id"], temp_dir=self.parent) as disk:
            folders = list(self.parent.iterdir())
            self.assertEqual(len(folders), 1)
            self.assertEqual(stat.S_IMODE(folders[0].stat().st_mode), 0o700)
            database = folders[0] / "profile.sqlite3"
            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
            self.assertEqual(len(disk.counts), 1)
        self.assertEqual(list(self.parent.iterdir()), [])
        with self.assertRaisesRegex(InputError, "closed"):
            forecast(disk, disk)
        with self.assertRaisesRegex(InputError, "closed"):
            len(disk.counts)

    def test_cleanup_on_consumer_exception(self):
        path = self.csv("keys.csv", [("a", "b")])
        with self.assertRaisesRegex(RuntimeError, "consumer"):
            with sqlite_csv(path, ["id"], temp_dir=self.parent):
                raise RuntimeError("consumer")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_consumer_os_and_database_errors_keep_identity(self):
        path = self.csv("keys.csv", [("a", "b")])
        for error in (OSError("consumer IO"), sqlite3.OperationalError("consumer DB")):
            with self.assertRaises(type(error)) as raised:
                with sqlite_csv(path, ["id"], temp_dir=self.parent):
                    raise error
            self.assertIs(raised.exception, error)
            self.assertEqual(list(self.parent.iterdir()), [])

    def test_malformed_caps_and_bad_options_cleanup(self):
        path = self.csv("keys.csv", [("a", "b"), ("c", "d")])
        for opts in (
            dict(max_rows=1),
            dict(max_keys=1),
            dict(max_keys=-1),
            dict(max_rows=True),
            dict(delimiter="xx"),
        ):
            with self.assertRaises(InputError):
                with sqlite_csv(path, ["id"], temp_dir=self.parent, **opts):
                    self.fail("must not yield partial profile")
            self.assertEqual(list(self.parent.iterdir()), [])
        bad = self.root / "bad.csv"
        for data in (b"id,id\na,b\n", b'id\n"unterminated', b"id\n\xff", b"id\nx,y\n"):
            bad.write_bytes(data)
            with self.assertRaises(InputError):
                with sqlite_csv(bad, ["id"], temp_dir=self.parent):
                    self.fail("must not yield incomplete profile")
            self.assertEqual(list(self.parent.iterdir()), [])

    def test_real_database_full_failure_cleanup(self):
        path = self.csv("keys.csv", [("a", "b")])
        with self.assertRaisesRegex(InputError, "full"):
            with sqlite_csv(path, ["id"], temp_dir=self.parent) as disk:
                disk.counts.connection.execute("PRAGMA max_page_count=3")
                for n in range(100):
                    disk.counts[(str(n) * 500,)] = 1
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_real_cursor_read_failure_is_not_partial_success(self):
        path = self.csv("keys.csv", [(str(i), "x") for i in range(5)])
        with self.assertRaisesRegex(InputError, "interrupted"):
            with sqlite_csv(path, ["id"], temp_dir=self.parent) as disk:
                rows = disk.counts.items()
                next(rows)
                disk.counts.connection.set_progress_handler(lambda: 1, 1)
                list(rows)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_missing_temp_parent_no_input_change(self):
        path = self.csv("keys.csv", [("a", "b")])
        before = path.read_bytes()
        with self.assertRaises(InputError):
            with sqlite_csv(path, ["id"], temp_dir=self.parent / "missing"):
                self.fail("missing storage must fail")
        self.assertEqual(path.read_bytes(), before)

    def test_connect_failure_cleanup(self):
        path = self.csv("keys.csv", [("a", "b")])
        with mock.patch(
            "joinforecast.disk.sqlite3.connect", side_effect=sqlite3.OperationalError("test")
        ):
            with self.assertRaisesRegex(InputError, "Temporary profile storage"):
                with sqlite_csv(path, ["id"], temp_dir=self.parent):
                    self.fail("cannot yield without a database")
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_empty_and_differently_named_keys(self):
        left = self.csv("left.csv", [])
        right = self.csv("right.csv", [("a", "b")], header=("other", "p"))
        with sqlite_csv(left, ["id"]) as ld, sqlite_csv(right, ["other"]) as rd:
            result = forecast(ld, rd)
            self.assertEqual(
                result["predicted_rows"],
                dict(inner=0, left=0, right=1, full=1, left_anti=0, right_anti=1),
            )
            self.assertEqual(result["right_key_columns"], ["other"])

    def cli(self, left, right, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main([str(left), str(right), "--left-key", "id", *extra])
        return status, out.getvalue(), err.getvalue()

    def test_cli_parity_exit_codes_and_read_only(self):
        left = self.csv("left.csv", [("a", "x"), ("a", "y"), ("", "z")])
        right = self.csv("right.csv", [("a", "x"), ("a", "y")])
        before = left.read_bytes(), right.read_bytes()
        for extra in ((), ("--expect", "many-to-one"), ("--nulls", "match", "--top", "0")):
            memory = self.cli(left, right, *extra)
            disk = self.cli(
                left, right, "--storage", "sqlite", "--temp-dir", str(self.parent), *extra
            )
            self.assertEqual(memory, disk)
            self.assertIsInstance(json.loads(disk[1])["predicted_rows"]["inner"], int)
        self.assertEqual((left.read_bytes(), right.read_bytes()), before)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_cli_second_input_failure_closes_both(self):
        left = self.csv("left.csv", [("a", "x")])
        right = self.root / "bad.csv"
        right.write_text('id\n"unterminated', encoding="utf-8")
        before = left.read_bytes(), right.read_bytes()
        code, out, err = self.cli(
            left, right, "--storage", "sqlite", "--temp-dir", str(self.parent)
        )
        self.assertEqual((code, out), (2, ""))
        self.assertIn("joinforecast:", err)
        self.assertEqual((left.read_bytes(), right.read_bytes()), before)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_cli_sqlite_import_unavailable_and_memory_unchanged(self):
        path = self.csv("keys.csv", [("a", "b")])
        with mock.patch.dict(sys.modules, {"joinforecast.disk": None}):
            code, out, err = self.cli(path, path, "--storage", "sqlite")
            self.assertEqual((code, out), (2, ""))
            self.assertIn("unavailable", err)
            self.assertEqual(self.cli(path, path)[0], 0)

    def test_cli_temp_dir_requires_opt_in(self):
        path = self.csv("keys.csv", [("a", "b")])
        code, out, err = self.cli(path, path, "--temp-dir", str(self.parent))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("requires --storage sqlite", err)
        self.assertEqual(list(self.parent.iterdir()), [])
