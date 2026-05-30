# Configuration reference

## Environment variables

ChaosProbe loads a `.env` file automatically (via python-dotenv); create one in
the project root. **Shell-exported variables take precedence** over `.env`.

| Variable | Default | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI. |
| `NEO4J_USER` | `neo4j` | Neo4j username. |
| `NEO4J_PASSWORD` | `chaosprobe` | Neo4j password. |
| `KUBECONFIG` | `~/.kube/config` | Path to the kubeconfig ChaosProbe acts against. |

Rust probe images use the **in-cluster registry exclusively** (installed by
`chaosprobe init`); there is no external-registry configuration. See
[Add a Rust probe](../how-to/add-a-rust-probe.md).

## Rust probe runtime variables

These are read **inside probe containers at runtime** (set via ChaosEngine env
or pod env), not by the CLI. See [Add a Rust probe](../how-to/add-a-rust-probe.md).

| Variable | Default | Probe |
|---|---|---|
| `PROBE_REDIS_ADDR` | `redis-cart.online-boutique.svc.cluster.local:6379` | check-redis |
| `PROBE_URL` | `http://frontend.online-boutique.svc.cluster.local/` | check-http-latency |
| `PROBE_LATENCY_MS_MAX` | `4000` | check-http-latency |
| `PROBE_HOST` | `frontend.online-boutique.svc.cluster.local:80` | check-dns-latency, check-cart-flow |
| `PROBE_DNS_MS_MAX` | `250` | check-dns-latency |
| `PROBE_TARGET` | `frontend.online-boutique.svc.cluster.local:80` | check-tcp-connect |
| `PROBE_CONNECT_MS_MAX` | `500` | check-tcp-connect |
| `PROBE_ROUTE_MS_MAX` | `1500` | check-cart-flow |
| `PROBE_TIMEOUT_MS` | `5000` (check-http-latency); `2000` (check-redis, check-tcp-connect, check-cart-flow) | per-probe |

## Prerequisites

- `kubectl`
- Python 3.9+
- [uv](https://docs.astral.sh/uv/)
- For building Rust `cmdProbe`s: `docker` (build) and `rustc`/`cargo` (compile).

Helm, [`crane`](https://github.com/google/go-containerregistry) (the daemon-less
pusher for probe images), LitmusChaos, ChaosCenter, metrics-server, Prometheus,
Neo4j, and the in-cluster registry are installed automatically by `chaosprobe
init` — and `run` self-installs them too, so a fresh checkout needs only the
tools listed above.

**Cluster provisioning** additionally needs:
- *Vagrant (local):* [Vagrant](https://www.vagrantup.com/downloads) with the
  **libvirt/KVM** provider — the only supported provider (ChaosProbe forces it
  via `VAGRANT_DEFAULT_PROVIDER=libvirt`), `git`, Python 3 with `venv`.
- *Kubespray (production):* `git`, `ssh`, Python 3 with `venv`
  (`apt install python3-venv`).

Kubespray is the only supported Kubernetes installer for both paths.
