# Getting started

This tutorial takes you from a fresh checkout to your first ChaosProbe results.
It has two parts:

1. **Part 1 — no cluster needed.** Run the analysis CLI against a bundled
   example so you can see what ChaosProbe produces in under a minute.
2. **Part 2 — a real experiment.** Point ChaosProbe at a Kubernetes cluster and
   run an actual chaos experiment.

By the end you'll have generated a thesis-style results report and (optionally)
run a real placement-strategy experiment.

## Prerequisites

- Python 3.9+
- [uv](https://docs.astral.sh/uv/)

That's all you need for Part 1. Part 2 additionally needs `kubectl` and a
cluster — see [Set up a cluster](../how-to/set-up-a-cluster.md) if you don't
have one.

## Part 1 — Your first analysis (no cluster)

ChaosProbe ships a worked-example `summary.json` so every analysis command is
demoable offline.

### Step 1 — Install

```bash
cd chaosprobe
uv sync          # creates .venv and installs everything
```

### Step 2 — Look at the example results

```bash
uv run chaosprobe summarize -s examples/example-summary.json
```

You'll see a per-strategy roll-up: resilience scores, recovery times, and a
small histogram. The example compares two placement strategies, `spread` and
`colocate`.

### Step 3 — Ask for a recommendation

```bash
uv run chaosprobe recommend -s examples/example-summary.json
```

ChaosProbe ranks the strategies and tells you which to pick — and whether the
difference is statistically significant:

```
  -> Recommended: spread  [significant]
     'spread' is significantly better than runner-up 'colocate' (p=0.0066, Cliff's delta=1.0 large).
```

### Step 4 — Generate a report

```bash
uv run chaosprobe report -s examples/example-summary.json -o /tmp/report.md
```

Open `/tmp/report.md`: it's a self-contained markdown appendix combining the
data-quality check, the per-strategy summary, and the statistical comparison —
the kind of artifact you'd paste into a thesis appendix.

**You've now seen ChaosProbe's output end-to-end.** Every analysis command works
this way — see [Analyze results](../how-to/analyze-results.md) for the full set.

## Part 2 — Your first real experiment

This needs a cluster. The quickest local option is Vagrant; if you already have
a cluster, skip to Step 2.

### Step 1 — Get a cluster (local Vagrant)

```bash
uv run chaosprobe cluster vagrant init --control-planes 1 --workers 4
uv run chaosprobe cluster vagrant setup     # one-time libvirt setup (WSL2/Linux)
uv run chaosprobe cluster vagrant up
uv run chaosprobe cluster vagrant deploy     # installs Kubernetes (15–30 min)
uv run chaosprobe cluster vagrant kubeconfig
export KUBECONFIG=~/.kube/config-chaosprobe
```

(Other cluster options — Kubespray, Proxmox — are in
[Set up a cluster](../how-to/set-up-a-cluster.md).)

### Step 2 — Initialize the infrastructure

```bash
uv run chaosprobe init
```

This installs everything the experiments need: LitmusChaos, ChaosCenter,
metrics-server, Prometheus, Neo4j, and an in-cluster image registry for probe
images.

### Step 3 — Run the experiment

```bash
uv run chaosprobe run -n online-boutique
```

ChaosProbe deploys the Online Boutique workload, then runs the chaos experiment
once per placement strategy, collecting recovery times, latency, resource
metrics, and probe verdicts into Neo4j and a `summary.json`.

### Step 4 — Analyze it

Point the same analysis commands from Part 1 at the `summary.json` the run
produced:

```bash
uv run chaosprobe report -s <run-output>/summary.json -o report.md
uv run chaosprobe recommend -s <run-output>/summary.json
```

## Where to next

- **Do a specific task** → [How-to guides](../index.md)
- **Understand what just happened** → [Concepts](../explanation/concepts.md)
- **Look up a command** → [CLI reference](../reference/cli.md)
- **Reproduce the thesis numbers** → [Reproduce the thesis results](../how-to/reproducing-thesis-results.md)
