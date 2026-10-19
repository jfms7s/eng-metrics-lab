# eng-metrics-lab

`quality-report` measures code duplication, dead code, and cyclomatic and cognitive complexity for a repository in CI and emits a JSON report.

Since 0.3.0 it also reports:

- **Cognitive complexity** per function, next to cyclomatic.
- **Coverage** from fragments the test step writes to `.quality/` (`coverage-ts*.lcov`, `coverage-rust*.lcov`, `coverage-py*.lcov`, `coverage-go*.out`). It is null unless every expected language has a fragment.
- **Test gap**: functions whose CRAP score (complexity weighted by missing coverage) is over the threshold.
- **`details.json`**: the per-function detail behind those numbers.
- **`quality-report feedback`**: an advisory summary of what a change made worse; it always exits 0.

## Tests

```bash
uv venv --python 3.11 .venv
uv pip install -e '.[dev]'
.venv/bin/pytest -m "not tools and not db"
```
