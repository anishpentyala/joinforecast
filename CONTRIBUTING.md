# Contributing

Report a reproducible input and expected result, using synthetic data rather
than private CSV files. Include the key columns, null tokens, and null policy.

For changes, run the unittest suite, Ruff checks, and a build using the commands
in README.md. Keep parsing and null semantics explicit and backwards compatible.
Do not turn incomplete input into a passing partial forecast. New matching
semantics need independent oracle tests and documentation.
