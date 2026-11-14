# joinforecast

Find a many-to-many row explosion before a CSV equality join, without building
that join or uploading either file.

`joinforecast` counts key frequencies and reports exact inner, left, right, full,
and anti-join row counts. It shows the largest matched-key contributions,
unmatched input rows, duplicate joinable keys, and optional contract violations.
It uses only Python's standard library. Python 3.9 or later is required.

## Install from source

```sh
git clone https://github.com/anishpentyala/joinforecast.git
cd joinforecast
python -m pip install .
```

This is a source-installable tool, not a claimed PyPI release.

## Catch a broken lookup

```sh
joinforecast examples/orders.csv examples/customers.csv \
  --left-key customer_id --expect many-to-one --require-match left
```

The example has two orders for `001` and two customer records for `001`.
Their key alone contributes **four** output rows. With the default null policy,
counts are inner **5**, left **7**, right **7**, and full **9**. Two orders have
no match: customer `003` and the empty customer ID. The command exits **1** because
the right keys are not unique and some left rows have no match. The report is
still complete, machine-readable JSON.

Without contracts, a valid forecast exits 0, even when it finds duplicates.
This is a preflight, not a declaration that a join is safe for your analysis.

## Specify the key and null semantics

```sh
# Composite key; map differently named columns in order.
joinforecast left.csv right.csv \
  --left-key account --left-key period \
  --right-key customer --right-key month --nulls never

# Matching null components; explicitly recognize empty fields and NA as null.
joinforecast left.csv right.csv --left-key id \
  --nulls match --null-value '' --null-value NA
```

- Keys are **exact CSV strings**. `001`, `1`, ` 1`, and `1.0` stay different.
  No numeric conversion, trimming, case folding, locale rules, or date parsing.
- Only empty fields are null by default. `--null-value` can be repeated and
  **replaces**, rather than extends, that default. `--null-value NA` makes
  an empty string an ordinary key. The library can use `null_values=()` for
  no null tokens.
- `--nulls never` is SQL equality behavior: any null component prevents a match.
- `--nulls match` matches nulls at corresponding key positions. This models
  null equality, **not a complete pandas CSV-loader simulation**. Set tokens and
  types to match the loader you actually use. pandas warns that its null-key
  matches differ from SQL: [pandas.merge documentation](https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.merge.html).
- UTF-8 and UTF-8 BOM input only. Comma delimiter by default; `--delimiter ';'`
  is available. CSV quoting and multiline fields are supported.
- Blank/duplicate header names, missing key columns, ragged rows (including
  blank data records), malformed quoting, and invalid UTF-8 are errors.
  A header-only file is valid and has zero data rows.

No missing rows are silently dropped, sampled, or counted as success.

## Contracts and resource limits

| Option | Meaning |
|---|---|
| `--expect one-to-one` | Both sides' joinable keys must be unique |
| `--expect one-to-many` | Left joinable keys must be unique |
| `--expect many-to-one` | Right joinable keys must be unique |
| `--expect many-to-many` | No uniqueness constraint |
| `--require-match left/right/both` | All rows on those sides must have a match |
| `--max-output-rows N` | Each of the four materializing join forecasts must be at most N |
| `--max-input-rows N` | Per-input row cap, default 10,000,000 |
| `--max-keys N` | Per-input distinct-key cap, default 1,000,000 |
| `--top N` | Return at most N matched-key contributors, default 10 |

Contracts check all joinable keys, even unmatched ones. Under `never`, null keys
are excluded from uniqueness checks, but still count as unmatched rows. Under
`match`, null keys participate normally. This is an explicit contract definition,
not a promise to duplicate a particular database's uniqueness constraint rules.

Input limits stop with exit 2 and **no JSON forecast**, rather than returning a
plausible partial count. They are safety bounds, not hard RAM guarantees: memory
is proportional to distinct keys and their lengths. One huge field is also
subject to Python's CSV parser field-size limit. Output row arithmetic uses
Python integers, with no floating-point rounding or output-row materialization.

Exit codes: **0** complete forecast satisfying requested contracts; **1** complete
forecast with contract violations; **2** invalid/incomplete input or bad options.
JSON `schema_version` is 1. `ok` means only that the requested contracts passed.

## Opt-in disk-backed key frequencies

The default backend keeps key frequencies in memory. If the distinct-key index
is too large for that, use a private temporary SQLite database instead:

```sh
joinforecast large-left.csv large-right.csv --left-key id \
  --storage sqlite --max-keys 5000000 --temp-dir /path/to/private/scratch
```

This uses Python's `sqlite3` module, not a database server. It writes exact key
values and their frequencies to temporary disk. Use a suitable scratch location
for sensitive data. The directory is private to the current user; normal success
and errors close and remove both databases. Removal is **not secure erasure**.
Forced termination or power loss can leave files behind. Other users with elevated
access, disk backups, and filesystem snapshots may still expose the data.

`--temp-dir` selects an existing parent directory, not a database filename. Omit
it to use Python's normal temporary location. It requires `--storage sqlite`.
An unwritable, missing, or full temporary location fails with exit 2 and no JSON
forecast. Neither CSV input is modified. The SQLite backend needs a Python build
with `sqlite3` support; the default memory backend does not.

Counts, null rules, top-contributor tie order, contracts, and input limits stay the
same. The default one-million distinct-key cap still applies: increase `--max-keys`
explicitly if needed. The disk backend reduces Python memory spent on the key
index, but adds database and disk I/O cost and may be much slower. SQLite's cache
is configured to about 8 MiB per profile, **not a hard process-memory limit**.
Long fields, database overhead, and `--top` also consume memory. Database storage
includes an index and journal; allow more space than the CSV key bytes alone.

Library callers use a context manager and keep every forecast inside it:

```python
from joinforecast import forecast
from joinforecast.disk import sqlite_csv

with sqlite_csv('orders.csv', ['customer_id']) as left:
    with sqlite_csv('customers.csv', ['id']) as right:
        report = forecast(left, right, expectation='many-to-one')
# The JSON-compatible report is independent of the closed databases.
```

Disk profile frequencies are a mapping, not an in-memory `Counter`. Access the
profile only inside its context; it cannot be used after cleanup. Normal
`read_csv` still returns an in-memory `Counter` profile as before.

## Library

```python
from joinforecast import read_csv, forecast

left = read_csv('orders.csv', ['customer_id'])
right = read_csv('customers.csv', ['id'])
report = forecast(left, right, nulls='never', expectation='many-to-one')
print(report['predicted_rows']['left'])
```

Input files are read only. There is no network code or telemetry.
The opt-in SQLite backend writes key frequencies to temporary disk as described above. Reports include
actual key values in `top_contributors`; treat redirected JSON as sensitive when
your CSV contains private identifiers. `--top 0` omits those examples.

## How counts work

For every joinable shared key with L left rows and R right rows, the inner join
contribution is L × R. Each left row with no right match adds one row to the left
and full joins; each unmatched right row adds one row to the right and full joins.
Composite keys are tuples, not delimiter-joined strings, so embedded separators
cannot create false matches. Top contributors are bounded with a heap.

This does not perform the join, validate non-key columns, estimate output bytes,
resolve suffix conflicts, check aggregates, or predict range/as-of/fuzzy/collation
joins. It cannot infer the semantics of a later loader or changed input files.
Run the preflight on the exact inputs and settings used by the eventual join.

## Develop

```sh
python -m pip install -e . coverage ruff build
python -m unittest discover -s tests -v
coverage run -m unittest discover -s tests
coverage report
ruff check src tests
ruff format --check src tests
python -m build
```

The test suite includes a seeded independent SQLite relational oracle for
composite-key joins under SQL equality and null-equal semantics, hand-computed
fanout, huge exact counts, malformed CSV rejection, contract failures, CLI exit
codes, and input byte preservation. Disk tests compare complete reports with the
memory backend, use an independent relational oracle, and exercise private-file
cleanup and real storage-full/read-interruption failures. CI tests the installed package, not a
materialized large join. No database service is required.
