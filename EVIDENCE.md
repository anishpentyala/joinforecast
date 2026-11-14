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
