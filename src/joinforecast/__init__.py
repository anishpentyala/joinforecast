"""Exact equality-join row forecasts from CSV key frequencies."""

import csv
import heapq
import os
import stat
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__version__ = "0.2.2"


class InputError(ValueError):
    """Input could not be completely and unambiguously analyzed."""


@dataclass
class Profile:
    counts: Mapping
    rows: int
    null_rows: int
    columns: tuple


def read_csv(
    path, columns, *, null_values=("",), delimiter=",", max_rows=10000000, max_keys=1000000
):
    """Read selected keys as exact strings; no trimming or type inference.

    Empty fields are null by default. Configure null_values explicitly to match
    the intended loader. Reject malformed or incomplete input, never sample.
    """
    return _read_csv(path, columns, Counter(), null_values, delimiter, max_rows, max_keys)


def _read_csv(path, columns, counts, null_values, delimiter, max_rows, max_keys, *, increment=None):
    """Shared complete-input parser; counts may be a disk-backed mapping."""
    if isinstance(columns, (str, bytes)):
        raise InputError("Key columns must be an iterable of column names, not one string")
    try:
        columns = tuple(columns)
    except TypeError as error:
        raise InputError("Key columns must be an iterable of column names") from error
    if (
        not columns
        or any(not isinstance(name, str) or not name for name in columns)
        or len(set(columns)) != len(columns)
    ):
        raise InputError("Key columns must be nonempty distinct strings")
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in "\r\n\0":
        raise InputError("Delimiter must be one non-newline character")
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (max_rows, max_keys)):
        raise InputError("Resource limits must be nonnegative")
    if isinstance(null_values, (str, bytes)):
        raise InputError("Null values must be an iterable of strings, not one string")
    try:
        null_values = tuple(null_values)
    except TypeError as error:
        raise InputError("Null values must be an iterable of strings") from error
    if any(not isinstance(value, str) for value in null_values):
        raise InputError("Null values must contain only strings")
    null_values = frozenset(null_values)
    rows = null_rows = 0
    try:

        def nonblocking(name, flags):
            return os.open(name, flags | getattr(os, "O_NONBLOCK", 0))

        with open(Path(path), encoding="utf-8-sig", newline="", opener=nonblocking) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InputError("CSV input must be a regular file")
            reader = csv.reader(stream, delimiter=delimiter, strict=True)
            header = next(reader, None)
            if not header or any(not name for name in header):
                raise InputError("CSV needs a nonempty header with no blank column names")
            if len(set(header)) != len(header):
                raise InputError("Duplicate CSV column names")
            missing = [name for name in columns if name not in header]
            if missing:
                raise InputError("Missing key columns: " + ", ".join(missing))
            positions = [header.index(name) for name in columns]
            for row in reader:
                if len(row) != len(header):
                    raise InputError("Ragged row ending at line %d" % reader.line_num)
                rows += 1
                if rows > max_rows:
                    raise InputError("Row limit exceeded; no complete forecast available")
                key = tuple(None if row[i] in null_values else row[i] for i in positions)
                if None in key:
                    null_rows += 1
                if increment is None:
                    counts[key] = counts.get(key, 0) + 1
                else:
                    increment(key)
                if len(counts) > max_keys:
                    raise InputError("Distinct-key limit exceeded; no complete forecast available")
    except (OSError, UnicodeError, csv.Error) as error:
        raise InputError(str(error)) from error
    return Profile(counts, rows, null_rows, columns)


def _profile_shape(profile):
    if (
        not isinstance(profile, Profile)
        or not profile.columns
        or len(set(profile.columns)) != len(profile.columns)
        or any(not isinstance(name, str) or not name for name in profile.columns)
        or any(
            isinstance(n, bool) or not isinstance(n, int) or n < 0
            for n in (profile.rows, profile.null_rows)
        )
    ):
        raise InputError("Expected a Profile with key columns")


def _validated_items(profile):
    rows = null_rows = 0
    for key, count in profile.counts.items():
        if (
            not isinstance(key, tuple)
            or len(key) != len(profile.columns)
            or any(value is not None and not isinstance(value, str) for value in key)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count <= 0
        ):
            raise InputError("Invalid key frequency")
        rows += count
        if None in key:
            null_rows += count
        yield key, count
    if rows != profile.rows or null_rows != profile.null_rows:
        raise InputError("Profile row totals do not match key frequencies")


def forecast(
    left,
    right,
    *,
    nulls="never",
    top=10,
    expectation=None,
    max_output_rows=None,
    require_match="none",
    unmatched_top=None,
):
    """Forecast equality joins without materializing the output.

    nulls='never': any null component prevents a match (SQL equality).
    nulls='match': corresponding null components compare equal. Not an implicit
    pandas CSV-loader simulation: string parsing and null tokens remain explicit.
    Uniqueness contracts apply to joinable keys, including unmatched keys.
    """
    _profile_shape(left)
    _profile_shape(right)
    if len(left.columns) != len(right.columns):
        raise InputError("Left and right key arities differ")
    if nulls not in ("never", "match"):
        raise InputError("nulls must be never or match")
    if isinstance(top, bool) or not isinstance(top, int) or top < 0:
        raise InputError("top must be a nonnegative integer")
    if expectation not in (None, "one-to-one", "one-to-many", "many-to-one", "many-to-many"):
        raise InputError("Unknown cardinality expectation")
    if require_match not in ("none", "left", "right", "both"):
        raise InputError("Unknown match requirement")
    if max_output_rows is not None and (
        isinstance(max_output_rows, bool)
        or not isinstance(max_output_rows, int)
        or max_output_rows < 0
    ):
        raise InputError("Output row limit must be a nonnegative integer")

    if unmatched_top is not None and (
        isinstance(unmatched_top, bool) or not isinstance(unmatched_top, int) or unmatched_top < 0
    ):
        raise InputError("unmatched_top must be a nonnegative integer or None")

    def joinable(key):
        return nulls == "match" or None not in key

    inner = matched_left = matched_right = shared = many_many = 0
    duplicate_left = 0
    largest_heap = []
    duplicate_right = sum(1 for k, n in _validated_items(right) if n > 1 and joinable(k))
    for ordinal, (key, ln) in enumerate(_validated_items(left)):
        duplicate_left += ln > 1 and joinable(key)
        rn = right.counts.get(key, 0) if joinable(key) else 0
        if rn:
            inner += ln * rn
            matched_left += ln
            matched_right += rn
            shared += 1
            many_many += ln > 1 and rn > 1
            if top:
                # Earlier left first-seen keys win equal-contribution ties.
                # Unique ordinal precedes the key, so mixed None/string values
                # are never compared by the heap.
                entry = (ln * rn, -ordinal, ln, rn, key)
                if len(largest_heap) < top:
                    heapq.heappush(largest_heap, entry)
                elif entry > largest_heap[0]:
                    heapq.heapreplace(largest_heap, entry)
    unmatched_left = left.rows - matched_left
    unmatched_right = right.rows - matched_right
    predictions = {
        "inner": inner,
        "left": inner + unmatched_left,
        "right": inner + unmatched_right,
        "full": inner + unmatched_left + unmatched_right,
        "left_anti": unmatched_left,
        "right_anti": unmatched_right,
    }

    largest = [
        {
            "key": list(key),
            "left_rows": ln,
            "right_rows": rn,
            "joined_rows": product,
            "many_to_many": ln > 1 and rn > 1,
        }
        for product, _, ln, rn, key in sorted(largest_heap, reverse=True)
    ]
    violations = []
    if expectation in ("one-to-one", "one-to-many") and duplicate_left:
        violations.append("Left joinable keys are not unique")
    if expectation in ("one-to-one", "many-to-one") and duplicate_right:
        violations.append("Right joinable keys are not unique")
    if require_match in ("left", "both") and unmatched_left:
        violations.append("Left rows have no match")
    if require_match in ("right", "both") and unmatched_right:
        violations.append("Right rows have no match")
    if max_output_rows is not None:
        # A cap applies to every reported materializing join, not anti joins.
        for how in ("inner", "left", "right", "full"):
            if predictions[how] > max_output_rows:
                violations.append("%s join exceeds output row limit" % how)
    report = {
        "schema_version": 1,
        "nulls": nulls,
        "left_rows": left.rows,
        "right_rows": right.rows,
        "left_null_rows": left.null_rows,
        "right_null_rows": right.null_rows,
        "left_key_columns": list(left.columns),
        "right_key_columns": list(right.columns),
        "shared_keys": shared,
        "left_duplicate_joinable_keys": duplicate_left,
        "right_duplicate_joinable_keys": duplicate_right,
        "many_to_many_keys": many_many,
        "matched_left_rows": matched_left,
        "matched_right_rows": matched_right,
        "predicted_rows": predictions,
        "top_contributors": largest,
        "expectation": expectation,
        "require_match": require_match,
        "max_output_rows": max_output_rows,
        "violations": violations,
        "ok": not violations,
    }

    if unmatched_top is not None:

        def contributors(profile, other):
            heap = []
            groups = 0
            null_unmatched = 0
            for ordinal, (key, count) in enumerate(_validated_items(profile)):
                excluded_null = not joinable(key)
                if excluded_null or not other.counts.get(key, 0):
                    groups += 1
                    if excluded_null:
                        null_unmatched += count
                    if unmatched_top:
                        entry = (count, -ordinal, key, excluded_null)
                        if len(heap) < unmatched_top:
                            heapq.heappush(heap, entry)
                        elif entry > heap[0]:
                            heapq.heapreplace(heap, entry)
            return {
                "key_groups": groups,
                "null_excluded_rows": null_unmatched,
                "top": [
                    {
                        "key": list(key),
                        "rows": count,
                        "reason": "NULLS_NEVER" if excluded else "NO_EQUAL_KEY",
                    }
                    for count, _, key, excluded in sorted(heap, reverse=True)
                ],
            }

        report["unmatched_keys"] = {
            "limit_per_side": unmatched_top,
            "left": contributors(left, right),
            "right": contributors(right, left),
            "scope": "Key-frequency groups, not source row samples or a cause of missing matches. "
            "Null exclusion follows the explicit null policy; values may be sensitive.",
        }
    return report
