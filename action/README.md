# quality-report action

Runs `quality-report` in its container image against the checked-out repo, uploads
`.quality/report.json` as an artifact and fails the job if the ratchet against
`.quality-baseline.json` is violated.

```yaml
jobs:
  quality:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: read   # the image is private
    steps:
      - uses: actions/checkout@v4
      - uses: jfms7s/eng-metrics-lab/action@v0
```

| Input | Default |
| --- | --- |
| `image` | `ghcr.io/jfms7s/quality-report:v0` |
| `artifact-name` | `quality-report` |
