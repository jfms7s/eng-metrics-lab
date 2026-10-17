# eng-metrics-lab

`quality-report` measures code duplication, dead code and cyclomatic complexity for a repository in CI and emits a JSON report.

## Tests

```bash
uv venv --python 3.12 .venv
uv pip install -e '.[dev]'
.venv/bin/pytest -m "not tools and not db"
```
