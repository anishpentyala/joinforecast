# Verification notes

Local verification, November 13, 2026, Linux:

- 26 unittest methods, including a deterministic SQLite execution oracle with
  240 random composite-key input pairs under each of two null policies. It
  compares all six row forecasts, plus actual SQLite left-join counts.
- Python 3.9.25 source execution passed. Python 3.11.17 and 3.14.9 installed-wheel
  runs passed all 26 methods. The final two methods add API arity and
  direct CLI-error controls without changing production code.
- Mutation control replacing matched-key multiplication with addition fails the
  relational oracle and hand-computed cases; restoring production passes.
- Source builds wheel and sdist. Both production modules in the wheel match the
  source bytes and the MIT license is included. No registry publication implied.
- Ruff check and format pass. Statement/branch coverage is measured locally;
  subprocess entry-point paths are tested separately and not included in the
  parent's coverage recording. No 100% coverage claim.

These are local checks, not claims of hosted CI, adoption, database query-plan
performance, or operating-system portability beyond the tested environments.
SQLite is an independent equality-join oracle, not a pandas loader oracle.

The motivating null-key difference and cardinality-validation semantics were
checked against the official pandas.merge documentation on November 13, 2026:
https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.merge.html

The tool deliberately preserves CSV strings and explicit null tokens rather than
silently claiming to predict pandas type inference or default NA parsing.

## Opt-in disk backend (0.2.0)

The additional backend is an existing-tool improvement, not a separate tool.
It shares the strict CSV parser and row-forecast arithmetic with the memory
backend. The default backend and JSON output are unchanged.

Local Linux verification on the revised source:

- 44 unittest methods, including the original 26. The 18 added methods include
  270 complete-report comparisons across null modes/tokens/contracts, a separate
  100-pair relational execution oracle under both null modes, stable top-K ties,
  mapping encoding collisions, private permissions and cleanup, CLI parity and
  byte preservation, and real database-full/read-interruption failures.
- Actual Python 3.9.25 source and Python 3.11.17, 3.13.17, and 3.14.9 installed
  packages pass all methods. No Windows, macOS, or power-loss cleanup claim.
- Fifty seeded input-pair/null-mode comparisons against the installed original
  0.1.0 CLI match exact default-backend exit codes, stdout, and stderr.
- Changing disk iteration to key-sorted order instead of first-seen order causes
  three new-test failures. Restoring first-seen order passes. Replacing JSON key
  encoding with delimiter concatenation is also rejected by the tests.
- Local coverage: core and disk modules 100% statement/branch; CLI 96%; combined
  99%, with only the direct-entry guard missed in the in-process recording.
  Ruff check/format and wheel/sdist builds pass.

An isolated-process Python 3.9 probe used one synthetic CSV as two independent
profiles, with 80-character distinct keys and `top=0`. At 500,000 rows per side,
all six counts agree: inner/left/right/full 500,000; both anti joins zero.
Memory profiles peaked at 267,800 KiB RSS and took 1.82 seconds; disk profiles
peaked at 38,352 KiB and took 18.01 seconds in that run. At only 20,000 keys,
the disk backend used more memory than the default backend. These are local
tradeoff observations, not portable benchmarks or hard resource bounds.
