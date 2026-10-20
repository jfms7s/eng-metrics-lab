# eng-metrics-lab

`quality-report` measures code duplication, dead code, and cyclomatic and cognitive complexity for a repository in CI and emits a JSON report.

Since 0.3.0 it also reports:

- **Cognitive complexity** per function, next to cyclomatic.
- **Coverage** from fragments the test step writes to `.quality/` (`coverage-ts*.lcov`, `coverage-rust*.lcov`, `coverage-py*.lcov`, `coverage-go*.out`). It is null unless every expected language has a fragment.
- **Test gap**: functions whose CRAP score (complexity weighted by missing coverage) is over the threshold.
- **`details.json`**: the per-function detail behind those numbers.
- **`quality-report feedback`**: an advisory summary of what a change made worse; it always exits 0.

Since 0.3.1:

- **Rust coverage is production code only.** Lines inside `#[cfg(test)]` items and test functions (`#[test]`, `#[tokio::test]`, ...), modules declared from test code, and files under a crate's `tests/` are left out of coverage and the test gap. `coverage.test_lines_excluded` says how many instrumented lines that removed.
- **`.quality.yml` `languages:`** adds languages that no manifest reveals (scripts CI runs without a `pyproject.toml` or `package.json`), for example `languages: [python]`. The report lists them in `declared_languages`.

Since 0.3.2:

- **Test files are left out of the test gap for every language.** Functions in test files are not scored, and they no longer count as lacking coverage data, so a runner that leaves its tests out (vitest, pytest with `--cov=src`) doesn't look misconfigured. `test_gap.functions_in_test_files` counts them. A test file is one named like a test (`*.test.*` and `*.spec.*` for JS/TS, `test_*.py`, `*_test.py` and `conftest.py`, `*_test.go`) or under a test directory (`test/`, `tests/`, `__tests__/` and `__mocks__/` for JS/TS, `test/` and `tests/` for Python, `testdata/` for Go). Rust test code is still found by the 0.3.1 source scan.
- **Generated protobuf code is excluded by default**, by its generators' file names: `*.pb.go`, `*.pb.gw.go`, `*_pb.ts`, `*_pb.js`, `*_pb2.py`, `*_pb2_grpc.py`. Other generated code (a `gen/` directory, say) goes in the repo's `.quality.yml` `exclude`.

Since 0.4.0:

- **`quality-collector hotspots`** stores each repo's churn × complexity per file for the week (below). The `quality-report` CLI is unchanged.

## Hotspots (`quality-collector hotspots`)

Run weekly by the homelab collector. For each repo in `QUALITY_HOTSPOT_REPOS` (comma-separated, owner `QUALITY_GITHUB_OWNER`) it makes a blob-less clone of the default branch with `GITHUB_TOKEN` (sent as an HTTP header, never in a URL or argument), then:

- **churn**: non-merge commits touching the file in the last 90 days. Bot and release-automation authors are dropped; author names and e-mails are never stored or printed.
- **fix_commits**: those commits whose subject is a Conventional Commits `fix`. Null for the whole repo when under half of its commits in the window follow Conventional Commits.
- **complexity** and **nloc**: lizard's cyclomatic complexity and NLOC summed over the file's functions, with the same excludes as the report (defaults plus the repo's `.quality.yml`). Files lizard doesn't parse are skipped, so YAML- or HCL-only repos get no rows.
- **score** = (churn / max churn) × (complexity / max complexity), maxima over the parsed files; **rank** orders every file with churn.

Rows go to the `hotspots` table under the week's Monday (UTC), replacing that repo's rows for the week. A repo that can't be cloned or measured keeps its earlier weeks and makes the run exit 1.

## Tests

```bash
uv venv --python 3.11 .venv
uv pip install -e '.[dev]'
.venv/bin/pytest -m "not tools and not db"
```
