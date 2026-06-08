# ChaosProbe documentation

This documentation follows the [Diátaxis](https://diataxis.fr/) framework: it is
split by *what you need right now* — to learn, to get a task done, to look
something up, or to understand why. Start wherever your need fits.

> **Scope.** These are the docs for the **ChaosProbe tool**. The thesis itself
> (its argument, results, and bibliography) lives outside this tree — see
> [`../../references.md`](../../references.md) and the dissertation. The deep,
> citable technical write-up is [`../TECHNICAL.md`](../TECHNICAL.md), which
> doubles as the project's consolidated **reference + explanation** appendix.

## 🎓 Tutorials — *learning-oriented*

Start here if you're new. A guided, end-to-end lesson.

- [Getting started](tutorials/getting-started.md) — run your first analysis with
  no cluster, then your first real chaos experiment.

## 🔧 How-to guides — *task-oriented*

Recipes for a specific goal, assuming you already know the basics.

- [Set up a cluster](how-to/set-up-a-cluster.md) — Vagrant (local) or Kubespray
  (bare-metal / cloud); Proxmox specifics in [`../proxmox-setup.md`](../proxmox-setup.md).
- [Run experiments](how-to/run-experiments.md) — the placement-strategy matrix,
  iterations, load profiles, multi-fault runs.
- [Analyze results](how-to/analyze-results.md) — `doctor`, `summarize`, `stats`,
  `power`, `recommend`, `inspect`, `diff`, `export`, `report`.
- [Write a scenario](how-to/write-a-scenario.md) — the directory layout and
  ChaosEngine YAML.
- [Add a Rust probe](how-to/add-a-rust-probe.md) — scaffold, build, and inject
  custom `cmdProbe` checks; registry trust in [`../manifests/README.md`](../manifests/README.md).
- [Reproduce the thesis results](how-to/reproducing-thesis-results.md) — exact
  spec, fault matrix, and invocations.

## 📖 Reference — *information-oriented*

Dry, complete technical descriptions. Look things up here.

- [CLI reference](reference/cli.md) — every command and flag.
- [Configuration](reference/configuration.md) — environment variables and probe
  runtime variables.
- [`../TECHNICAL.md`](../TECHNICAL.md) — module reference, output schema (v2.0.0),
  Neo4j graph schema, dependencies.

## 💡 Explanation — *understanding-oriented*

Background and rationale — the *why*.

- [Concepts](explanation/concepts.md) — the AI feedback loop, placement
  strategies, resilience scoring, and the system architecture.
- [Hypotheses & findings](explanation/hypotheses.md) — the falsifiable research
  hypotheses (H1–H4), their status, and the committed script that reproduces
  each number.
- [Scope of claims](explanation/scope-of-claims.md) — what the evidence does and
  does not support, and what generalizes vs. what does not. Read before quoting
  any result.
- [`../TECHNICAL.md`](../TECHNICAL.md) — data flow, experiment design, resilience
  scoring methodology, and the statistics behind `stats` / `power` / `recommend`.
