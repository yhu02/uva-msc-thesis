# How to run experiments

This guide assumes you have a cluster and `KUBECONFIG` exported (see
[Set up a cluster](set-up-a-cluster.md)).

## Initialize the infrastructure (once per cluster)

```bash
uv run chaosprobe init
```

Installs Helm, LitmusChaos, ChaosCenter, metrics-server, Prometheus, Neo4j, and
the in-cluster image registry that Rust `cmdProbe` images are pushed to. There
is no external-registry option — `--skip-registry` only makes sense if your
scenario has no Rust probes, since they require the in-cluster registry (see
[Add a Rust probe](add-a-rust-probe.md)).

## Run the placement-strategy matrix

```bash
uv run chaosprobe run -n online-boutique
```

By default this runs the experiment once per strategy:
`baseline, default, colocate, spread, adversarial, random, best-fit,
dependency-aware`. Each run deploys the workload, applies the placement, injects
the fault, and collects recovery/latency/resource metrics + probe verdicts into
Neo4j and a `summary.json`.

The `summary.json` (and charts) are written to the run's output directory;
override its location with `-o/--output-dir`. That file is the input to every
[analysis command](analyze-results.md) — the `<run-output>/summary.json` paths
below refer to it.

### Common variations

```bash
# Specific strategies only
uv run chaosprobe run -n online-boutique -s colocate,spread

# Multiple iterations (needed for statistical power — see `stats` / `power`)
uv run chaosprobe run -n online-boutique -i 5

# Ramp load profile during chaos
uv run chaosprobe run -n online-boutique --load-profile ramp

# Skip chart generation
uv run chaosprobe run -n online-boutique --no-visualize
```

For the `random` strategy each iteration uses a different seed
(`base_seed + iter - 1`), so N iterations sample the seed-variance distribution
rather than repeating one placement. Override the base with `--seed`.

### Multi-fault matrix

Run every strategy once per fault class — holding placement, target, and probes
constant while varying only the fault — by passing multiple experiment files:

```bash
uv run chaosprobe run -n online-boutique \
    -e scenarios/online-boutique/pod-delete.yaml \
    -e scenarios/online-boutique/cpu-hog.yaml \
    -i 5
```

This is the recommended invocation for systematic comparison: it isolates the
fault class as the independent variable.

## Compare before/after a fix

```bash
uv run chaosprobe compare run-baseline-001 run-afterfix-001 \
    --neo4j-uri bolt://localhost:7687
```

## Provision only (no experiments)

```bash
uv run chaosprobe provision <scenario-dir>   # apply manifests, run nothing
```

## Clean up

```bash
uv run chaosprobe cleanup <namespace> --all  # remove provisioned resources
uv run chaosprobe delete -n <namespace>      # remove ALL ChaosProbe infra
```

## Next

- [Analyze results](analyze-results.md) — turn the `summary.json` into stats and
  a recommendation.
- All `run` flags: [CLI reference → run](../reference/cli.md#run).
