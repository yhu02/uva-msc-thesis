"""Diagnostics captured when probe verdicts come back ``Unknown``.

When a LitmusChaos probe doesn't report a verdict (eviction, timeout,
scheduling delay, ChaosCenter cleanup), the iteration result records
``Unknown``.  This module captures the surrounding cluster state at
the time of the run so post-hoc analysis can distinguish "probe never
ran" from "probe ran and the SUT was actually broken."
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from kubernetes import client as k8s_client

from chaosprobe.k8s import ensure_k8s_config


def capture_unknown_diagnostics(
    *,
    namespace: str,
    probe_verdicts: Dict[str, str],
    output_data: Dict[str, Any],
    executed: List[Dict[str, Any]],
    experiment_start: float,
    experiment_end: float,
) -> Dict[str, Any]:
    """Capture raw probe state when verdicts are Unknown.

    Used to diagnose *why* certain probes resolve to Unknown — i.e., is
    the CRD's ``probeStatuses`` actually missing those entries, is
    ChaosCenter's executionData a different snapshot, and what
    happened to the probe pods themselves.

    Every read is wrapped in try/except: a diagnostic capture failure
    must never break an experiment run.
    """
    unknown_names = sorted(n for n, v in probe_verdicts.items() if v == "Unknown")

    # 1. Raw CRD probe statuses (already parsed by result_collector but
    #    we kept the .status dict verbatim).
    crd_probe_statuses: Dict[str, Any] = {}
    try:
        for exp in output_data.get("experiments", []):
            for probe in exp.get("probes", []):
                name = probe.get("name", "")
                if name:
                    crd_probe_statuses[name] = {
                        "type": probe.get("type"),
                        "mode": probe.get("mode"),
                        "status": probe.get("status", {}),
                        "phaseVerdicts": probe.get("phaseVerdicts"),
                    }
    except Exception as e:
        crd_probe_statuses = {"_error": f"crd capture failed: {e}"}

    # 2. ChaosCenter's view (raw probe statuses + parsed verdicts) from
    #    executionData — different snapshot moment than the CRD.
    cc_raw: Dict[str, Any] = {}
    cc_verdicts: Dict[str, str] = {}
    try:
        for exp_entry in executed:
            cc_raw.update(exp_entry.get("chaosCenterRawProbeStatuses", {}) or {})
            cc_verdicts.update(exp_entry.get("probeVerdicts", {}) or {})
    except Exception as e:
        cc_raw = {"_error": f"chaoscenter capture failed: {e}"}

    # 3. Probe-pod events from the chaos namespace within the experiment
    #    window. LitmusChaos cmdProbe pods carry the probe name in
    #    metadata labels and the name prefix.
    probe_pod_events: List[Dict[str, Any]] = []
    probe_pod_summary: List[Dict[str, Any]] = []
    try:
        ensure_k8s_config()
        core = k8s_client.CoreV1Api()

        start_dt = datetime.fromtimestamp(experiment_start, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(experiment_end, tz=timezone.utc)

        # Events involving any pod whose name contains a probe name or
        # the marker "probe" / "litmus".  Cheap to over-collect: the
        # diagnostic is rare (only when Unknowns occur).
        events = core.list_namespaced_event(namespace=namespace).items
        for ev in events:
            io = ev.involved_object
            name = (io.name or "") if io else ""
            if not name:
                continue
            lname = name.lower()
            if not any(p in lname for p in ("probe", "litmus")) and not any(
                u in lname for u in unknown_names
            ):
                continue
            ev_ts = ev.last_timestamp or ev.event_time or ev.first_timestamp
            if ev_ts and not (start_dt <= ev_ts <= end_dt):
                continue
            probe_pod_events.append(
                {
                    "time": ev_ts.isoformat() if ev_ts else None,
                    "type": ev.type,
                    "reason": ev.reason,
                    "object": f"{io.kind}/{io.name}" if io else "",
                    "message": ev.message,
                }
            )

        # Snapshot of any surviving probe-related pods at read time.
        pods = core.list_namespaced_pod(namespace=namespace).items
        for pod in pods:
            pname = (pod.metadata.name or "").lower()
            if not any(p in pname for p in ("probe", "litmus")) and not any(
                u in pname for u in unknown_names
            ):
                continue
            container_states: List[Dict[str, Any]] = []
            for cs in pod.status.container_statuses or []:
                state = {}
                if cs.state and cs.state.waiting:
                    state["waiting"] = {
                        "reason": cs.state.waiting.reason,
                        "message": cs.state.waiting.message,
                    }
                if cs.state and cs.state.terminated:
                    state["terminated"] = {
                        "reason": cs.state.terminated.reason,
                        "exitCode": cs.state.terminated.exit_code,
                    }
                container_states.append(
                    {
                        "name": cs.name,
                        "restartCount": cs.restart_count,
                        "state": state,
                    }
                )
            probe_pod_summary.append(
                {
                    "name": pod.metadata.name,
                    "node": pod.spec.node_name,
                    "phase": pod.status.phase,
                    "podIP": pod.status.pod_ip,
                    "startTime": (
                        pod.status.start_time.isoformat() if pod.status.start_time else None
                    ),
                    "containerStatuses": container_states,
                }
            )
    except Exception as e:
        probe_pod_events = [{"_error": f"events capture failed: {e}"}]

    return {
        "unknownProbes": unknown_names,
        "experimentWindow": {
            "start": experiment_start,
            "end": experiment_end,
            "duration_s": round(experiment_end - experiment_start, 1),
        },
        "crdProbeStatuses": crd_probe_statuses,
        "chaosCenterProbeStatuses": cc_raw,
        "chaosCenterVerdicts": cc_verdicts,
        "probePodEvents": probe_pod_events,
        "probePodSnapshot": probe_pod_summary,
    }
