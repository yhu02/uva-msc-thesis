# CLI reference

Complete list of `chaosprobe` commands. All commands are invoked as
`uv run chaosprobe <command>`. For deeper module/schema reference see
[`../../TECHNICAL.md`](../../TECHNICAL.md); for task-oriented walkthroughs see
the [how-to guides](../index.md).

> This page documents the commonly-used flags. `uv run chaosprobe <command>
> --help` is always the authoritative, complete list for any command.

## Core

| Command | Purpose |
|---|---|
| `status` | Check prerequisites and cluster connectivity. |
| `init [--skip-registry]` | Install all infrastructure (Helm, LitmusChaos, ChaosCenter, metrics-server, Prometheus, Neo4j, in-cluster registry). |
| `provision <scenario-dir>` | Deploy a scenario's manifests only — run no experiments. |
| `compare <base> <fix> --neo4j-uri <uri>` | Compare two runs by Neo4j run ID. |
| `cleanup <namespace> --all` | Remove provisioned resources in a namespace. |
| `delete -n <namespace>` | Delete **all** ChaosProbe infrastructure. |

## run

Runs the placement-experiment matrix. Default strategies:
`baseline, default, colocate, spread, adversarial, random, best-fit,
dependency-aware`.

| Flag | Default | Purpose |
|---|---|---|
| `-n, --namespace <ns>` | scenario's own | Target namespace / scenario name. |
| `-o, --output-dir <dir>` | auto | Where the run writes its output, including `summary.json`. |
| `-s, --strategies <list>` | all 8 | Comma-separated strategies to run. |
| `-i, --iterations <n>` | 1 | Iterations per strategy. |
| `-e, --experiment <file>` | scenario default | Experiment YAML; repeat for a multi-fault matrix. |
| `--settle-time <s>` | 60 | Pre- and post-chaos steady-state sample window (seconds). |
| `--baseline-duration <s>` | 0 | Pre-chaos baseline window override; `0` falls back to `--settle-time`. |
| `--app-ready-timeout <s>` | 240 | Upper bound for the per-iteration app-readiness gate after the clean-baseline restart. Raise it for slow-recovering workloads (e.g. hotelReservation's ~2-4 min Consul/gRPC re-resolution) so the gate does not false-taint every iteration. Returns early once ready, so a larger budget is free when the app recovers quickly. |
| `--batch-id <label>` | current UTC date | Batch/session label written to `summary.json → batchId` and emitted by `export` as the `batch_id` column; lets mixed-run analysis separate run-to-run cluster drift from strategy effects. |
| `--load-profile <steady\|ramp\|spike>` | steady | Locust load profile during chaos. |
| `--seed <n>` | 42 | Base seed for the `random` strategy (iteration *k* uses `seed + k − 1`). |
| `-t, --timeout <s>` | 300 | Timeout per experiment, seconds. |
| `--no-visualize` | off | Skip chart generation. |

### v2 complete-block sessions (`--v2-*`)

Passing `--v2-levels` switches the run from v1 named strategies to the v2
session driver (pre-registration §Session design / WORKPLAN C1–C3): every
target cross-node fraction becomes one *condition* — fraction-solver
placement realized through the replica-level affinity engine, achieved
placement verified from live pods — executed through the same iteration
pipeline (fault injection, all collectors including the conntrack prober,
taint/doctor metadata) as a strategy. The session is a complete block: all
levels are visited once, in a randomized order drawn from `--v2-order-seed`.
Between conditions the driver restores default scheduling and waits for
namespace quiescence (the M1b barrier). Per the pre-registered rejection
rule, a condition (or iteration) whose live fraction misses its target by
more than 0.05 is **tainted, never dropped**; everything lands in
`summary.json → v2Session` (levels, applied order, both seeds, the
(r, mode, workers) cell, and per-level solver/live fractions with
acceptance verdicts).

Mutually exclusive with `-s/--strategies`, `--seeds`, and `--replicas`; a
session runs exactly one fault (pass exactly one `-e` — the v1 multi-fault
matrix does not combine with v2 sessions).

| Flag | Default | Purpose |
|---|---|---|
| `--v2-levels <list>` | — | Comma-separated target fractions, e.g. `0,0.25,0.5,0.75,1.0` (the complete block; activates the v2 driver). |
| `--v2-order-seed <n>` | 42 | Seed for the randomized condition order (recorded as `v2Session.orderSeed` / `orderApplied`). |
| `--v2-solver-seed <n>` | 0 | Seed for the fraction solver's placements. |
| `--v2-replicas <1\|3>` | 1 | Replicas per service (r = 2 deliberately unsupported per DESIGN §2.3). |
| `--v2-mode <packed\|anti-affine>` | packed | Replica packing mode; at r = 1 the modes are physically identical, at r = 3 `anti-affine` lets the scheduler pick 3 distinct nodes (no solver pin, no live fraction). |
| `--v2-workers <list>` | — | Ordered worker node names; solver node index *i* maps to the *i*-th name (required with `--v2-levels`). |
| `--v2-packed-assignment <solver\|round-robin>` | solver | Pinned-cell (r = 1 / r = 3 packed) assignment. `solver` targets the condition's f via the fraction solver (the V2-H1 dose-response sweep). `round-robin` uses the capacity-feasible per-service round-robin packing (V2-H3 replication-rescue; f-independent, matches the M1b-verified packed semantics — each service's replicas on one node, services spread across nodes). |

**A/A pairs.** An A/A pair is simply two runs with identical `--v2-*`
arguments *including* `--v2-solver-seed` (identical placements per level);
`--v2-order-seed` may differ between the two runs — the visit order may
differ, the placements do not. No special A/A mode exists:

```bash
# A/A pair: identical placements, independently randomized visit order
chaosprobe run -n online-boutique -i 3 \
    -e scenarios/online-boutique/pod-delete.yaml \
    --v2-levels 0,0.25,0.5,0.75,1.0 --v2-solver-seed 0 --v2-order-seed 11 \
    --v2-replicas 1 --v2-mode packed --v2-workers worker1,worker2,worker3,worker4
chaosprobe run -n online-boutique -i 3 \
    -e scenarios/online-boutique/pod-delete.yaml \
    --v2-levels 0,0.25,0.5,0.75,1.0 --v2-solver-seed 0 --v2-order-seed 12 \
    --v2-replicas 1 --v2-mode packed --v2-workers worker1,worker2,worker3,worker4
```

See [Run experiments](../how-to/run-experiments.md).

## Analysis commands

All consume a `summary.json`; all are demoable against `examples/`. See
[Analyze results](../how-to/analyze-results.md).

| Command | Purpose |
|---|---|
| `doctor -s <summary> [--strict]` | Data-quality gate (tainted iterations, OOM, missing recovery, inconclusive CIs, schema drift). `--strict` exits 1 on warnings. |
| `summarize -s <summary> [--strategy <name>]` | Per-strategy aggregate roll-up (resilience, recovery split, CV, histogram). |
| `stats -s <summary> [--metric m] [--all-metrics] [--baseline <name>] [--effect-size-min lvl] [--sort p_holm\|p_raw\|delta] [--merge <file>] [--markdown\|--csv\|--json]` | Bootstrap CIs + pairwise Mann-Whitney U (Holm-Bonferroni) + Cliff's delta. `--metric` ∈ `resilience, recovery, d2s, s2r`; `--sort` orders the pairwise table; output as table (default), `--markdown`, `--csv`, or `--json`. |
| `power -s <summary> [--metric m] [--target-delta d] [--alpha a] [--power p] [--json]` | Required sample size per strategy for a target effect. |
| `recommend -s <summary> [--metric m] [--alpha a] [--include-control] [--json]` | Statistically-justified placement recommendation. `--metric` ∈ `resilience, recovery`. The `baseline` control (no real fault) is excluded by default since it isn't a deployable placement; `--include-control` keeps it in the ranking. Multi-fault summaries are ranked per fault (a `byFault` map under `--json`, one `Fault: <name>` section in text); single-fault summaries keep the flat output. |
| `inspect -s <summary> --strategy <name> -i <n> [--json]` | Per-iteration drill-down (verdict, probes, recovery split, snapshots). |
| `diff --a <summary> --b <summary> [--strict]` | Two-summary stability comparison. `--strict` exits 1 on disjoint CIs. |
| `export -s <summary> -o <file> [--format csv\|jsonl]` | Flatten iterations to CSV / JSONL. |
| `report -s <summary> [--diff <summary>] -o <file>` | One-shot markdown appendix: `doctor` + `summarize` + `stats` (+ optional `diff`). |

## placement

```
placement apply <strategy> -n <ns> [--seed <n>]
placement show -n <ns>
placement nodes
placement clear -n <ns>
```

Strategies: `colocate, spread, random, adversarial, best-fit, dependency-aware`
(see [Concepts → placement strategies](../explanation/concepts.md#placement-strategies)).

## graph

Neo4j-backed analysis; all take `--neo4j-uri <uri>`.

```
graph status
graph sessions
graph blast-radius <service>
graph topology --run-id <id>
graph details <run-id>
graph compare --run-ids <id1,id2,...>
```

## dashboard

ChaosCenter UI access.

```
dashboard install
dashboard status
dashboard open
dashboard connect -n <namespace>
dashboard credentials
```

## visualization

```
visualize --neo4j-uri <uri> -o charts/
visualize --neo4j-uri <uri> --session <id> -o charts/
visualize --summary summary.json -o charts/      # legacy (no Neo4j)
```

## ml-export

Aligned, labeled time-series datasets from Neo4j.

```
ml-export --neo4j-uri <uri> -o dataset.csv
ml-export --neo4j-uri <uri> -o dataset.parquet --format parquet
ml-export --neo4j-uri <uri> --strategy colocate -o dataset.csv
```

## probe

Custom Rust `cmdProbe` checks. See [Add a Rust probe](../how-to/add-a-rust-probe.md).

```
probe init <name> --scenario <path> [--single-file]
probe build <scenario> [-r <registry> --push]
probe list <scenario>
```

## cluster

```
# Vagrant (local)
cluster vagrant init [--control-planes N --workers N]
cluster vagrant setup            # one-time libvirt setup (WSL2/Linux)
cluster vagrant up | deploy | kubeconfig | status | halt | destroy
cluster vagrant ssh <vm-name>

# Kubespray (bare-metal / cloud)
cluster create --hosts-file hosts.yaml
cluster kubeconfig --host <ip> --user <user>
cluster destroy --inventory <path>
```

See [Set up a cluster](../how-to/set-up-a-cluster.md).
