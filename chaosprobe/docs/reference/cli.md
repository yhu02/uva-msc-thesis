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
| `--load-profile <steady\|ramp\|spike>` | steady | Locust load profile during chaos. |
| `--seed <n>` | 42 | Base seed for the `random` strategy (iteration *k* uses `seed + k − 1`). |
| `-t, --timeout <s>` | 300 | Timeout per experiment, seconds. |
| `--no-visualize` | off | Skip chart generation. |

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
