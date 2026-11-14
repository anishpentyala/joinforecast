"""Command-line interface. JSON stdout, errors stderr, no input mutation."""

import argparse
import json
import sys
from contextlib import ExitStack

from . import InputError, __version__, forecast, read_csv


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Forecast equality-join row counts without building the join."
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument(
        "--left-key", action="append", required=True, help="Repeat for composite keys"
    )
    parser.add_argument("--right-key", action="append", help="Defaults to left key names")
    parser.add_argument("--nulls", choices=("never", "match"), default="never")
    parser.add_argument(
        "--null-value", action="append", help="Repeat; replaces default empty-field null token"
    )
    parser.add_argument("--delimiter", default=",")
    parser.add_argument(
        "--expect", choices=("one-to-one", "one-to-many", "many-to-one", "many-to-many")
    )
    parser.add_argument(
        "--require-match", choices=("none", "left", "right", "both"), default="none"
    )
    parser.add_argument("--max-output-rows", type=int, help="Cap all four reported join sizes")
    parser.add_argument(
        "--max-input-rows", type=int, default=10000000, help="Per file; fail rather than sample"
    )
    parser.add_argument(
        "--max-keys", type=int, default=1000000, help="Per file; fail rather than sample"
    )
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument(
        "--storage",
        choices=("memory", "sqlite"),
        default="memory",
        help="sqlite writes sensitive key values to private temporary disk",
    )
    parser.add_argument("--temp-dir", help="Parent directory for sqlite temporary profiles")
    args = parser.parse_args(argv)
    try:
        options = dict(
            null_values=args.null_value if args.null_value is not None else ("",),
            delimiter=args.delimiter,
            max_rows=args.max_input_rows,
            max_keys=args.max_keys,
        )
        if args.temp_dir is not None and args.storage != "sqlite":
            raise InputError("--temp-dir requires --storage sqlite")
        with ExitStack() as stack:
            if args.storage == "sqlite":
                try:
                    from .disk import sqlite_csv
                except ImportError as error:
                    raise InputError(
                        "SQLite backend is unavailable in this Python build"
                    ) from error

                def load(path, columns):
                    return stack.enter_context(
                        sqlite_csv(path, columns, temp_dir=args.temp_dir, **options)
                    )
            else:

                def load(path, columns):
                    return read_csv(path, columns, **options)

            left = load(args.left, args.left_key)
            right = load(args.right, args.right_key or args.left_key)
            result = forecast(
                left,
                right,
                nulls=args.nulls,
                top=args.top,
                expectation=args.expect,
                max_output_rows=args.max_output_rows,
                require_match=args.require_match,
            )
        result["csv_null_values"] = list(options["null_values"])
        result["delimiter"] = args.delimiter
    except InputError as error:
        print("joinforecast: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
