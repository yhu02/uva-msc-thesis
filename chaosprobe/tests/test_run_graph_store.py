"""Unit tests for the Neo4j connect helper extracted from ``run``.

Neo4j is the required primary data store, so a missing driver or a failed
connection must abort the run (now via ``click.ClickException``) — this logic
was inline in the ~440-line ``run`` command.
"""

import click
import pytest

from chaosprobe.commands import run_cmd


def test_returns_none_when_no_uri():
    assert run_cmd._connect_graph_store(None, "u", "p", "ns", {}) is None


def test_returns_store_on_success(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(run_cmd, "init_graph_store", lambda *a, **k: sentinel)
    assert run_cmd._connect_graph_store("bolt://x", "u", "p", "ns", {}) is sentinel


def test_raises_clickexception_on_missing_driver(monkeypatch):
    def boom(*a, **k):
        raise ImportError("neo4j not installed")

    monkeypatch.setattr(run_cmd, "init_graph_store", boom)
    with pytest.raises(click.ClickException):
        run_cmd._connect_graph_store("bolt://x", "u", "p", "ns", {})


def test_raises_clickexception_on_connection_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(run_cmd, "init_graph_store", boom)
    with pytest.raises(click.ClickException):
        run_cmd._connect_graph_store("bolt://x", "u", "p", "ns", {})
