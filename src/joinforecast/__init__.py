"""Exact equality-join row forecasts from CSV key frequencies."""

import csv
import heapq
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__version__ = "0.2.0"


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


def _read_csv(path, columns, counts, null_values, delimiter, max_rows, max_keys):
    """Shared complete-input parser; counts may be a disk-backed mapping."""
    columns = tuple(columns)
    if not columns or len(set(columns)) != len(columns):
        raise InputError("Key columns must be nonempty and distinct")
    if len(delimiter) != 1 or delimiter in "\r\n\0":
        raise InputError("Delimiter must be one non-newline character")
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (max_rows, max_keys)):
        raise InputError("Resource limits must be nonnegative")
    null_values = frozenset(null_values)
    rows = null_rows = 0
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
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
                counts[key] = counts.get(key, 0) + 1
                if len(counts) > max_keys:
                    raise InputError("Distinct-key limit exceeded; no complete forecast available")
    except (OSError, UnicodeError, csv.Error) as error:
        raise InputError(str(error)) from error
    return Profile(counts, rows, null_rows, columns)


def _validate_profile(profile):
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
):
    """Forecast equality joins without materializing the output.

    nulls='never': any null component prevents a match (SQL equality).
    nulls='match': corresponding null components compare equal. Not an implicit
    pandas CSV-loader simulation: string parsing and null tokens remain explicit.
    Uniqueness contracts apply to joinable keys, including unmatched keys.
    """
    _validate_profile(left)
    _validate_profile(right)
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

    def joinable(key):
        return nulls == "match" or None not in key

    inner = matched_left = matched_right = shared = many_many = 0
    duplicate_left = sum(1 for k, n in left.counts.items() if n > 1 and joinable(k))
    duplicate_right = sum(1 for k, n in right.counts.items() if n > 1 and joinable(k))
    for key, ln in left.counts.items():
        rn = right.counts.get(key, 0) if joinable(key) else 0
        if rn:
            inner += ln * rn
            matched_left += ln
            matched_right += rn
            shared += 1
            many_many += ln > 1 and rn > 1
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

    def contributors():
        for key, ln in left.counts.items():
            rn = right.counts.get(key, 0) if joinable(key) else 0
            if rn:
                yield {
                    "key": list(key),
                    "left_rows": ln,
                    "right_rows": rn,
                    "joined_rows": ln * rn,
                    "many_to_many": ln > 1 and rn > 1,
                }

    # Keep only top K contributors, rather than sorting or copying all keys.
    largest = heapq.nlargest(top, contributors(), key=lambda row: row["joined_rows"])
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
    return {
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
