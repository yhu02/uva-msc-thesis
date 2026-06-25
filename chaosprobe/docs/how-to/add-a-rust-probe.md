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

Probe images live in the **in-cluster registry** that `chaosprobe init` installs
on the control-plane node — ChaosProbe uses this registry exclusively (no
external registry). `chaosprobe run` resolves its address automatically and
pushes there; if it isn't installed, `run` fails with a clear message telling
you to run `chaosprobe init`.

`run` builds with docker, then pushes with [`crane`](https://github.com/google/go-containerregistry)
(daemon-less) through a `kubectl port-forward` tunnel — so **the build host needs
no docker registry config**, just `docker` and `kubectl` (the daemon, which can
be network-isolated on Docker Desktop, never touches the registry; no `docker
login`). crane itself is auto-installed (like Helm) by `init`/`run`, so it isn't
a manual prerequisite. The one manual step is a one-time "insecure registry"
trust on **each node's containerd**, so the kubelet can *pull* the images over
plain HTTP.
The full runbook is in [`../../manifests/README.md`](../../manifests/README.md).

The standalone `probe build` command builds local images by default; to push to
the cluster manually, pass the in-cluster registry address:

```bash
uv run chaosprobe probe build scenarios/online-boutique -r <node-ip>:30500 --push
```

## Next

- Probe runtime variables (timeouts, targets):
  [Configuration → probe runtime variables](../reference/configuration.md#rust-probe-runtime-variables).
- All `probe` flags: [CLI reference → probe](../reference/cli.md#probe).
