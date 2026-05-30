# How to set up a cluster

ChaosProbe needs a Kubernetes cluster to run experiments against. Pick the path
that matches your environment.

| Path | Use when |
|---|---|
| [Vagrant](#local-development-vagrant) | Local development on one machine (VMs) |
| [Kubespray](#bare-metal-or-cloud-vms-kubespray) | Bare-metal or cloud VMs you can SSH to |
| [Proxmox](../../proxmox-setup.md) | A Proxmox host (Kubespray under the hood) |

After the cluster is up, `export KUBECONFIG=~/.kube/config-chaosprobe` and
continue with `chaosprobe init` (see [Run experiments](run-experiments.md)).

> ⚠️ **Target the right cluster.** `chaosprobe` acts on whatever `KUBECONFIG`
> points at. Always export the thesis kubeconfig before running — never run
> against an unrelated/production cluster.

## Local development (Vagrant)

Requires [Vagrant](https://www.vagrantup.com/downloads), a libvirt/KVM provider,
`git`, and Python 3 with the `venv` module.

```bash
# 1. Generate a Vagrantfile (1 control plane + 4 workers)
uv run chaosprobe cluster vagrant init --control-planes 1 --workers 4

# 2. One-time libvirt provider setup (WSL2 / Linux)
uv run chaosprobe cluster vagrant setup

# 3. Start the VMs
uv run chaosprobe cluster vagrant up

# 4. Deploy Kubernetes (takes 15–30 minutes)
uv run chaosprobe cluster vagrant deploy

# 5. Fetch the kubeconfig
uv run chaosprobe cluster vagrant kubeconfig
export KUBECONFIG=~/.kube/config-chaosprobe
```

Lifecycle:

```bash
uv run chaosprobe cluster vagrant status        # show VM state
uv run chaosprobe cluster vagrant ssh <vm-name> # shell into a VM
uv run chaosprobe cluster vagrant halt          # stop VMs (preserves disk)
uv run chaosprobe cluster vagrant destroy       # delete VMs permanently
```

## Bare-metal or cloud VMs (Kubespray)

Requires `git`, `ssh`, and Python 3 with `venv` (`apt install python3-venv`) on
the control machine, plus SSH access to the target hosts.

```bash
# 1. Describe your hosts
cat > hosts.yaml << 'EOF'
hosts:
  - name: master1
    ip: 192.168.1.10
    ansible_user: ubuntu
    roles: [control_plane, worker]
  - name: worker1
    ip: 192.168.1.11
    ansible_user: ubuntu
    roles: [worker]
  - name: worker2
    ip: 192.168.1.12
    ansible_user: ubuntu
    roles: [worker]
EOF

# 2. Deploy the cluster (15–30 minutes)
uv run chaosprobe cluster create --hosts-file hosts.yaml

# 3. Fetch the kubeconfig
uv run chaosprobe cluster kubeconfig --host 192.168.1.10 --user ubuntu
export KUBECONFIG=~/.kube/config-chaosprobe

# Tear down later
uv run chaosprobe cluster destroy --inventory <path>
```

## Proxmox

For provisioning the VMs on a Proxmox host (then deploying with Kubespray as
above), see the dedicated runbook: [`../../proxmox-setup.md`](../../proxmox-setup.md).

## Next

- [Run experiments](run-experiments.md) — `init` then `run`.
- Full cluster-command flags: [CLI reference → cluster](../reference/cli.md#cluster).
