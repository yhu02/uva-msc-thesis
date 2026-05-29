# Example fixtures

Worked-example summaries you can run every defender-facing
`chaosprobe` command against — no cluster required.

## Files

- **`example-summary.json`** — Two strategies (`spread`, `colocate`),
  5 iterations each. Designed so the resilience CIs are disjoint and
  the pairwise stats produce a significant result, exercising every
  formatter without needing a live run.

## Try it

```bash
chaosprobe doctor    -s examples/example-summary.json
chaosprobe summarize -s examples/example-summary.json
chaosprobe stats     -s examples/example-summary.json --metric resilience
chaosprobe stats     -s examples/example-summary.json --all-metrics --markdown
chaosprobe inspect   -s examples/example-summary.json --strategy colocate -i 3
chaosprobe power     -s examples/example-summary.json --metric resilience
chaosprobe report    -s examples/example-summary.json -o /tmp/report.md
chaosprobe export    -s examples/example-summary.json --jsonl -o /tmp/runs.jsonl
chaosprobe diff      --a examples/example-summary.json --b examples/example-summary.json
```

The diff line is intentionally a self-diff — useful to confirm the
`stable (CIs overlap)` flag fires for every metric in a no-change
comparison.
