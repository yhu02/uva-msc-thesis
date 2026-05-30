# In-cluster registry for ChaosProbe probe images

ChaosProbe's Rust `cmdProbe` images have to live in a registry the cluster can
`docker pull` from. Instead of depending on an external registry (GHCR auth +
package visibility), `chaosprobe init` installs a small private `registry:2` on
the control-plane node and `chaosprobe run` pushes/pulls probe images there
automatically.

`init` deploys the registry and prints its address; `run` discovers it and uses
it with no credentials (no `docker login`). The **one thing neither can do** is
mark the registry as *trusted* on the build host and the nodes — that's
node-level config outside the Kubernetes API, so you do it once (steps 2–3).

> ⚠️ Always target the thesis kubeconfig. The machine's *default* `KUBECONFIG`
> may point at an unrelated cluster. In every shell:
> ```bash
> export KUBECONFIG=~/.kube/config-chaosprobe
> kubectl config current-context   # confirm it's the thesis cluster
> ```

## 1. Install the registry

```bash
export KUBECONFIG=~/.kube/config-chaosprobe
chaosprobe init                 # installs + wires the registry (use --skip-registry to opt out)
```
`init` prints the registry address, e.g. `192.168.56.11:30500` (control-plane
node IP + NodePort). To install/inspect it without full init:
`kubectl apply -f manifests/registry.yaml`. Sanity check (should print `{}`):
```bash
curl http://<registry-address>/v2/
```

## 2. Trust it on the build host (Docker) — so `docker push` over HTTP works

Without this, push fails with *"http: server gave HTTP response to HTTPS client."*

- **Native dockerd:** add to `/etc/docker/daemon.json`, then `sudo systemctl restart docker`:
  ```json
  { "insecure-registries": ["<registry-address>"] }
  ```
- **Docker Desktop (WSL):** Settings → Docker Engine → add the same
  `insecure-registries` array → Apply & Restart.

## 3. Trust it on every node (containerd) — so the kubelet can pull over HTTP

containerd verifies TLS by default, so each node needs the registry marked
insecure. containerd must have `config_path = "/etc/containerd/certs.d"`
(Kubespray sets this). Then on each node (`chaosprobe cluster vagrant ssh <node>`):
```bash
sudo mkdir -p /etc/containerd/certs.d/<registry-address>
sudo tee /etc/containerd/certs.d/<registry-address>/hosts.toml >/dev/null <<EOF
server = "http://<registry-address>"
[host."http://<registry-address>"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
EOF
sudo systemctl restart containerd
```
Reproducible alternative (Kubespray) — put this in inventory `group_vars` and
re-run the containerd role so it survives rebuilds:
```yaml
containerd_insecure_registries:
  "<registry-address>": "http://<registry-address>"
```

## 4. Run

```bash
export KUBECONFIG=~/.kube/config-chaosprobe
uv run chaosprobe run -i 1 --strategies baseline,default,colocate,spread
```
`run` resolves the in-cluster registry's address automatically and pushes there
(it's an unauthenticated insecure HTTP registry, so no `docker login`). If the
registry isn't installed, `run` fails with a message telling you to run
`chaosprobe init`. Verify the push landed:
```bash
curl http://<registry-address>/v2/_catalog
```

## Troubleshooting

- **`docker push` → "HTTP response to HTTPS client"** → step 2 not done (or daemon not restarted).
- **`docker push` → `dial tcp <registry-address>: ... connection attempt failed` / "Docker Desktop has no HTTPS proxy"** → the docker *daemon* can't route to the registry's node IP, even when `curl http://<registry-address>/v2/` works from your shell. Common on **Docker Desktop + WSL2**: the daemon runs in a separate VM that has no route to the cluster's host-only network (e.g. `192.168.56.0/24`). Fix: give the Docker Desktop VM a route to that network, or build/push the probe images from a host that is on it (a WSL distro running a native `dockerd`, or a cluster node).
- **Pods `ImagePullBackOff` with the HTTP/HTTPS error** → step 3 not done on the node that scheduled the probe pod.
- **`run` fails with "In-cluster registry not found"** → the registry isn't installed/ready; run `chaosprobe init` and check `kubectl -n registry get pods`.
