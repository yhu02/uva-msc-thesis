# CLAUDE.md — autonomous cluster setup + experiment runs

Guidance for Claude Code to provision the thesis cluster with Vagrant and run
ChaosProbe experiments end to end. Full docs: [`chaosprobe/docs/index.md`](chaosprobe/docs/index.md).

This flow is **idempotent and re-entrant**: every step is gated by a pre-check,
so on a second invocation (cluster already up, infra already installed) it skips
straight to running. **Run the pre-check before each command and skip the
command when its check already passes** — never re-provision or re-install
blindly. A few steps require the user; they are flagged ⚠️.

---

## ⛔ Safety gate — verify the target cluster before ANY chaos (non-negotiable)

`chaosprobe` acts on whatever `KUBECONFIG` points at, and it does **not** check
which cluster that is. Another kubeconfig on this machine targets a
**corporate / production AKS cluster** (`aie-*` namespaces). Running chaos there
would be catastrophic.

**Before** `init`, `run`, `provision`, `placement`, `delete`, `cleanup`, or any
chaos command, confirm **both**:

```bash
echo "$KUBECONFIG"                 # MUST be ~/.kube/config-chaosprobe
kubectl config current-context     # MUST be the Vagrant thesis cluster (never aks / aie-*)
```

If either is wrong, missing, unexpected, or you are unsure → **STOP and ask the
user.** Never run a cluster-mutating or chaos command against a cluster you did
not just provision and verify in this session.

---

## What you own vs. what the user owns

You drive the `chaosprobe` CLI. The **user** owns privileged host/node
operations — do not attempt them; stop and hand over (suggest they run it via
`! <command>` in the prompt so the output lands in the session):

- ⚠️ `cluster vagrant setup` / `deploy` when they need **sudo or libvirt** (VM
  provider setup, ansible "become" password).
- ⚠️ The one-time **in-cluster registry trust** step on each node's containerd
  (for image *pull*; `run` pushes via a port-forward tunnel so the build host
  needs no trust — see [`chaosprobe/manifests/README.md`](chaosprobe/manifests/README.md)).
- ⚠️ Any node restart or containerd reload.

---

## Decide what's actually needed (pre-checks)

Work top-down. Each row's **check** tells you whether to run the command or skip
it. `cd chaosprobe` first; all checks assume `KUBECONFIG=~/.kube/config-chaosprobe`.

### 0. Toolchain
- **Check:** `uv run chaosprobe --version` succeeds.
- **If it fails:** `uv sync` (from `chaosprobe/`). Otherwise skip.

### 1. Is there already a working cluster? (the master gate)
```bash
kubectl --kubeconfig ~/.kube/config-chaosprobe get nodes
```
- **Nodes listed and `Ready`** → a cluster already exists. **Skip the entire
  Vagrant section (steps 2–5)**, `export KUBECONFIG=~/.kube/config-chaosprobe`,
  and jump to the safety gate → step 6.
- **Command fails / no nodes** → you need to provision: do steps 2–5, each gated
  by its own check.

### 2–5. Provision with Vagrant (only if step 1 found no cluster)

`uv run chaosprobe cluster vagrant status` is the authoritative probe here: it
resolves the cluster dir itself (`~/.chaosprobe/vagrant/chaosprobe/` — **not** the
current directory, so don't `test -f Vagrantfile` from `chaosprobe/`) and reports
both whether the Vagrantfile exists and whether the VMs are running. Run it once,
then act on what it says.

| Step | Check (skip the command if true) | Command |
|---|---|---|
| 2. Vagrantfile | `cluster vagrant status` shows the cluster exists (Vagrantfile present) | `uv run chaosprobe cluster vagrant init --control-planes 1 --workers 4` |
| 3. ⚠️ libvirt | libvirt already set up | `uv run chaosprobe cluster vagrant setup` — diagnostic-first, but it also *installs* the provider and may need **sudo**, so treat it as the user-owned gate: run it, and if it reports `MISSING`/`NOT RUNNING` or asks for sudo → hand to user |
| 4. VMs up | `cluster vagrant status` shows the VMs `running` | `uv run chaosprobe cluster vagrant up` |
| 5. ⚠️ k8s installed | — | `uv run chaosprobe cluster vagrant deploy` — installs Kubernetes; this is what makes step 1's `kubectl get nodes` succeed. **Run it once during fresh provisioning. You only reach this step because step 1 found no cluster, and that master gate is what stops it ever re-running.** 15–30 min, background + poll; may need sudo → user |

Then fetch the kubeconfig **only if missing**:
- **Check:** `test -f ~/.kube/config-chaosprobe` and `kubectl --kubeconfig ~/.kube/config-chaosprobe get nodes` works.
- **Else:** `uv run chaosprobe cluster vagrant kubeconfig`, then `export KUBECONFIG=~/.kube/config-chaosprobe`.

### 6. Safety gate
Run the ⛔ safety gate above. Then optionally `uv run chaosprobe status`
(prerequisites + connectivity).

### 7. In-cluster registry — the ONLY thing `init` adds that `run` won't
`run` self-heals Helm, local-path-provisioner, LitmusChaos, RBAC, experiment
CRDs, metrics-server, Prometheus, Neo4j, ChaosCenter, and `crane` (the
daemon-less probe-image pusher, auto-installed like Helm) on its own — so **you
do not need `init` for those.** The single exception is the in-cluster image
registry (for the 5 Rust `cmdProbes`), which only `init` installs.
Probe images use the in-cluster registry **exclusively** — there is no external
/ GHCR option. `run` resolves its address and pushes there, and **fails** if it
isn't installed (no off-cluster fallback).
- **Check:** `kubectl get ns registry` (and `kubectl get pods -n registry -l app=registry`).
- **Registry present** → skip `init` entirely; go to step 8.
- **Registry absent** → `uv run chaosprobe init` to install it (⚠️ the one-time
  insecure-registry trust on each node's containerd is user-owned; the build
  host needs none — `run` pushes via a port-forward tunnel — see
  [`chaosprobe/manifests/README.md`](chaosprobe/manifests/README.md)).
  `run` self-heals all other infra but never the registry.

> Tip: you can skip `init` and let `run` bootstrap the rest — the trade-off is
> the *first* `run` takes longer (it installs the infra inline) and has no
> in-cluster registry. Use `init` when you want that install done upfront and/or
> need the registry.

### 8. Run experiments (always — this is the goal)
Long (per iteration ≈ 60 s settle + 120 s chaos + 60 s post, × strategy ×
iterations — hours for the full matrix). Background + poll; don't block.

`run` defaults to all 8 strategies
(`baseline,default,colocate,spread,adversarial,random,best-fit,dependency-aware`);
`-s` selects a subset, `-i` sets the iteration count, and `-e` (repeatable)
selects the fault experiment file(s). The output dir (`results/<timestamp>/`,
holding `summary.json`) is printed at startup; override with `-o`. Full flag
list: [`chaosprobe/docs/reference/cli.md`](chaosprobe/docs/reference/cli.md#run).
```bash
# Full multi-fault matrix (recommended for results):
uv run chaosprobe run -n online-boutique \
    -e scenarios/online-boutique/placement-experiment.yaml \
    -e scenarios/online-boutique/placement-experiment-cpuhog.yaml \
    -i 5
# A subset of strategies (e.g. 4 strategies × 2 iterations):
uv run chaosprobe run -n online-boutique \
    -s default,colocate,spread,dependency-aware -i 2
# Fast smoke check first:
uv run chaosprobe run -n online-boutique -i 1
```

---

## Analyze + report

- `uv run chaosprobe doctor -s <run-output>/summary.json --strict` (gate data quality).
- `uv run chaosprobe recommend -s <run-output>/summary.json` and
  `uv run chaosprobe report -s <run-output>/summary.json -o report.md` — surface
  the recommendation and report path to the user.

Full analysis set: [`chaosprobe/docs/how-to/analyze-results.md`](chaosprobe/docs/how-to/analyze-results.md).

---

## Teardown

- `uv run chaosprobe cluster vagrant halt` — stops the VMs, **preserves** state
  (restart with `up`; no re-`deploy` needed). Default when done.
- ⚠️ `uv run chaosprobe cluster vagrant destroy` — **destructive**. Only on
  explicit user request. (After a destroy, step 1's check fails and the full
  provision path runs again.)

---

## Conventions

- **Pre-check, then act** — skip any command whose check already passes; never
  re-provision/re-install blindly. The expensive ones to guard are `vagrant
  deploy` (gate on `kubectl get nodes`) and `init` (gate on `kubectl get ns registry`).
- Long ops (`deploy`, `run`): launch in the background and poll; don't block.
- Stop at the ⚠️ gates rather than guessing sudo passwords or node config.
- Do **not** commit run outputs, `summary.json`, charts, or reports unless asked.
- Reference: [`chaosprobe/docs/index.md`](chaosprobe/docs/index.md) (Diátaxis map),
  [`chaosprobe/TECHNICAL.md`](chaosprobe/TECHNICAL.md) (deep reference).
