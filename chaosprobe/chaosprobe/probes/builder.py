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
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from chaosprobe.probes.templates import (
    generate_dockerfile,
)

# Default container registry host. Override with the CHAOSPROBE_REGISTRY
# env var or the --registry flag on `chaosprobe probe build`. The image
# namespace comes from CHAOSPROBE_REGISTRY_USER, so the final image path
# is ``{registry}/{user}/{scenario}-{probe}:{hash}``.
DEFAULT_REGISTRY = "ghcr.io"

# Rust musl target for static Linux binaries
MUSL_TARGET = "x86_64-unknown-linux-musl"


class ProbeBuilderError(Exception):
    """Raised when probe compilation or image build fails."""


class RustProbeBuilder:
    """Compile Rust probes and package them as container images.

    Args:
        registry: Container registry host (e.g. ``ghcr.io``). May also be
            a host+namespace string like ``ghcr.io/some-org`` to override
            the namespace; in that case ``user`` is ignored for the image
            path (but still used for ``docker login``).
        user: Registry namespace and login user (e.g. ``yhu02``). Falls
            back to the ``CHAOSPROBE_REGISTRY_USER`` env var.
        push: If True, push the built image to the remote registry.
    """

    def __init__(
        self,
        registry: str = DEFAULT_REGISTRY,
        user: Optional[str] = None,
        push: bool = False,
    ):
        self.registry = registry.rstrip("/")
        self.user = user if user is not None else os.environ.get("CHAOSPROBE_REGISTRY_USER", "")
        self.push = push
        self._login_done = False

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
            "--target", MUSL_TARGET,
            "--edition", "2021",
            "-C", "opt-level=3",
            "-C", "target-feature=+crt-static",
            "-o", out_binary,
            rs_path,
        ]
        _run_cmd(cmd, f"Failed to compile {rs_path}")

    def _compile_cargo_project(self, project_path: str, out_binary: str) -> None:
        """Compile a Cargo project and copy the release binary out."""
        _require_tool("cargo", "Cargo not found. Install via https://rustup.rs")

        cmd = [
            "cargo", "build",
            "--release",
            "--target", MUSL_TARGET,
            "--manifest-path", str(Path(project_path) / "Cargo.toml"),
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

            # Write Dockerfile.  CHAOSPROBE_SOURCE_REPO (if set) is written
            # as the OCI image source label so GHCR auto-links the package
            # to that repo on the package's GitHub page.
            source_repo = os.environ.get("CHAOSPROBE_SOURCE_REPO", "")
            dockerfile = build_dir / "Dockerfile"
            dockerfile.write_text(generate_dockerfile(probe_name, source_repo))

            # Build
            cmd = [
                "docker", "build",
                "-t", image_tag,
                "-t", f"{image_name}:latest",
                str(build_dir),
            ]
            _run_cmd(cmd, f"Failed to build Docker image for probe '{probe_name}'")

        # Optionally push to remote registry
        if self.push:
            self._push_image(image_tag)
            self._push_image(f"{image_name}:latest")
            self._print_visibility_hint(image_name)

        return image_tag

    def _push_image(self, image_tag: str, retries: int = 3) -> None:
        """Push an image to the remote container registry with retries."""
        import time as _time

        self._ensure_login()
        for attempt in range(retries):
            try:
                _run_cmd(
                    ["docker", "push", image_tag],
                    f"Failed to push image {image_tag}",
                )
                return
            except ProbeBuilderError:
                if attempt < retries - 1:
                    wait = 5 * (attempt + 1)
                    print(f"    Push failed (attempt {attempt + 1}/{retries}), retrying in {wait}s...")
                    _time.sleep(wait)
                else:
                    raise

    def _print_visibility_hint(self, image_name: str) -> None:
        """Print the GHCR settings URL when a pushed package is still private.

        GitHub does not expose package visibility changes via REST for
        user-owned container packages -- it must be flipped once via the
        web UI (Settings -> Change visibility -> Public).  We GET the
        package's current visibility and print the settings URL only
        when it's still private, so the hint disappears after the
        one-time manual flip.
        """
        if self._registry_host() != "ghcr.io":
            return
        token = os.environ.get("CHAOSPROBE_REGISTRY_PASSWORD")
        if not token:
            return

        parts = image_name.split("/", 2)
        if len(parts) < 3:
            return
        owner, package_name = parts[1], parts[2]

        import json
        import urllib.parse
        import urllib.request

        encoded = urllib.parse.quote(package_name, safe="")
        get_url = f"https://api.github.com/user/packages/container/{encoded}"
        req = urllib.request.Request(
            get_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        visibility = None
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                visibility = json.loads(resp.read()).get("visibility")
        except Exception:
            pass  # Lookup is best-effort; fall through and show the hint.

        if visibility == "public":
            return

        settings_url = (
            f"https://github.com/users/{owner}/packages/container/{encoded}/settings"
        )
        print(f"    Set visibility to Public: {settings_url}")

    def _registry_host(self) -> str:
        """Return the registry hostname (e.g. ``ghcr.io``).

        Treats the first path segment as a hostname if it contains a dot.
        Bare prefixes like ``myuser`` map to ``docker.io`` (Docker's
        convention for unqualified image names).
        """
        head = self.registry.split("/", 1)[0]
        return head if "." in head else "docker.io"

    def _image_prefix(self) -> str:
        """Compose the full image prefix used for tagging.

        - ``ghcr.io`` + user ``yhu02`` → ``ghcr.io/yhu02``
        - ``ghcr.io/some-org`` (registry already includes a namespace)
          + any user → ``ghcr.io/some-org`` (user is not appended)
        - ``chaosprobe`` (local-only prefix, no user) → ``chaosprobe``
        """
        if self.user and "/" not in self.registry:
            return f"{self.registry}/{self.user}"
        return self.registry

    def _ensure_login(self) -> None:
        """Run ``docker login`` once per builder before the first push.

        If ``CHAOSPROBE_REGISTRY_PASSWORD`` (and a user) are set, performs
        a programmatic ``docker login`` via ``--password-stdin``.  Otherwise
        assumes the user has already authenticated (e.g. ``docker login
        ghcr.io``) and credentials exist in ``~/.docker/config.json``.
        """
        if self._login_done:
            return
        host = self._registry_host()
        password = os.environ.get("CHAOSPROBE_REGISTRY_PASSWORD")
        if self.user and password:
            try:
                subprocess.run(
                    ["docker", "login", host, "-u", self.user, "--password-stdin"],
                    input=password.encode(),
                    capture_output=True,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or b"").decode().strip()
                raise ProbeBuilderError(
                    f"docker login {host} failed: {stderr or e.returncode}"
                ) from e
        self._login_done = True

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

        with tempfile.TemporaryDirectory(prefix="chaosprobe-build-") as tmp:
            for probe in probes:
                name = probe["name"]
                print(f"  Building Rust probe '{name}' ({probe['kind']})...")

                try:
                    binary_path = self.compile_probe(probe, tmp)
                    image_tag = self.build_image(name, binary_path, scenario_name)
                    built_images[name] = image_tag
                    print(f"    → {image_tag}")
                except (ProbeBuilderError, Exception) as exc:
                    print(f"    WARNING: probe '{name}' failed: {exc}")

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

                # Ensure the probe pod can pull private images
                source.setdefault("imagePullPolicy", "Always")

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


def ensure_image_pull_secret(
    namespace: str,
    registry: str = DEFAULT_REGISTRY,
    user: Optional[str] = None,
    password: Optional[str] = None,
    secret_name: str = "chaosprobe-registry",
) -> bool:
    """Create or update a docker-registry imagePullSecret and attach it to
    the ``default`` service account so that LitmusChaos probe pods can pull
    private images.

    Returns True if the secret was created/updated, False if credentials
    are not available (skipped silently).
    """
    user = user or os.environ.get("CHAOSPROBE_REGISTRY_USER", "")
    password = password or os.environ.get("CHAOSPROBE_REGISTRY_PASSWORD", "")
    if not user or not password:
        return False

    # Determine registry server hostname
    head = registry.split("/", 1)[0]
    server = head if "." in head else "https://index.docker.io/v1/"

    # Create or replace the docker-registry secret
    _run_cmd(
        [
            "kubectl", "delete", "secret", secret_name,
            "-n", namespace, "--ignore-not-found",
        ],
        "Failed to delete old imagePullSecret",
    )
    _run_cmd(
        [
            "kubectl", "create", "secret", "docker-registry", secret_name,
            "-n", namespace,
            f"--docker-server={server}",
            f"--docker-username={user}",
            f"--docker-password={password}",
        ],
        "Failed to create imagePullSecret",
    )

    # Patch the default and litmus service accounts to use it
    import json as _json_mod

    patch = {"imagePullSecrets": [{"name": secret_name}]}
    patch_json = _json_mod.dumps(patch)
    for sa in ("default", "litmus-admin", "argo-chaos", "litmus"):
        try:
            _run_cmd(
                [
                    "kubectl", "patch", "serviceaccount", sa,
                    "-n", namespace,
                    "-p", patch_json,
                ],
                f"Failed to patch SA {sa}",
            )
        except ProbeBuilderError:
            pass  # SA may not exist in this namespace

    return True
