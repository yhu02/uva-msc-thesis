# How to add a Rust probe

Custom Rust probes are compiled to static Linux binaries, packaged into minimal
(`scratch`) container images, and injected into ChaosEngine `cmdProbe` specs at
run time. Use them when LitmusChaos's built-in probes can't express the check
you need (e.g. a multi-step cart flow, a Redis round-trip, a DNS latency bound).

## Workflow

```bash
# 1. Scaffold a probe inside a scenario
uv run chaosprobe probe init check-redis --scenario scenarios/online-boutique
#    -> creates scenarios/online-boutique/probes/check-redis/
```

2. Edit `probes/check-redis/src/main.rs` — implement the check.
3. Add a `cmdProbe` entry to the experiment YAML with `source.image: auto`.
4. Build the images:

```bash
uv run chaosprobe probe build scenarios/online-boutique
```

5. Run — `run` auto-builds probes, patches `source.image`, and executes:

```bash
uv run chaosprobe run -n online-boutique
```

A probe prints a result string to stdout; the ChaosEngine comparator matches
against that output. **Exit 0** means the check ran (the verdict comes from the
comparator); **non-zero** means the probe itself failed.

## Scaffolding options

```bash
uv run chaosprobe probe init <name> --scenario <path>               # full Cargo project
uv run chaosprobe probe init <name> --single-file --scenario <path> # no Cargo.toml
uv run chaosprobe probe list <scenario>                             # list, don't build
```

## Where probe images go (the registry)

Probe images must live in a registry the cluster can `docker pull` from.
`chaosprobe run` resolves the push/pull registry in this order:

1. `CHAOSPROBE_REGISTRY` (env override),
2. the **in-cluster registry** that `chaosprobe init` installs on the
   control-plane node (default; no `docker login` needed),
3. the `ghcr.io` fallback.

The in-cluster registry serves plain HTTP, so each node's containerd and your
build-host docker need a one-time "insecure registry" trust step — the full
runbook is in [`../../manifests/README.md`](../../manifests/README.md). Opt out of the
in-cluster registry with `chaosprobe init --skip-registry`.

To build and push to an external registry instead:

```bash
uv run chaosprobe probe build scenarios/online-boutique -r ghcr.io/<user> --push
```

Authenticated registries read `CHAOSPROBE_REGISTRY_USER` /
`CHAOSPROBE_REGISTRY_PASSWORD` — see [Configuration](../reference/configuration.md).

## Next

- Probe runtime variables (timeouts, targets):
  [Configuration → probe runtime variables](../reference/configuration.md#rust-probe-runtime-variables).
- All `probe` flags: [CLI reference → probe](../reference/cli.md#probe).
