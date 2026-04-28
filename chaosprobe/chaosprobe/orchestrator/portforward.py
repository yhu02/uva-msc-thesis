"""Port-forward lifecycle management for kubectl port-forward processes.

Provides functions to start, ensure, monitor, and clean up kubectl
port-forward processes used to reach in-cluster services (Prometheus,
Neo4j, ChaosCenter, frontend) from the local machine.
"""

import socket
import subprocess
import threading
import time
from typing import Any, Dict, Optional

# Module-level state — tracks all active port-forward processes
_procs: Dict[tuple, Any] = {}
_specs: Dict[tuple, list[str]] = {}  # (svc, ns) -> ports list for auto-restart
_monitor_event: Optional[threading.Event] = None
_monitor_thread: Optional[threading.Thread] = None


def check_port(host: str, port: int) -> bool:
    """Check if a TCP port is reachable."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        sock.connect((host, port))
        sock.close()
        return True
    except (ConnectionRefusedError, OSError):
        sock.close()
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
    _procs[(svc, ns)] = proc
    _specs[(svc, ns)] = ports
    time.sleep(3)


def ensure(svc: str, ns: str, ports: list[str], host: str, port: int) -> bool:
    """Check if port-forward is alive; restart if dead. Returns True if reachable."""
    proc = _procs.get((svc, ns))
    if proc and proc.poll() is not None:
        start(svc, ns, ports)
    elif not proc:
        start(svc, ns, ports)
    for _attempt in range(15):
        if check_port(host, port):
            return True
        # If process died, restart it before retrying
        proc = _procs.get((svc, ns))
        if proc and proc.poll() is not None:
            start(svc, ns, ports)
        time.sleep(2)
    return False


def _monitor_loop(stop_event: threading.Event):
    """Background loop that restarts dead port-forward processes."""
    while not stop_event.is_set():
        for key, proc in list(_procs.items()):
            if proc and proc.poll() is not None:
                # Process died — restart it
                svc, ns = key
                ports = _specs.get(key, [])
                if ports:
                    new_proc = subprocess.Popen(
                        ["kubectl", "port-forward", f"svc/{svc}", "-n", ns] + ports,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    _procs[key] = new_proc
        stop_event.wait(10)  # check every 10 seconds


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
    for proc in _procs.values():
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    _procs.clear()
    _specs.clear()
