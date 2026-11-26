# joinforecast

Count CSV join rows before a many-to-many merge eats your memory.

[![Tests](https://github.com/anishpentyala/joinforecast/actions/workflows/tests.yml/badge.svg)](https://github.com/anishpentyala/joinforecast/actions)

## Quickstart

```sh
python -m pip install joinforecast
printf 'customer_id\n001\n001\n002\n' > orders.csv
printf 'customer_id\n001\n001\n003\n' > customers.csv
joinforecast orders.csv customers.csv --left-key customer_id
```

This prints a full JSON report. Selected fields for these exact files:

```json
{"predicted_rows": {"inner": 4, "left": 5, "right": 5, "full": 6, "left_anti": 1, "right_anti": 1}}
```

The four matching rows come from two orders times two customer records for `001`.
No join is built, and neither input is changed. Add `--expect many-to-one` to make
repeated lookup keys fail the contract (exit 1, with a complete report).

Install from [PyPI](https://pypi.org/project/joinforecast/). Python 3.9 or later is required.

## Details

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

The source checkout is optional; the quickstart above installs from PyPI.

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
forecast with contract violations; **2** invalid/incomplete input, bad options,
or output-delivery failure. A successful report write must accept the complete
text and flush before a normal verdict is returned. Help, version and argument
messages use the same checked output path. If output fails, partial bytes may
already have escaped; discard them, since they are not a delivered forecast.
Diagnostics are best effort if stderr fails too. Embedded `main(argv)` leaves
caller streams open and unchanged. The executable wrappers own shutdown and
avoid retrying failed flushes that would otherwise turn exit 2 into exit 120.
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

## Frequency-scan implementation (0.2.1)

After full profile validation, forecasting visits each left key once to count
matches, check left uniqueness and maintain the bounded top-contributor heap.
Each joinable left key needs one right-frequency lookup, regardless of `top`.
Equal-contribution ties still prefer the left input's first-seen key. Validation
and the right uniqueness scan remain complete, even with `top=0`.

The SQLite CSV parser encodes each key once, tries an insert, then increments
existing counts in SQL. It no longer reads a frequency back into Python for
each CSV row. The private parser callback is supplied explicitly by the SQLite
backend; ordinary mapping assignment still replaces a value rather than adding
to it. No SQLite `RETURNING` support is needed.

These are operation-count changes, not a promise of a particular speedup.
SQLite is still an opt-in lower-memory tradeoff and can be slower than the
in-memory backend. High `top` values retain up to that many contributor entries;
there is no hard process-memory guarantee. Parsing, null handling, report fields,
validation and contract failures are unchanged. Synthetic tests compare complete
reports and stable ties, and independently enumerate row pairs for small cases.


## Combined validation and aggregation (0.2.2)

Forecasting now validates frequencies while using them, rather than scanning
both complete mappings once for validation and again for aggregation. Each
mapping's `items()` is traversed once. The right profile is fully checked while
counting duplicate keys, then the left profile is checked while counting matches
and selecting contributors. Totals are checked after each traversal. An invalid
last key or inconsistent row total still raises an error, including with
`top=0`; no partial forecast is returned.

Accepted inputs produce the same complete reports, null semantics, stable ties,
and contract results. If several inputs or options are invalid, which error is
reported first can change. Profiles and mappings are not atomic snapshots, so do
not mutate them during a forecast.

This removes two full SQLite key-decoding passes. In a local Linux CPython 3.14
measurement of the forecast alone over two 100,000-distinct-key profiles, the
median of three fresh-process runs changed from 0.718 to 0.564 seconds for disk
profiles. The in-memory case was slower, from 0.079 to 0.085 seconds. Input parsing
was outside that timing; peak process RSS was similar. These are synthetic local
measurements, not a universal speed or memory guarantee.

### CSV input boundary

Both `read_csv` and `sqlite_csv` require regular CSV files. Ordinary symlinks to
regular files are allowed. The parser checks the opened descriptor before
reading; on systems with `O_NONBLOCK`, a FIFO without a producer is rejected
without waiting for one. Devices, directories and missing inputs fail with
`InputError` (CLI exit 2, no forecast on stdout). SQLite temporary profiles are
cleaned up on these failures, including a bad right input after the left loaded.

This is not a universal read timeout. Opening or reading a regular file on a
slow filesystem can still wait, and a regular file may change while it is read.
Pipes and process-substitution streams are not supported CSV inputs. CSV text,
BOM handling, null policy and complete-input validation for regular files are
unchanged.

## Inspect unmatched key groups

```sh
joinforecast left.csv right.csv --left-key id --unmatched-top 5
```

Opt in to `unmatched_keys` without changing forecast or contract semantics.
The API option is `forecast(left, right, unmatched_top=5)`. Omitting it keeps the
existing report shape; zero includes group totals but no key-value witnesses.
The limit must be a nonnegative integer, independently of `top` for matched-key
contributors. Each side lists at most that many unmatched key groups by row
frequency, with first-seen order breaking ties. It also reports all unmatched
`key_groups` and `null_excluded_rows`, not just the displayed groups.

Each witness has `key`, `rows` and `reason`: `NULLS_NEVER` means a null component
prevents equality under the explicit `never` policy, even if the same tuple
occurs on the other side. `NO_EQUAL_KEY` means no equal joinable tuple exists on
the other side. With `nulls="match"`, null components can join and the latter
reason may still apply to absent null-containing tuples. A zero null-excluded
count is not proof all rows match. Existing anti-join row totals remain exact.

These are key-frequency groups, not source-row samples or a diagnosis of why
matches are missing. Values may be sensitive and appear in JSON stdout. No
normalization, inferred spelling correction, join materialization or input edits
occur. Memory and SQLite modes use the same logic and tie ordering. The option
adds two full profile scans and opposite-side lookups, retaining at most the
requested witnesses per side; it does not bound total RAM or lookup cost.

### Explicit Python API option collections

`read_csv` and `sqlite_csv` require `columns` to be an iterable of nonempty,
distinct column-name strings, not one bare string or bytes value. Use
`columns=['id']`, not `columns='id'`. Previously the latter could silently select
separate columns `i` and `d` if both happened to exist. Likewise use
`null_values=['NA']`, not `null_values='NA'`: a bare string previously split into
single-character null tokens. These ambiguous forms now raise `InputError`
before opening the CSV, as do non-string collection members and non-string or
invalid delimiters. SQLite temporary files are cleaned up on these errors.

Lists, tuples and one-shot generators are accepted. An empty null-token
collection still disables null recognition, and explicitly listed
single-character tokens remain valid. Duplicate null tokens remain harmless.
Collections are consumed into memory before reading; this is not a bound on
arbitrary or infinite iterables. The CLI already builds explicit lists, so its
options, valid CSV parsing and report schema do not change. This stricter API
input boundary is source-only, not a new package-index release.

## Optional distinct-key byte budget

Use `--max-key-bytes N` to limit the total UTF-8 string payload across distinct
normalized key tuples **per input file**. The default is no byte budget. The
library accepts `max_key_bytes=N` in both `read_csv()` and `sqlite_csv()`.

```sh
joinforecast left.csv right.csv --left-key id --max-key-bytes 16777216
```

Each distinct tuple is charged once. Repeated rows do not add bytes; the same
string in two different tuples is charged in each tuple. Composite components
are summed without separators. Configured null tokens become `None` before
counting and contribute zero bytes. No Unicode normalization is applied, and
UTF-8 bytes, rather than character counts, determine the charge. For example,
`("雪", "😀")` costs 7 bytes; a budget of 7 accepts it, while 6 refuses it.
A zero budget accepts header-only inputs and zero-payload keys.

The check runs before an over-budget new key enters the frequency index. Both
backends fail with exit 2 and no JSON forecast, rather than returning partial
counts. SQLite temporary profiles are removed on normal error unwinding as
usual. Accepted-input reports, null behavior and join counts are unchanged.
The budget is independent for left and right inputs, not combined across them.

This is **not a process-memory, disk-usage or total-input-byte limit**. Python
objects, tuple/index overhead, SQLite JSON encoding and journals, headers,
non-key fields and parser buffers are not charged. A row is already parsed when
the check runs, and even a rejected candidate key temporarily occupies memory.
The row and distinct-key caps still apply, including to empty/null keys. The
optional budget adds a membership lookup for each row, which may slow SQLite
input parsing. Omitting it keeps the existing parser lookup path.
