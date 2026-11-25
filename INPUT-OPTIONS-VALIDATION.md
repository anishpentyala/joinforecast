# Explicit CSV API option validation, November 25, 2026

Base public main0d9d108cf5c17f3abed30798536f227ff02716be. Reproduced PythonAPI
read_csv(path,'id') silently counting columns i,d instead of id when all three
headers exist. Likewise null_values='NA' splits N/A, not the intended tokenNA.
These can produce plausible wrong forecasts rather than fail loudly.

Reject bare str/bytes columns/null_values, noniterable values, invalid column
members/duplicates/empty collections, nonstring null members and invalid delimiter
types. Validate before opening CSV. Preserve valid list/tuple/generator input,
explicit empty null sets, duplicate null tokens, explicit onecharacter tokens,
exact text identity and all CLI behavior. Shared parser covers memory and SQLite;
SQLite still creates then cleans its private temporary storage before this
validation. No claim validation precedes every storage setup operation.

93 unittests PASS on actualPython3.9.25,3.13.18,3.15.2. Original88 retained,
including2000 independent unmatched relational-oracle cases and existing parser/
frequency/disk/storage/output tests. Five new methods:22 invalid column/backend
subcases,16 null-token/backend,18 delimiter/backend; generators and whole-report
memory/SQLite parity; explicit empty and onecharacter nulls and onecharacter
column names. Invalid-option tests patch CSV open to fail if reached, and check
SQLite scratch cleanup. Reports preserve actualNA/N/A/empty distinctions.

Regression file against base gives22 failures18 errors. Four mutants killed:
allowbarecolumns4failures;allowbarenulls4failures;skipcolumnmembervalidation6failures
2errors;skipnullmembervalidation4failures2errors. Source restored. Ruffcheck and
formatPASS after newtest formatting correction. First systemPythonrun found
missing installed distribution metadata in inherited console-entrypoint test,
not code failure; full installed environments then PASS on all3versions.
Sdist/wheelbuildPASS. Fresh neutral3.15 environment installs builtwheel and all93
PASS from/tmp. No tests weakened or skipped intentionally, no dependency/version
change, noPyPI/tag/promo, no new external API use.

Review focus: does rejecting ambiguous formerly accepted API input correctly
protect semantics; ordering/short-circuit validation avoids rawTypeErrors for
invalid collection members; generator consumption once; storagecleanup; no valid
CSV behavior or schema change. No public mutation before independentPASS.
