"""Port-forward lifecycle management for kubectl port-forward processes.

Provides functions to start, ensure, monitor, and clean up kubectl
port-forward processes used to reach in-cluster services (Prometheus,
Neo4j, ChaosCenter, frontend) from the local machine.
"""

import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# Module-level state — tracks all active port-forward processes.
# Mutated from both the main thread and the background monitor thread,
# so all reads/writes go through ``_lock``.
_procs: Dict[tuple, Any] = {}
_specs: Dict[tuple, list[str]] = {}  # (svc, ns) -> ports list for auto-restart
_lock = threading.Lock()
_monitor_event: Optional[threading.Event] = None
_monitor_thread: Optional[threading.Thread] = None


def check_port(host: str, port: int) -> bool:
    """Check if a TCP port is reachable."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        try:
            sock.connect((host, port))
            return True
        except (ConnectionRefusedError, OSError):
            return False


def http_reachable(url: str, timeout: float = 5.0) -> bool:
    """True when an HTTP GET to *url* gets any HTTP response from the server.

    Unlike :func:`check_port` (which only confirms the local listener accepts a
    TCP connection), this proves the forward actually reaches a live backend: a
    ``kubectl port-forward`` whose target pod was rescheduled (e.g. by a
    node-drain) keeps its listener open but **resets the connection** on real
    data — that shows up here as a failure, not a false "reachable".  An HTTP
    error *status* (4xx/5xx) still means the tunnel reached the server, so it
    counts as reachable.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # reached the server; the app returned an error status
    except Exception:
        return False  # connection reset / refused / timeout: the tunnel is dead


def free_local_port(local_port: int) -> int:
    """Kill orphaned ``kubectl port-forward`` processes bound to *local_port*.

    A forward started with ``start_new_session=True`` survives the run that
    created it; the next run, seeing the local port still open, would otherwise
    **reuse that stale forward** (whose pod may be gone) instead of starting a
    fresh one.  Killing the orphan first guarantees each run gets a forward to a
    live pod.  Returns the number of processes killed.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", f"port-forward.* {local_port}:"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    pids: List[int] = [int(p) for p in out.stdout.split() if p.strip().isdigit()]
    killed = 0
    for pid in pids:
        try:
            subprocess.run(["kill", str(pid)], timeout=5)
            killed += 1
        except (OSError, subprocess.SubprocessError):
            pass
    if killed:
        time.sleep(1)  # let the listener release before a fresh start binds it
    return killed


def ensure_load_target(svc: str, ns: str, local_port: int, url: str) -> bool:
    """Establish/repair a port-forward that actually reaches a live pod.

    The HTTP-verified replacement for a bare :func:`ensure` on the load target.
    Probes *url* first (cheap); only when the tunnel does **not** reach a live
    backend does it kill any orphan on the port and start a fresh forward,
    re-probing (up to two heal attempts).  This catches the stale-but-alive
    tunnel — process up, TCP listener open, but the target pod rescheduled (e.g.
    by a node-drain) so every real request resets — that :func:`ensure` /
    :func:`check_port` cannot see.  Used by both the preflight setup and the
    per-iteration re-ensure, so mid-session pod reschedules heal too.  Returns
    ``True`` when the tunnel reaches a live backend.
    """
    if http_reachable(url):
        return True
    for _ in range(2):
        free_local_port(local_port)
        ensure(svc, ns, [f"{local_port}:80"], "localhost", local_port)
        if http_reachable(url):
            return True
    return False


def start(svc: str, ns: str, ports: list[str]):
    """Start a kubectl port-forward in the background and track it.

    Uses ``start_new_session=True`` so the process survives after the
    parent Python process exits (no SIGHUP on parent termination).
    """
    proc = subprocess.Popen(
        ["kubectl", "port-forward", f"svc/{svc}", "-n", ns] + ports,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    with _lock:
        _procs[(svc, ns)] = proc
        _specs[(svc, ns)] = ports
    time.sleep(3)


def ensure(svc: str, ns: str, ports: list[str], host: str, port: int) -> bool:
    """Check if port-forward is alive; restart if dead. Returns True if reachable."""
    with _lock:
        proc = _procs.get((svc, ns))
    if proc and proc.poll() is not None:
        start(svc, ns, ports)
    elif not proc:
        start(svc, ns, ports)
    for _attempt in range(15):
        if check_port(host, port):
            return True
        # If process died, restart it before retrying
        with _lock:
            proc = _procs.get((svc, ns))
        if proc and proc.poll() is not None:
            start(svc, ns, ports)
        time.sleep(2)
    return False


def _monitor_loop(stop_event: threading.Event):
    """Background loop that restarts dead port-forward processes."""
    while not stop_event.is_set():
        with _lock:
            snapshot = list(_procs.items())
        for key, proc in snapshot:
            if proc and proc.poll() is not None:
                # Process died — restart it
                svc, ns = key
                with _lock:
                    ports = list(_specs.get(key, []))
                if ports:
                    new_proc = subprocess.Popen(
                        ["kubectl", "port-forward", f"svc/{svc}", "-n", ns] + ports,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    with _lock:
                        _procs[key] = new_proc
        stop_event.wait(5)  # check every 5 seconds


def ensure_all() -> None:
    """Re-ensure every tracked port-forward is alive and reachable.

    Call this between strategies to recover from infrastructure
    disruptions (resource-starved nodes killing kubectl tunnels).
    """
    with _lock:
        specs_snapshot = [(key, list(ports)) for key, ports in _specs.items()]
    for key, ports in specs_snapshot:
        svc, ns = key
        with _lock:
            proc = _procs.get(key)
        if not proc or proc.poll() is not None:
            start(svc, ns, ports)
        # Extract the local port from the first port spec (e.g. "9090:80" -> 9090)
        local_port_str = ports[0].split(":")[0] if ports else ""
        if local_port_str.isdigit():
            local_port = int(local_port_str)
            if not check_port("localhost", local_port):
                # Kill stale process and restart
                with _lock:
                    proc = _procs.get(key)
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                start(svc, ns, ports)
                # Wait for port to become reachable
                for _ in range(10):
                    if check_port("localhost", local_port):
                        break
                    time.sleep(2)


def monitor_start():
    """Start the background port-forward health monitor."""
    global _monitor_event, _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return
    _monitor_event = threading.Event()
    _monitor_thread = threading.Thread(
        target=_monitor_loop, args=(_monitor_event,), daemon=True, name="pf-monitor"
    )
    _monitor_thread.start()


def monitor_stop():
    """Stop the background port-forward health monitor."""
    global _monitor_event, _monitor_thread
    if _monitor_event:
        _monitor_event.set()
    if _monitor_thread:
        _monitor_thread.join(timeout=15)
    _monitor_event = None
    _monitor_thread = None


def cleanup():
    """Stop the monitor and terminate all tracked port-forward processes."""
    monitor_stop()
    with _lock:
        procs = list(_procs.values())
        _procs.clear()
        _specs.clear()
    for proc in procs:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
