"""Shared type surface for the LitmusSetup mixins.

``LitmusSetup`` (see ``setup.py``) is assembled from several mixins
(``_ChaosCenterMixin``, ``_ChaosCenterAPIMixin``, ``_ComponentsMixin``,
``_VagrantMixin``).  Each mixin freely uses attributes and methods that are
actually provided by *one of the other* mixins or by ``LitmusSetup`` itself,
so on its own a mixin has no idea those members exist and mypy reports
``has no attribute`` for every cross-mixin reference.

This base declares that shared surface once.  Every mixin inherits from it,
so each one type-checks independently; the concrete classes override the
method stubs with the real implementations (the stubs are shadowed at
runtime via the MRO, since the base sits last).
"""

from pathlib import Path
from typing import Any, Optional, Protocol


class _LitmusSetupBase(Protocol):
    """Declares the attributes/methods shared across the LitmusSetup mixins.

    A ``Protocol`` so the method stubs are a pure type surface (no
    empty-body errors); the concrete mixins/LitmusSetup provide the real
    implementations and shadow these via the MRO.
    """

    # ── Class constants (concrete values live on LitmusSetup) ──────────
    LITMUS_NAMESPACE: str
    VAGRANT_DIR: Path
    VAGRANTFILE_TEMPLATE: str
    CHAOSCENTER_HELM_CHART: str
    CHAOSCENTER_RELEASE_NAME: str
    CHAOSCENTER_FRONTEND_SVC: str
    CHAOSCENTER_FRONTEND_PORT: int
    CHAOSCENTER_SERVER_PORT: int
    CHAOSCENTER_DEFAULT_USER: str
    CHAOSCENTER_DEFAULT_PASS: str

    # ── Instance attributes (set up in LitmusSetup; k8s clients are opaque) ──
    core_api: Any
    apps_api: Any
    storage_api: Any
    _k8s_initialized: bool

    # ── Cross-mixin methods (real implementations on the concrete mixins) ──
    def _ensure_namespace(self, namespace: str) -> None: ...

    def _apply_manifest(self, manifest: str, namespace: str) -> None: ...

    def generate_inventory(
        self,
        hosts: list[dict],
        cluster_name: str = "chaosprobe",
        output_dir: Optional[Path] = None,
    ) -> Path: ...

    def deploy_cluster(
        self,
        inventory_dir: Path,
        extra_vars: Optional[dict] = None,
        become_pass: Optional[str] = None,
    ) -> bool: ...

    def fetch_kubeconfig(
        self,
        control_plane_host: str,
        ansible_user: str = "root",
        output_path: Optional[Path] = None,
        ssh_key: Optional[Path] = None,
    ) -> Path: ...

    def _chaoscenter_api_request(
        self,
        url: str,
        method: str = "POST",
        data: Optional[dict] = None,
        token: Optional[str] = None,
        headers: Optional[dict] = None,
        timeout: int = 30,
    ) -> dict: ...

    def _chaoscenter_gql_url(self, base_host: str) -> str: ...

    def _chaoscenter_auth_url(self, base_host: str) -> str: ...

    def _chaoscenter_login(
        self,
        auth_url: str,
        username: str = "",
        password: str = "",
    ) -> tuple[str, str]: ...

    def _chaoscenter_list_environments(
        self,
        gql_url: str,
        project_id: str,
        token: str,
    ) -> list[dict]: ...

    def _chaoscenter_list_infras(
        self,
        gql_url: str,
        project_id: str,
        token: str,
    ) -> list[dict]: ...

    def _chaoscenter_create_environment(
        self,
        gql_url: str,
        project_id: str,
        env_name: str,
        token: str,
    ) -> str: ...

    def _chaoscenter_server_internal_url(self) -> str: ...

    def _chaoscenter_register_infra(
        self,
        gql_url: str,
        project_id: str,
        env_id: str,
        namespace: str,
        token: str,
    ) -> dict: ...

    def _wait_for_infra_active(
        self,
        gql_url: str,
        project_id: str,
        token: str,
        infra_id: str,
        timeout: int = 60,
    ) -> bool: ...

    def _subscriber_diagnostics(self, namespace: str) -> str: ...
