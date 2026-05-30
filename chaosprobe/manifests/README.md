# In-cluster registry for ChaosProbe probe images

ChaosProbe's Rust `cmdProbe` images have to live in a registry the cluster can
`docker pull` from. Instead of depending on an external registry (GHCR auth +
package visibility), `chaosprobe init` installs a small private `registry:2` on
the control-plane node and `chaosprobe run` pushes/pulls probe images there
automatically.

`run` builds images with docker, then **pushes them with
[`crane`](https://github.com/google/go-containerregistry) (daemon-less)**
through a `kubectl port-forward` tunnel to the registry Service. crane runs in
the `chaosprobe` process — not the docker daemon, whose network can be isolated
from the cluster (e.g. Docker Desktop) — so the build host needs only docker (to
build) and `kubectl`. crane itself is **auto-installed** (like Helm) by `init`
and `run` if missing, so it isn't a manual prerequisite. No route to the
registry's NodePort, no docker `insecure-registries` config, no `docker login`
(crane pushes over plain HTTP with `--insecure`). This works the same on Docker
Desktop, native Linux, or a remote build host.

The **one manual step** is trusting the registry on each node's containerd, so
the kubelet can *pull* the images over plain HTTP (step 2) — that's node-level
config outside the Kubernetes API.

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

## 2. Trust it on every node (containerd) — so the kubelet can pull over HTTP

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

## 3. Run

```bash
export KUBECONFIG=~/.kube/config-chaosprobe
uv run chaosprobe run -i 1 --strategies baseline,default,colocate,spread
```
`run` resolves the registry's address automatically, opens a `kubectl
port-forward` to the registry Service, and `crane push`es the probe images
through it (the image repository is preserved, so cmdProbe pods still pull from
the node-reachable `<registry-address>`). If the registry isn't installed, `run`
fails with a message telling you to run `chaosprobe init`. Verify the push
landed:
```bash
curl http://<registry-address>/v2/_catalog
```

## Troubleshooting

- **`run`/`init` fails with "Failed to install crane" or "no prebuilt binary"** → the auto-install couldn't fetch the release binary (offline, or an unsupported OS/arch). Install crane manually onto `PATH`: download a release binary from [go-containerregistry](https://github.com/google/go-containerregistry/releases) or `go install github.com/google/go-containerregistry/cmd/crane@latest`.
- **`run` fails with "In-cluster registry not found"** → the registry isn't installed/ready; run `chaosprobe init` and check `kubectl -n registry get pods`.
- **`run` fails with "registry port-forward did not become ready / failed"** → kubectl can't reach the cluster or the registry Service is missing. Check `kubectl config current-context` (thesis cluster) and `kubectl -n registry get svc registry`.
- **Pods `ImagePullBackOff` with an HTTP/HTTPS error** → step 2 not done on the node that scheduled the probe pod (or its containerd wasn't restarted).
