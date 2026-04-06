"""Base class for continuous probers with phase tracking.

Provides a reusable ``ContinuousProberBase`` that handles thread
lifecycle (start/stop), chaos phase boundaries, and time-series
collection.  Subclasses only need to implement ``_probe_loop``.
"""

import statistics
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class ContinuousProberBase:
    """Thread-safe base for continuous measurement probers.

    Handles start/stop, chaos-phase markers (pre-chaos, during-chaos,
    post-chaos), and basic time-series bookkeeping.

    Subclasses must implement ``_probe_loop()`` which runs in a
    background thread.  Call ``self._stop_event.wait(timeout=…)``
    between probe cycles to respect the stop signal.
    """

    def __init__(self, namespace: str, interval: float, name: str):
        self.namespace = namespace
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._time_series: List[Dict[str, Any]] = []
        self._start_time: Optional[float] = None
        self._chaos_start_time: Optional[float] = None
        self._chaos_end_time: Optional[float] = None
        self._probe_errors: int = 0
        self._thread_name = name

    def start(self) -> None:
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._probe_loop,
            daemon=True,
            name=self._thread_name,
        )
        self._thread.start()

    def mark_chaos_start(self) -> None:
        with self._lock:
            self._chaos_start_time = time.time()

    def mark_chaos_end(self) -> None:
        with self._lock:
            self._chaos_end_time = time.time()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=30)

    def _current_phase(self, now: float) -> str:
        with self._lock:
            chaos_start = self._chaos_start_time
            chaos_end = self._chaos_end_time
        if chaos_start is None or now < chaos_start:
            return "pre-chaos"
        if chaos_end is None or now < chaos_end:
            return "during-chaos"
        return "post-chaos"

    def _make_entry(self, now: float, phase: str) -> Dict[str, Any]:
        return {
            "timestamp": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "elapsed_s": round(now - (self._start_time or now), 1),
            "phase": phase,
        }

    def _probe_loop(self) -> None:
        raise NotImplementedError

    def _split_phases(self, series: List[Dict[str, Any]], metric_key: str) -> Dict[str, Any]:
        phases: Dict[str, List[Dict[str, Any]]] = {
            "pre-chaos": [],
            "during-chaos": [],
            "post-chaos": [],
        }
        for entry in series:
            phases.setdefault(entry.get("phase", "pre-chaos"), []).append(entry)
        result = {}
        for name, entries in phases.items():
            if not entries:
                result[name] = {"sampleCount": 0, metric_key: {}}
            else:
                result[name] = {
                    "sampleCount": len(entries),
                    metric_key: self._aggregate_operations(entries, metric_key),
                }
        return result

    @staticmethod
    def _aggregate_operations(
        entries: List[Dict[str, Any]],
        key: str,
    ) -> Dict[str, Any]:
        """Aggregate throughput metrics for a given key across entries."""
        ops_by_op: Dict[str, List[float]] = {}
        lat_by_op: Dict[str, List[float]] = {}
        bps_by_op: Dict[str, List[float]] = {}
        err_by_op: Dict[str, int] = {}

        for entry in entries:
            for op, data in entry.get(key, {}).items():
                ops_by_op.setdefault(op, [])
                lat_by_op.setdefault(op, [])
                bps_by_op.setdefault(op, [])
                err_by_op.setdefault(op, 0)
                if data.get("ops_per_second") is not None:
                    ops_by_op[op].append(data["ops_per_second"])
                if data.get("latency_ms") is not None:
                    lat_by_op[op].append(data["latency_ms"])
                if data.get("bytes_per_second") is not None:
                    bps_by_op[op].append(data["bytes_per_second"])
                if data.get("status") != "ok":
                    err_by_op[op] += 1

        summary = {}
        for op in ops_by_op:
            ops = ops_by_op[op]
            lats = lat_by_op.get(op, [])
            bps = bps_by_op.get(op, [])
            errs = err_by_op.get(op, 0)
            if ops:
                summary[op] = {
                    "meanOpsPerSecond": round(statistics.mean(ops), 2),
                    "medianOpsPerSecond": round(statistics.median(ops), 2),
                    "minOpsPerSecond": round(min(ops), 2),
                    "maxOpsPerSecond": round(max(ops), 2),
                    "meanLatency_ms": round(statistics.mean(lats), 2) if lats else None,
                    "meanBytesPerSecond": round(statistics.mean(bps), 2) if bps else None,
                    "sampleCount": len(ops),
                    "errorCount": errs,
                }
            else:
                summary[op] = {"meanOpsPerSecond": None, "sampleCount": 0, "errorCount": errs}
        return summary
