# Validation of 0.2.2 combined frequency traversal

Private candidate validation, 2026-11-14. Public CI is not claimed here.
Baseline: merged 0.2.1 source at c610813217fc8b605247a47f07f44c3b6d409c93.

## Mechanism and scope

The profile-shape check remains separate. A checked item generator validates
key/count types and positivity, accumulates row and null-row totals, yields each
frequency, and checks those totals after exhaustion. Forecast fully consumes
right frequencies while counting duplicates, then left frequencies while
aggregating matches and contributors. There is no early successful return.
Each items traversal is used for validation and calculation together.

This removes two complete frequency iterations and, for SQLite, two complete
ordered key SELECTs and JSON-decoding passes. Each joinable left key still needs
one right-frequency lookup. Parser, disk storage, SQL increment operations,
report schema, null rules, ties, and contract meanings are unchanged. Profile
values must not mutate while forecasting. No snapshot or new iterator protocol
support is promised. Error ordering can change for multiply-invalid input or
invalid options combined with invalid frequencies.

## Checks performed

- 59 unittest methods pass on local CPython 3.9.25, 3.10.12, 3.11.17,
  3.13.17, 3.14.9, and 3.15.1.
- The original 54 test methods are retained. The existing operation-count test
  changes its two scan assertions from two to one, which is the intended new
  mechanism. Five new methods cover one traversal, actual SQLite SELECT counts,
  invalid count and key tails, and totals after exhaustion on either side,
  including top=0 and both null modes.
- The five new methods against the actual unchanged baseline produce two
  assertion failures, no errors: right items is traversed again, and actual
  SQLite key SELECT count is four rather than two. The three rejection controls
  already pass on the baseline. This is an efficiency change, not a newly fixed
  partial-validation bug.
- 12,000 complete report equalities against baseline from 1,500 random profiles,
  two null modes and four top sizes (seed 612794). Contracts, match requirements
  and output caps vary too. Existing independent literal row-pair and SQLite
  relational oracles remain in the suite.
- Four deliberately broken variants each cause assertion failures without test
  errors: omitted totals check (8 failures), accepted zero (2 diagnostic
  assertion failures, still rejected by totals), omitted last left key (394
  failures), and omitted null-key right duplicates (81 failures). These are full
  suite failure counts, including subtests, not counts of independent cases.
  The original source was restored and all 59 methods pass again.
- Ruff 0.17.5 checks and formatting pass for src and tests, seven Python files.
- Wheel and sdist build as 0.2.2; packaged source bytes match source checkout.
  Installed wheel tested from a neutral directory on CPython 3.9 and 3.14,
  59 pass on each.

## Local timing, not a general performance promise

Linux CPython 3.14.9, median of three fresh processes for each implementation and
backend, 100,000 distinct matching one-column keys in each profile, top=10.
The timed region is forecast only; construction and CSV parsing occur first.

| Backend | 0.2.1 seconds | Candidate seconds |
| --- | ---: | ---: |
| In-memory | 0.079082 | 0.085165 |
| SQLite | 0.718195 | 0.564392 |

The memory case is slower. Peak process RSS is similar and includes input setup,
so these numbers do not establish a memory reduction. This is synthetic local
measurement, not a portable benchmark, end-to-end parsing speed claim, or
universal improvement. Smaller inputs and different key distributions can vary.
