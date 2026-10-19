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
    steps:
      - uses: actions/checkout@v4
      - uses: jfms7s/eng-metrics-lab/action@v0
```

| Input | Default |
| --- | --- |
| `image` | `ghcr.io/jfms7s/quality-report:v0` |
| `artifact-name` | `quality-report` |

Besides the report artifact, the action uploads `.quality/details.json` (the offending
functions and clones) as `<artifact-name>-details`, and a final step (`if: always()`)
appends `quality-report feedback` to the job's step summary: headline numbers, the
ratchet result and the worst offenders. The feedback is advisory and never fails the job.
The action also passes the host workspace path into the container as
`QUALITY_HOST_WORKSPACE`, so coverage fragments written by host-side tests match repo files.
