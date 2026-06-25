"""Tests for the chaos context safety gate in ``chaosprobe.k8s`` (REVIEW.md W8).

ChaosProbe mutates / injects chaos into whatever the active kube context points
at, so ``ensure_k8s_config`` refuses denylisted contexts (and, when pinned,
anything but the expected one) before loading a kubeconfig.
"""

import pytest
from kubernetes import config as kube_config

from chaosprobe import k8s
from chaosprobe.k8s import (
    ALLOW_ANY_CONTEXT_ENV,
    EXPECTED_CONTEXT_ENV,
    UnsafeKubeContextError,
    assert_safe_context,
)


def _patch_active(monkeypatch, active):
    """Make list_kube_config_contexts report *active* as the active context."""
    monkeypatch.setattr(k8s.config, "list_kube_config_contexts", lambda: ([], active))


def _named(name):
    return {"name": name, "context": {}}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(EXPECTED_CONTEXT_ENV, raising=False)
    monkeypatch.delenv(ALLOW_ANY_CONTEXT_ENV, raising=False)


class TestAssertSafeContext:
    def test_safe_context_passes(self, monkeypatch):
        _patch_active(monkeypatch, _named("kubernetes-admin@chaosprobe"))
        assert_safe_context()  # no raise

    @pytest.mark.parametrize("bad_name", ["aks-prod", "myAKSCluster", "aie-platform"])
    def test_denylisted_context_rejected(self, monkeypatch, bad_name):
        _patch_active(monkeypatch, _named(bad_name))
        with pytest.raises(UnsafeKubeContextError) as exc:
            assert_safe_context()
        assert exc.value.context_name == bad_name

    def test_expected_context_match_passes(self, monkeypatch):
        monkeypatch.setenv(EXPECTED_CONTEXT_ENV, "thesis")
        _patch_active(monkeypatch, _named("thesis"))
        assert_safe_context()

    def test_expected_context_mismatch_rejected(self, monkeypatch):
        monkeypatch.setenv(EXPECTED_CONTEXT_ENV, "thesis")
        _patch_active(monkeypatch, _named("some-other"))
        with pytest.raises(UnsafeKubeContextError):
            assert_safe_context()

    def test_expected_context_overrides_denylist(self, monkeypatch):
        # Explicitly pinning an AKS-named context is the user's deliberate call.
        monkeypatch.setenv(EXPECTED_CONTEXT_ENV, "aks-onpurpose")
        _patch_active(monkeypatch, _named("aks-onpurpose"))
        assert_safe_context()

    def test_allow_any_bypasses_without_reading_config(self, monkeypatch):
        monkeypatch.setenv(ALLOW_ANY_CONTEXT_ENV, "1")

        def explode():
            raise AssertionError("list_kube_config_contexts must not be called")

        monkeypatch.setattr(k8s.config, "list_kube_config_contexts", explode)
        assert_safe_context()

    def test_unreadable_kubeconfig_does_not_block(self, monkeypatch):
        def boom():
            raise kube_config.ConfigException("no config")

        monkeypatch.setattr(k8s.config, "list_kube_config_contexts", boom)
        assert_safe_context()  # quiet; load_kube_config surfaces the real error

    def test_no_active_context_does_not_block(self, monkeypatch):
        _patch_active(monkeypatch, None)
        assert_safe_context()

    def test_malformed_active_context_does_not_block(self, monkeypatch):
        _patch_active(monkeypatch, {"context": {}})  # no "name" key
        assert_safe_context()


class TestEnsureK8sConfigGate:
    def test_already_configured_short_circuits(self, monkeypatch):
        monkeypatch.setattr(k8s, "_configured", True)
        called = []
        monkeypatch.setattr(k8s.config, "load_incluster_config", lambda: called.append("in"))
        monkeypatch.setattr(k8s.config, "load_kube_config", lambda: called.append("kube"))
        k8s.ensure_k8s_config()
        assert called == []

    def test_kubeconfig_path_runs_gate(self, monkeypatch):
        monkeypatch.setattr(k8s, "_configured", False)
        gate_calls = []
        monkeypatch.setattr(k8s, "assert_safe_context", lambda: gate_calls.append(1))

        def no_incluster():
            raise kube_config.ConfigException("not in cluster")

        monkeypatch.setattr(k8s.config, "load_incluster_config", no_incluster)
        monkeypatch.setattr(k8s.config, "load_kube_config", lambda: None)
        k8s.ensure_k8s_config()
        assert gate_calls == [1]

    def test_incluster_path_skips_gate(self, monkeypatch):
        monkeypatch.setattr(k8s, "_configured", False)
        gate_calls = []
        monkeypatch.setattr(k8s, "assert_safe_context", lambda: gate_calls.append(1))
        monkeypatch.setattr(k8s.config, "load_incluster_config", lambda: None)
        k8s.ensure_k8s_config()
        assert gate_calls == []
