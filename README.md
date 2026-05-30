# uva-msc-thesis

MSc thesis project — University of Amsterdam.

## ChaosProbe

A framework for running LitmusChaos experiments against Kubernetes deployments, collecting structured experiment data into Neo4j for AI-driven anomaly classification and remediation. See [chaosprobe/README.md](chaosprobe/README.md) for the landing page, or the full [documentation map](chaosprobe/docs/index.md) (organized by [Diátaxis](https://diataxis.fr/): tutorial / how-to / reference / explanation).

### Quick Start

```bash
cd chaosprobe
uv sync
uv run chaosprobe init
uv run chaosprobe run -n online-boutique
```

### Try the analysis CLI without a cluster

Every analysis command (`doctor`, `summarize`, `stats`, `power`, `recommend`,
`inspect`, `diff`, `report`, `export`) consumes a `summary.json` and ships with a
worked-example fixture in [chaosprobe/examples/](chaosprobe/examples/) — no
cluster required:

```bash
cd chaosprobe
uv sync
uv run chaosprobe report -s examples/example-summary.json -o /tmp/report.md
```

See [chaosprobe/examples/README.md](chaosprobe/examples/README.md) for the
full list of demoable commands.

## Repository Structure

- `chaosprobe/` — ChaosProbe CLI tool (Python package)
