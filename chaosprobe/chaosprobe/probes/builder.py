"""Rust cmdProbe builder for LitmusChaos experiments.

Discovers Rust probe sources in scenario directories, compiles them
to static Linux binaries, and builds minimal container images that
can be referenced by cmdProbe ``source.image`` fields.

Supported layouts:
  probes/<name>/Cargo.toml + src/main.rs   (full Cargo project)
  probes/<name>.rs                          (single-file probe)
"""

import hashlib
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from chaosprobe.probes.templates import (
    generate_dockerfile,
)

# crane (go-containerregistry) does the daemon-less push to the in-cluster
# registry. Auto-installed (like helm) if missing — see ensure_crane.
CRANE_VERSION = "v0.20.2"

# SHA-256 of each release tarball for CRANE_VERSION (from the release's
# checksums.txt). The download is verified against these before extraction so a
# tampered or redirected release asset cannot run unverified code on the build
# host. Update this map together with CRANE_VERSION when bumping crane.
CRANE_SHA256 = {
    "go-containerregistry_Linux_x86_64.tar.gz": (
        "c14340087103ba9dadf61d45acd20675490fd0ccbd56ac7901fc1b502137f44b"
    ),
    "go-containerregistry_Linux_arm64.tar.gz": (
        "aff0db48825124c9331ea310057214bd4e92c01aa2e414d539e9659841d9422a"
    ),
    "go-containerregistry_Darwin_x86_64.tar.gz": (
        "ae2677fc68b05ee3a63fe7b1d599aa4a554610b9f9da499a0c39669f446d29ed"
    ),
    "go-containerregistry_Darwin_arm64.tar.gz": (
        "b47a8291d1069656bcfb8346dc9494f03e734d7a4058961fa53f0dfc9cb41abb"
    ),
}

# Default image prefix for local (build-only) images. Probe images destined
# for the cluster are pushed to the in-cluster registry, whose address the
# caller resolves and passes in; ChaosProbe does not use any external registry.
LOCAL_IMAGE_PREFIX = "chaosprobe"

# The in-cluster registry Service (installed by `chaosprobe init`). Pushes go
# through a `kubectl port-forward` to this Service rather than dialing the
# registry's NodePort directly, so the build host only needs kubectl access —
# not a network route to the cluster's node network (which breaks on Docker
# Desktop, remote build hosts, etc.).
REGISTRY_SERVICE = "registry"
REGISTRY_NAMESPACE = "registry"
REGISTRY_TARGET_PORT = 5000

# Rust musl target for static Linux binaries
MUSL_TARGET = "x86_64-unknown-linux-musl"


def _free_local_port() -> int:
    """Pick an unused localhost TCP port for the push tunnel."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _port_accepts(port: int) -> bool:
    """True if 127.0.0.1:port accepts a TCP connection."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


class ProbeBuilderError(Exception):
    """Raised when probe compilation or image build fails."""


def _check_crane() -> bool:
    """True if the `crane` CLI is available."""
    try:
        subprocess.run(["crane", "version"], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _verify_crane_tarball(tarball: Path, filename: str) -> None:
    """Verify a downloaded crane tarball against its pinned SHA-256.

    Fails closed: an unknown filename (no pinned hash) or a checksum mismatch
    raises, so a tampered or redirected release asset is never extracted or
    executed on the build host.
    """
    expected = CRANE_SHA256.get(filename)
    if expected is None:
        raise ProbeBuilderError(
            f"No pinned SHA-256 for {filename} (crane {CRANE_VERSION}); "
            "refusing to install an unverified binary."
        )
    actual = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if actual != expected:
        raise ProbeBuilderError(
            f"crane tarball checksum mismatch for {filename}: "
            f"expected {expected}, got {actual}. Refusing to install a tampered binary."
        )


def ensure_crane() -> None:
    """Ensure `crane` is installed, downloading the release binary if missing.

    crane (go-containerregistry) performs the daemon-less push to the in-cluster
    registry. Mirrors how helm is auto-installed: fetch the pinned release
    tarball into ``~/.local/bin``. Raises if it can't be made available.
    """
    if _check_crane():
        return

    system = platform.system().lower()
    machine = platform.machine().lower()
    os_name = {"linux": "Linux", "darwin": "Darwin"}.get(system)
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(
        machine
    )
    if not os_name or not arch:
        raise ProbeBuilderError(
            f"crane not found and no prebuilt binary for {system}/{machine}. "
            "Install crane manually: https://github.com/google/go-containerregistry"
        )

    filename = f"go-containerregistry_{os_name}_{arch}.tar.gz"
    url = (
        "https://github.com/google/go-containerregistry/releases/download/"
        f"{CRANE_VERSION}/{filename}"
    )
    print(f"  crane not found, installing {CRANE_VERSION} to ~/.local/bin ...")
    install_dir = Path.home() / ".local" / "bin"
    install_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tarball = Path(tmp) / filename
        try:
            subprocess.run(
                ["curl", "-fsSL", "-o", str(tarball), url], check=True, capture_output=True
            )
            _verify_crane_tarball(tarball, filename)
            subprocess.run(
                ["tar", "-xzf", str(tarball), "-C", str(install_dir), "crane"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or b"").decode().strip()
            raise ProbeBuilderError(f"Failed to install crane: {stderr or e}") from e
    (install_dir / "crane").chmod(0o755)
    # Make it visible to this process (mirrors install_helm).
    if str(install_dir) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{install_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    if not _check_crane():
        raise ProbeBuilderError(
            f"crane installed to {install_dir} but `crane version` still fails; "
            f"ensure {install_dir} is on PATH."
        )


class RustProbeBuilder:
    """Compile Rust probes and package them as container images.

    Args:
        registry: Image prefix / registry. Defaults to the local
            ``chaosprobe`` prefix for build-only use. To push to the cluster,
            pass the in-cluster registry address (``<node-ip>:<nodePort>``),
            which the caller resolves via ``resolve_probe_registry``.
        push: If True, push the built image to the in-cluster registry. The
            push goes through a ``kubectl port-forward`` tunnel to ``localhost``
            (so the build host needs only kubectl access, not a route to the
            registry's NodePort), and ``127.0.0.0/8`` is insecure-by-default in
            docker, so no ``docker login`` or build-host registry trust is
            needed. The image is *tagged* with *registry* (the node-reachable
            address cmdProbe pods pull from); only the bytes travel via the
            tunnel.
    """

    def __init__(
        self,
        registry: str = LOCAL_IMAGE_PREFIX,
        push: bool = False,
    ):
        self.registry = registry.rstrip("/")
        self.push = push
        self._push_host: Optional[str] = None  # 127.0.0.1:<port> while tunnel is open
        self._tunnel: Optional[subprocess.Popen] = None

    # ── Discovery ─────────────────────────────────────────

    @staticmethod
    def discover_probes(scenario_path: str) -> List[Dict[str, Any]]:
        """Find all Rust probe sources in a scenario directory.

        Looks for a ``probes/`` subdirectory containing either:
        - ``<name>.rs`` single-file probes
        - ``<name>/Cargo.toml`` full Cargo projects

        Args:
            scenario_path: Absolute path to the scenario directory.

        Returns:
            List of probe descriptors::

                [
                    {
                        "name": "check-db",
                        "path": "/abs/path/to/probes/check-db",
                        "kind": "cargo",      # or "single_file"
                    },
                    ...
                ]
        """
        probes_dir = Path(scenario_path) / "probes"
        if not probes_dir.is_dir():
            return []

        found: List[Dict[str, Any]] = []

        # Single .rs files
        for rs_file in sorted(probes_dir.glob("*.rs")):
            found.append(
                {
                    "name": rs_file.stem,
                    "path": str(rs_file),
                    "kind": "single_file",
                }
            )

        # Cargo project directories
        for child in sorted(probes_dir.iterdir()):
            if child.is_dir() and (child / "Cargo.toml").exists():
                found.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "kind": "cargo",
                    }
                )

        return found

    # ── Compilation ───────────────────────────────────────

    def compile_probe(self, probe: Dict[str, Any], output_dir: str) -> str:
        """Compile a single Rust probe to a static Linux binary.

        Args:
            probe: Probe descriptor from :meth:`discover_probes`.
            output_dir: Directory to place the compiled binary.

        Returns:
            Absolute path to the compiled binary.

        Raises:
            ProbeBuilderError: If compilation fails.
        """
        _require_tool("rustc", "Rust compiler (rustc) not found. Install via https://rustup.rs")

        name = probe["name"]
        kind = probe["kind"]
        out_binary = str(Path(output_dir) / name)

        if kind == "single_file":
            self._compile_single_file(probe["path"], out_binary)
        elif kind == "cargo":
            self._compile_cargo_project(probe["path"], out_binary)
        else:
            raise ProbeBuilderError(f"Unknown probe kind: {kind}")

        return out_binary

    def _compile_single_file(self, rs_path: str, out_binary: str) -> None:
        """Compile a single .rs file using rustc directly."""
        _require_tool("rustc", "Rust compiler (rustc) not found. Install via https://rustup.rs")

        cmd = [
            "rustc",
            "--target",
            MUSL_TARGET,
            "--edition",
            "2021",
            "-C",
            "opt-level=3",
            "-C",
            "target-feature=+crt-static",
            "-o",
            out_binary,
            rs_path,
        ]
        _run_cmd(cmd, f"Failed to compile {rs_path}")

    def _compile_cargo_project(self, project_path: str, out_binary: str) -> None:
        """Compile a Cargo project and copy the release binary out."""
        _require_tool("cargo", "Cargo not found. Install via https://rustup.rs")

        cmd = [
            "cargo",
            "build",
            "--release",
            "--target",
            MUSL_TARGET,
            "--manifest-path",
            str(Path(project_path) / "Cargo.toml"),
        ]
        _run_cmd(cmd, f"Failed to build Cargo project at {project_path}")

        # Find the binary in target/<MUSL_TARGET>/release/
        name = Path(project_path).name
        built = Path(project_path) / "target" / MUSL_TARGET / "release" / name
        if not built.exists():
            raise ProbeBuilderError(
                f"Expected binary not found at {built}. "
                f"Ensure the Cargo.toml [[bin]] name matches the directory name '{name}'."
            )

        shutil.copy2(str(built), out_binary)

    # ── Image Building ────────────────────────────────────

    def build_image(
        self,
        probe_name: str,
        binary_path: str,
        scenario_name: str = "probe",
    ) -> str:
        """Build a minimal container image containing the probe binary.

        Args:
            probe_name: Name of the probe.
            binary_path: Path to the compiled binary.
            scenario_name: Scenario name for the image tag.

        Returns:
            Full image tag (e.g. ``chaosprobe/my-scenario-check-db:a1b2c3``).

        Raises:
            ProbeBuilderError: If container build fails.
        """
        _require_tool("docker", "Docker not found. Install Docker to build probe images.")

        # Generate a short content hash for the tag
        binary_bytes = Path(binary_path).read_bytes()
        tag_hash = hashlib.sha256(binary_bytes).hexdigest()[:8]

        image_name = f"{self._image_prefix()}/{scenario_name}-{probe_name}"
        image_tag = f"{image_name}:{tag_hash}"

        with tempfile.TemporaryDirectory(prefix="chaosprobe-probe-") as build_ctx:
            build_dir = Path(build_ctx)

            # Copy binary into build context
            dest_binary = build_dir / probe_name
            shutil.copy2(binary_path, str(dest_binary))
            dest_binary.chmod(0o755)

            dockerfile = build_dir / "Dockerfile"
            dockerfile.write_text(generate_dockerfile(probe_name))

            # Build
            cmd = [
                "docker",
                "build",
                "-t",
                image_tag,
                "-t",
                f"{image_name}:latest",
                str(build_dir),
            ]
            _run_cmd(cmd, f"Failed to build Docker image for probe '{probe_name}'")

        # Optionally push to the in-cluster registry
        if self.push:
            self._push_image(image_tag)
            self._push_image(f"{image_name}:latest")

        return image_tag

    def _push_image(self, image_tag: str, retries: int = 3) -> None:
        """Push an image to the in-cluster registry with ``crane`` (daemon-less).

        ``docker push`` runs inside the docker daemon, whose network may be
        isolated from the registry (e.g. Docker Desktop, where the daemon's VM
        can't reach the port-forward tunnel). ``crane`` runs in *this* process —
        which can — so we ``docker save`` the built image and ``crane push`` the
        tarball over plain HTTP (the registry is insecure).

        The destination is the localhost tunnel opened by :meth:`build_all` when
        present; the repository path is preserved so cmdProbe pods still pull the
        image from the node-reachable address in *image_tag*. With no tunnel
        (e.g. a direct ``probe build --push -r``), pushes to *image_tag*.
        """
        if self._push_host:
            # Same registry, re-addressed to the localhost tunnel; repo path kept.
            dest = f"{self._push_host}/{image_tag.split('/', 1)[1]}"
        else:
            dest = image_tag

        with tempfile.TemporaryDirectory(prefix="chaosprobe-push-") as td:
            tarball = str(Path(td) / "image.tar")
            _run_cmd(["docker", "save", image_tag, "-o", tarball], f"Failed to save {image_tag}")
            for attempt in range(retries):
                try:
                    _run_cmd(
                        ["crane", "push", "--insecure", tarball, dest],
                        f"Failed to push image {dest}",
                    )
                    return
                except ProbeBuilderError:
                    if attempt < retries - 1:
                        wait = 5 * (attempt + 1)
                        print(
                            f"    Push failed (attempt {attempt + 1}/{retries}), "
                            f"retrying in {wait}s..."
                        )
                        time.sleep(wait)
                    else:
                        raise

    def _open_push_tunnel(self) -> None:
        """Open a `kubectl port-forward` to the in-cluster registry Service.

        Sets ``self._push_host`` to ``127.0.0.1:<port>`` once reachable. Uses
        whatever kubeconfig is in the environment (the caller sets KUBECONFIG).
        """
        _require_tool("kubectl", "kubectl not found — needed to reach the in-cluster registry.")
        port = _free_local_port()
        self._tunnel = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                "-n",
                REGISTRY_NAMESPACE,
                f"svc/{REGISTRY_SERVICE}",
                f"{port}:{REGISTRY_TARGET_PORT}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            if _port_accepts(port):
                self._push_host = f"127.0.0.1:{port}"
                return
            if self._tunnel.poll() is not None:
                err = self._tunnel.stderr.read().decode().strip() if self._tunnel.stderr else ""
                self._close_push_tunnel()
                raise ProbeBuilderError(f"registry port-forward failed: {err or 'exited early'}")
            time.sleep(0.5)
        self._close_push_tunnel()
        raise ProbeBuilderError(
            "registry port-forward did not become ready. Is the in-cluster "
            "registry installed? Run `chaosprobe init`."
        )

    def _close_push_tunnel(self) -> None:
        """Tear down the push tunnel (idempotent)."""
        if self._tunnel is not None:
            self._tunnel.terminate()
            try:
                self._tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._tunnel.kill()
            self._tunnel = None
        self._push_host = None

    def _image_prefix(self) -> str:
        """Image prefix used for tagging — just the registry/prefix.

        e.g. ``chaosprobe`` (local) or ``192.168.56.11:30500`` (in-cluster).
        """
        return self.registry

    # ── Orchestrator ──────────────────────────────────────

    def build_all(
        self,
        scenario_path: str,
        scenario_name: Optional[str] = None,
    ) -> Dict[str, str]:
        """Discover, compile, and package all Rust probes in a scenario.

        Args:
            scenario_path: Path to the scenario directory.
            scenario_name: Optional name for image tagging; defaults to
                the scenario directory name.

        Returns:
            Mapping of probe name to built image tag::

                {"check-db": "chaosprobe/my-scenario-check-db:a1b2c3", ...}

        Raises:
            ProbeBuilderError: If any probe fails to build.
        """
        probes = self.discover_probes(scenario_path)
        if not probes:
            return {}

        if scenario_name is None:
            scenario_name = Path(scenario_path).name

        built_images: Dict[str, str] = {}
        failures: List[Tuple[str, str]] = []

        with tempfile.TemporaryDirectory(prefix="chaosprobe-build-") as tmp:
            # One push tunnel for the whole batch (only needed when pushing).
            if self.push:
                ensure_crane()  # daemon-less pusher; auto-installs if missing
                self._open_push_tunnel()
            try:
                for probe in probes:
                    name = probe["name"]
                    print(f"  Building Rust probe '{name}' ({probe['kind']})...")

                    try:
                        binary_path = self.compile_probe(probe, tmp)
                        image_tag = self.build_image(name, binary_path, scenario_name)
                        built_images[name] = image_tag
                        print(f"    → {image_tag}")
                    except (ProbeBuilderError, Exception) as exc:
                        print(f"    ERROR: probe '{name}' failed: {exc}")
                        failures.append((name, str(exc)))
            finally:
                self._close_push_tunnel()

        if failures:
            # Fail-fast: silent drops produce experiments that *look* complete
            # but are missing probes (results/20260519-130102: 2 of 5 probes
            # had no verdict because the build swallowed transient push errors,
            # masking the loss).  An aborted run is a clearer signal.
            details = "\n".join(f"    - {n}: {e}" for n, e in failures)
            raise ProbeBuilderError(
                f"{len(failures)} of {len(probes)} probes failed to build:\n{details}"
            )

        return built_images


def patch_probe_images(
    experiments: List[Dict[str, Any]],
    built_images: Dict[str, str],
) -> int:
    """Patch cmdProbe source.image fields with built image tags.

    For each cmdProbe whose name matches a key in *built_images*, the
    ``source.image`` field is set and ``command`` is prefixed with
    ``/probe/<name>`` if not already set.

    Args:
        experiments: List of experiment entries (``{file, spec}`` dicts)
            from the scenario loader. **Modified in-place.**
        built_images: Mapping of probe name → image tag from
            :meth:`RustProbeBuilder.build_all`.

    Returns:
        Number of probes patched.
    """
    patched = 0
    for exp_entry in experiments:
        engine_spec = exp_entry.get("spec", {}).get("spec", {})
        for exp in engine_spec.get("experiments", []):
            for probe in exp.get("spec", {}).get("probe", []):
                if probe.get("type") != "cmdProbe":
                    continue

                probe_name = probe.get("name", "")
                if probe_name not in built_images:
                    continue

                inputs = probe.setdefault("cmdProbe/inputs", {})
                source = inputs.setdefault("source", {})
                source["image"] = built_images[probe_name]

                # IfNotPresent so probe pods don't hit the registry on
                # every tick — the image is pre-pulled onto each worker
                # node at run start (see ``prepull_probe_images``).  The
                # image tag is content-hashed, so a rebuild produces a
                # new tag and the cache miss forces a fresh pull anyway.
                # Overwrite any scenario-level value: builder owns this
                # policy, and a stray ``Always`` in the YAML defeats the
                # prepull and reintroduces Unknown verdicts under chaos.
                source["imagePullPolicy"] = "IfNotPresent"

                # Set default command to the binary path if not customised
                if not inputs.get("command") or inputs["command"] == probe_name:
                    inputs["command"] = f"/probe/{probe_name}"

                patched += 1

    return patched


# ── Utilities ─────────────────────────────────────────────


def _require_tool(name: str, message: str) -> None:
    """Raise ProbeBuilderError if a CLI tool is not found on PATH."""
    if shutil.which(name) is None:
        raise ProbeBuilderError(message)


def _run_cmd(cmd: List[str], error_msg: str) -> subprocess.CompletedProcess:
    """Run a subprocess and raise ProbeBuilderError on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        return result
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise ProbeBuilderError(f"{error_msg}: {detail}") from exc


def extract_cmdprobe_images(experiments: List[Dict[str, Any]]) -> List[str]:
    """Return the unique set of cmdProbe ``source.image`` values."""
    seen: List[str] = []
    for exp_entry in experiments:
        engine_spec = exp_entry.get("spec", {}).get("spec", {})
        for exp in engine_spec.get("experiments", []):
            for probe in exp.get("spec", {}).get("probe", []):
                if probe.get("type") != "cmdProbe":
                    continue
                image = probe.get("cmdProbe/inputs", {}).get("source", {}).get("image", "")
                if image and image != "auto" and image not in seen:
                    seen.append(image)
    return seen


def _imagepull_failure(pod: Any) -> Optional[Tuple[str, str]]:
    """Return ``(image, reason)`` for the first container stuck pulling an image.

    Scans a pod's init and main container statuses for a container waiting on
    ``ImagePullBackOff`` — the stable state the kubelet settles into once an
    image genuinely cannot be pulled (e.g. an untrusted registry). Returns
    ``None`` while the pod is still progressing. ``pod`` is the opaque (untyped)
    kubernetes V1Pod returned by ``read_namespaced_pod_status``.
    """
    status = pod.status
    statuses = list(status.init_container_statuses or []) + list(status.container_statuses or [])
    for cs in statuses:
        waiting = getattr(cs.state, "waiting", None) if cs.state else None
        if waiting is not None and waiting.reason == "ImagePullBackOff":
            return cs.image, waiting.reason
    return None


def prepull_probe_images(
    namespace: str,
    images: List[str],
    worker_nodes: List[str],
    timeout: int = 300,
    secret_name: str = "chaosprobe-registry",
) -> int:
    """Pre-pull probe images onto each worker node's local cache.

    Creates one short-lived Pod per worker node with every probe image
    listed as an init container (each running ``true``).  The kubelet
    pulls the image before starting the init container; once the pod
    completes, the image layers stay cached on the node.  Combined
    with ``imagePullPolicy: IfNotPresent`` on the probe specs, this
    eliminates per-tick registry round-trips — the dominant source
    of cmdProbe Unknown verdicts under chaos.

    Slow or transient pulls are best-effort: a pod that fails to schedule
    or times out is logged and skipped; remaining nodes continue. An
    *unpullable* image is not — if any pod reports ``ImagePullBackOff``
    (the image genuinely cannot be pulled, e.g. an untrusted registry),
    this raises ``click.ClickException`` to abort the run fast rather than
    wait out the timeout and proceed on missing images.

    Args:
        namespace: Namespace to create the pre-pull pods in (must have
            the registry imagePullSecret attached to its default SA).
        images: Probe images to pull (typically from
            :func:`extract_cmdprobe_images`).
        worker_nodes: Node names to pull onto — usually the schedulable
            worker nodes from the placement mutator.
        timeout: Per-pod timeout in seconds.
        secret_name: Name of the docker-registry imagePullSecret.

    Returns:
        Number of (node, image) pulls successfully completed.

    Raises:
        click.ClickException: if a probe image is unpullable (ImagePullBackOff).
    """
    if not images or not worker_nodes:
        return 0

    import time as _time

    import click as _click
    from kubernetes import client as _k8s_client
    from kubernetes.client.rest import ApiException as _ApiException

    from chaosprobe.k8s import ensure_k8s_config

    ensure_k8s_config()
    core_api = _k8s_client.CoreV1Api()

    init_containers = [
        {
            "name": f"pull-{i}",
            "image": img,
            "imagePullPolicy": "IfNotPresent",
            "command": ["true"],
        }
        for i, img in enumerate(images)
    ]
    pod_spec_template = {
        "restartPolicy": "Never",
        "imagePullSecrets": [{"name": secret_name}],
        # Tolerate everything so we can pre-pull even on tainted nodes
        # (e.g. control-plane during single-node debugging).
        "tolerations": [{"operator": "Exists"}],
        "initContainers": init_containers,
        # Main container must exist; reuse first probe image so we
        # don't pull an extra base image just to satisfy this slot.
        "containers": [
            {
                "name": "done",
                "image": images[0],
                "imagePullPolicy": "IfNotPresent",
                "command": ["true"],
            }
        ],
    }

    ts = int(_time.time())
    pod_names: List[str] = []
    for node_name in worker_nodes:
        # K8s pod names limited to 63 chars; truncate aggressively.
        short_node = node_name.replace(".", "-")[:20]
        pod_name = f"chaosprobe-prepull-{short_node}-{ts}"[:63]
        body = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace,
                "labels": {"chaosprobe.io/prepull": "true"},
            },
            "spec": {"nodeName": node_name, **pod_spec_template},
        }
        try:
            core_api.create_namespaced_pod(namespace, body)
            pod_names.append(pod_name)
        except _ApiException as e:
            _click.echo(
                f"  WARNING: pre-pull pod create failed on {node_name}: {e.reason}",
                err=True,
            )

    pending = set(pod_names)
    succeeded: set = set()
    try:
        start = _time.time()
        while pending and (_time.time() - start) < timeout:
            for name in list(pending):
                try:
                    pod = core_api.read_namespaced_pod_status(name, namespace)
                except _ApiException:
                    # Transient read error — re-poll this pod next iteration.
                    continue
                # Fail fast on an unpullable image: it will never self-heal, and
                # proceeding runs the whole matrix on missing probe images.
                stuck = _imagepull_failure(pod)
                if stuck is not None:
                    image, reason = stuck
                    raise _click.ClickException(
                        f"Probe image cannot be pulled: {image} ({reason}).\n"
                        f"  A worker node's containerd does not trust the in-cluster "
                        f"registry, so probe images fail to pull — aborting before a "
                        f"doomed run.\n"
                        f"  Fix the node trust and retry: re-run `chaosprobe cluster "
                        f"vagrant deploy` (it configures the trust), or apply the manual "
                        f"per-node step in chaosprobe/manifests/README.md (section 2)."
                    )
                phase = (pod.status.phase or "").lower()
                if phase == "succeeded":
                    succeeded.add(name)
                    pending.discard(name)
                elif phase == "failed":
                    pending.discard(name)
                    _click.echo(
                        f"  WARNING: pre-pull pod {name} failed; some images may not be cached",
                        err=True,
                    )
            if pending:
                _time.sleep(2)

        if pending:
            _click.echo(
                f"  WARNING: pre-pull timed out for {len(pending)} pod(s); "
                f"those nodes will pull on first probe tick",
                err=True,
            )
    finally:
        # Cleanup all created pods (succeeded + still-pending), even if we
        # aborted on an unpullable image.
        for name in pod_names:
            try:
                core_api.delete_namespaced_pod(
                    name, namespace, body=_k8s_client.V1DeleteOptions(grace_period_seconds=0)
                )
            except _ApiException:
                # Best-effort cleanup — the pod may already be gone or terminating.
                pass

    return len(succeeded) * len(images)
