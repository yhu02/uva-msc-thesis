"""Unit tests for the ``chaosprobe run`` concurrency lock (`_acquire_run_lock`).

Two concurrent runs mutate the same cluster and corrupt each other, so ``run``
takes a non-blocking advisory ``flock`` on ``~/.chaosprobe/run.lock`` before any
cluster work. These tests redirect ``Path.home()`` to a tmp dir and exercise the
free-lock, held-lock, and empty-holder paths. A second open file description
blocks even within this one process, so the contention path is testable without
spawning a subprocess.
"""

import fcntl
import os

import pytest

from chaosprobe.commands import run_cmd


@pytest.fixture(autouse=True)
def _reset_lock(monkeypatch, tmp_path):
    """Point the lock at a tmp dir and drop any fd the test left held."""
    monkeypatch.setattr(run_cmd.Path, "home", lambda: tmp_path)
    yield
    held = run_cmd._run_lock_file
    if held is not None:
        held.close()
        run_cmd._run_lock_file = None


def test_acquires_free_lock_and_records_holder(tmp_path):
    run_cmd._acquire_run_lock()

    assert run_cmd._run_lock_file is not None
    holder = (tmp_path / ".chaosprobe" / "run.lock").read_text()
    assert holder.startswith(f"PID {os.getpid()} started ")


def test_held_lock_reports_holder_and_exits(tmp_path):
    lock_path = tmp_path / ".chaosprobe" / "run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("PID 999 started 2026-06-07T10:00:00+00:00\n")
    holder_fd = open(lock_path, "a+")
    fcntl.flock(holder_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(SystemExit) as exc:
            run_cmd._acquire_run_lock()
        assert exc.value.code == 1
        # The helper must not have stolen the lock for the blocked caller.
        assert run_cmd._run_lock_file is None
    finally:
        holder_fd.close()


def test_held_lock_with_empty_holder_falls_back_to_unknown(tmp_path):
    lock_path = tmp_path / ".chaosprobe" / "run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("   \n")  # blank holder line → "unknown" fallback
    holder_fd = open(lock_path, "a+")
    fcntl.flock(holder_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(SystemExit) as exc:
            run_cmd._acquire_run_lock()
        assert exc.value.code == 1
    finally:
        holder_fd.close()
