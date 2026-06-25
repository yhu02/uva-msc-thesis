"""Continuous EndpointSlice time-series sampler — the H3 availability instrument.

DESIGN §4 registers a "EndpointSlice trough sampler (15s cadence)" as the
H3 availability-face instrument, measuring the outage trough's **depth and
duration**.  The pre-existing instrumentation (``MetricsCollector.
snapshot_endpoint_slices`` + the during-chaos min-snapshot loop in
``strategy_runner``) captures only the *worst* (fewest-ready) snapshot, so
trough *depth* is observable but *duration* is not — it was proxied by mean
pod recovery time.  The M2 report flagged this as an instrumentation gap to
close before the C2 / node-drain campaign.

This module adds a proper continuous sampler that retains the **full**
EndpointSlice time series across the pre -> during -> post chaos window at a
15 s cadence, mirroring the conntrack first-class sampler
(:mod:`chaosprobe.metrics.conntrack`):

- :class:`EndpointSliceTimeSeriesProber` is a
  :class:`~chaosprobe.metrics.base.ContinuousProberBase`, so
  ``orchestrator/probers.py`` manages it identically to the other continuous
  probers (``start`` / ``mark_chaos_start`` / ``mark_chaos_end`` / ``stop`` /
  ``result``).
- Each tick lists the namespace's EndpointSlices (the same raw-JSON read the
  snapshot path uses — ``_preload_content=False`` so an ``endpoints: null``
  emptied slice during a node-drain summarizes to ``total = 0`` instead of
  raising), summarizes per service via
  :func:`~chaosprobe.metrics.endpointslices.summarize_endpoint_slices_json`,
  and appends one record ``{"ts", "phase", "services"}``.
- It runs pre -> during -> post (not during-only) so the recovery tail — the
  raw material for trough *duration* — is captured.
- Every cluster-facing step degrades gracefully exactly like conntrack: an
  API error increments ``probeErrors`` and skips the tick, and ``available``
  reflects whether any sample succeeded so "not collected" never reads as a
  zero-depth, zero-duration trough.

The result lands in ``summary.json`` as the additive top-level metrics key
``endpointSliceTimeSeries = {"samples": [...], "meta": {...}}`` (parallel to
``conntrackProtocolSamples`` / ``conntrackProtocolMeta``).  The existing
pre/during/post snapshots under ``metrics.endpointSlices`` are left untouched
— ``blast_radius.py`` and the frozen A/A data depend on them; the time series
is purely additive.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from kubernetes import client
from kubernetes.client.rest import ApiException

from chaosprobe.k8s import ensure_k8s_config
from chaosprobe.metrics.base import ContinuousProberBase
from chaosprobe.metrics.endpointslices import summarize_endpoint_slices_json

logger = logging.getLogger(__name__)

#: Default sampling cadence (DESIGN §4: "EndpointSlice trough sampler (15s
#: cadence)").  Matches ``strategy_runner._DURING_SAMPLE_INTERVAL_S`` so the
#: time series and the legacy min-snapshot loop sample at the same rate.
DEFAULT_INTERVAL_S = 15.0


class EndpointSliceTimeSeriesProber(ContinuousProberBase):
    """Samples the namespace's EndpointSlices on a fixed cadence through chaos.

    Unlike the legacy during-chaos sampler (which discards every snapshot but
    the single worst one), this retains the full time series across the
    pre / during / post phases, so the outage trough's *duration* — not just
    its depth — is recoverable downstream.

    Lifecycle matches every other continuous prober, so
    ``orchestrator/probers.py`` drives it identically::

        prober = EndpointSliceTimeSeriesProber("online-boutique")
        prober.start()
        prober.mark_chaos_start()
        # ... chaos ...
        prober.mark_chaos_end()
        prober.stop()
        data = prober.result()   # {"samples": [...], "meta": {...}}

    The Kubernetes discovery API is injectable (*discovery_api*) so tests can
    feed canned EndpointSlice JSON without a cluster, mirroring the conntrack
    prober's ``exec_fn`` seam.
    """

    def __init__(
        self,
        namespace: str,
        interval: float = DEFAULT_INTERVAL_S,
        discovery_api: Optional[Any] = None,
    ):
        super().__init__(namespace, interval, name="endpointslice-timeseries")
        if discovery_api is None:
            ensure_k8s_config()
            discovery_api = client.DiscoveryV1Api()
        self.discovery_api = discovery_api
        self._samples: List[Dict[str, Any]] = []
        # True once any tick has successfully listed + summarized slices.
        self._sampled_ok: bool = False

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    def _probe_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._sample_once()
            except Exception as exc:  # defensive: the loop must never die
                logger.warning("EndpointSlice probe tick failed: %s", exc)
                with self._lock:
                    self._probe_errors += 1
            self._stop_event.wait(timeout=self.interval)

    def _list_endpoint_slices(self) -> List[Dict[str, Any]]:
        """List the namespace's EndpointSlices as raw JSON items.

        Reads raw JSON (``_preload_content=False``) for the same reason the
        snapshot path does: the typed ``V1EndpointSlice`` model rejects
        ``endpoints: null`` (returned for a slice emptied mid node-drain),
        which would raise ``ValueError`` and break the tick.
        ``json.JSONDecodeError`` is a ``ValueError`` subclass.  Raises
        ``ApiException`` / ``ValueError`` on an API / decode error, which the
        caller catches and counts as a probe error.
        """
        resp = self.discovery_api.list_namespaced_endpoint_slice(
            self.namespace, _preload_content=False
        )
        raw = json.loads(resp.data)
        items = raw.get("items")
        return items if isinstance(items, list) else []

    def _sample_once(self) -> None:
        """Sample the namespace's EndpointSlices once and append one record.

        On an API / decode failure the tick is counted as a probe error and
        nothing is appended (graceful degradation, mirroring conntrack).
        """
        now = time.time()
        phase = self._current_phase(now)
        ts = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        try:
            items = self._list_endpoint_slices()
        except (ApiException, ValueError) as exc:
            logger.warning("EndpointSlice probe list failed: %s", exc)
            with self._lock:
                self._probe_errors += 1
            return
        summary = summarize_endpoint_slices_json(items or [])
        with self._lock:
            self._sampled_ok = True
            self._samples.append(
                {
                    "ts": ts,
                    "phase": phase,
                    "services": summary.get("services") or {},
                }
            )

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    def result(self) -> Dict[str, Any]:
        """Return the collected time series + metadata.

        ``samples`` / ``meta`` land in ``summary.json`` as
        ``endpointSliceTimeSeries`` (see ``MetricsCollector.collect``).
        ``meta.available`` distinguishes "no endpoint churn observed" from
        "not collected" so an absent signal never reads as a zero-depth,
        zero-duration trough.
        """
        with self._lock:
            samples = list(self._samples)
            errors = self._probe_errors
            sampled_ok = self._sampled_ok
        meta: Dict[str, Any] = {
            "available": sampled_ok,
            "intervalSeconds": self.interval,
            "namespace": self.namespace,
            "sampleCount": len(samples),
        }
        if not sampled_ok:
            # The only "unavailable" path is "no tick ever succeeded" — unlike
            # conntrack, this prober has no pre-flight setup (sampler pods,
            # version pin) that could fail before the loop starts.
            meta["reason"] = "no EndpointSlice sample succeeded"
        if errors > 0:
            meta["probeErrors"] = errors
        return {"samples": samples, "meta": meta}
