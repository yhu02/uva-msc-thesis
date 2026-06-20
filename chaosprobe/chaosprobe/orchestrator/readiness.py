"""Readiness, warmup, and target-pod gates for a single chaos iteration.

These gates run between strategies and between iterations to ensure
the cluster is in a consistent "hot" state when chaos starts.  K8s
reporting pods as Ready is necessary but not sufficient: pods can
report Ready while their connection pools are broken, gRPC channels
are in TRANSIENT_FAILURE, or service endpoints haven't propagated.
The functions here verify end-to-end readiness via real HTTP checks
from a probe pod.
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional, Tuple

import click
from kubernetes import client as k8s_client


def shell_escape(s: str) -> str:
    """Escape a string for safe substitution **inside `'...'`** in sh.

    Only escapes single quotes — the result is only safe when wrapped
    in single quotes at the call site (e.g. ``f"echo '{shell_escape(x)}'"``).
    Do not use the result outside single quotes; other shell metacharacters
    (``$``, ``` ` ```, ``\\``) are NOT escaped and would be interpreted by
    the shell.
    """
    return s.replace("'", "'\\''")


def wait_for_target_pod(
    namespace: str,
    deployment_name: str,
    timeout: int = 60,
    stable_secs: int = 10,
) -> None:
    """Wait until the target deployment has a pod that stays Running.

    After finding a Running pod, keeps checking for *stable_secs* to
    confirm the pod doesn't crash under resource pressure (common with
    colocate where all deployments compete on one node).

    Raises ``click.ClickException`` if no stable pod is found within
    *timeout* seconds.
    """
    core = k8s_client.CoreV1Api()
    deadline = time.time() + timeout
    stable_since: Optional[float] = None

    while time.time() < deadline:
        pods = core.list_namespaced_pod(
            namespace,
            label_selector=f"app={deployment_name}",
        )
        running = [
            p
            for p in pods.items
            if p.status
            and p.status.phase == "Running"
            and all(cs.ready for cs in (p.status.container_statuses or []))
        ]
        if running:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= stable_secs:
                return  # Pod has been Running for stable_secs
        else:
            stable_since = None  # Pod disappeared — reset
        time.sleep(3)
    raise click.ClickException(
        f"Target deployment '{deployment_name}' has no ready pods in "
        f"'{namespace}' after {timeout}s. The placement strategy may "
        f"have moved pods to a node that cannot schedule them."
    )


def warmup_application(
    core,
    namespace: str,
    pod: str,
    urls_to_check: List[Tuple[str, str]],
    *,
    duration_s: int = 10,
) -> None:
    """Pump concurrent HTTP load on every probed route for ``duration_s``.

    Runs as a single sh-loop inside the probe pod that fires ``wget``
    requests against each URL in a tight loop, in parallel via ``&``.
    Not a benchmark — the requests' outputs are discarded.  The goal is
    to force every route's downstream chain (frontend → recommendation
    → currency, etc.) to warm its connection pools and JIT caches so
    the cluster is in a consistent "hot" state when chaos starts.

    Reduces iteration-to-iteration variance from cold-state effects
    that the previous readiness gate didn't address: the gate verified
    HTTP-200 reachability under low load, but a probe-pod kubectl exec
    issuing one request per route is nowhere near the load profile
    chaos + Locust will apply, so warmup-sensitive failure modes only
    surfaced under chaos and showed up as bimodal scores.
    """
    from chaosprobe.metrics.base import exec_in_pod

    # Build a parallel wget loop per route, all running for duration_s.
    # `wget -q -O /dev/null -T 2` makes each request bounded and silent.
    # `while true; do ... done` loops continuously; killed by `timeout`.
    routes_block = " ".join(
        f"(while true; do wget -q -O /dev/null -T 2 '{shell_escape(url)}' || true; done) &"
        for _path, url in urls_to_check
    )
    cmd = (
        f"set +e; "
        f"{routes_block} "
        f"sleep {duration_s}; "
        # Kill background loops so the exec returns promptly.
        f"kill $(jobs -p) 2>/dev/null; "
        f"wait 2>/dev/null; "
        f"echo done"
    )
    try:
        exec_in_pod(core, namespace, pod, ["sh", "-c", cmd])
    except Exception as exc:
        click.echo(f"    Warning: warmup phase failed (continuing anyway): {exc}", err=True)


def wait_for_app_ready(
    namespace: str,
    target_deployment: str,
    timeout: int = 180,
    http_routes: Optional[List[tuple]] = None,
    service_routes: Optional[List[tuple]] = None,
    required_consecutive: int = 5,
    sustained_period_s: int = 15,
    latency_budget_ms: int = 5000,
    gate_east_west: bool = False,
    pre_gate_warmup_s: int = 0,
    sustained_gate_load: bool = False,
) -> bool:
    """Wait until all probed routes respond successfully and stay stable.

    Two-phase functional gate:

    1. **Consecutive-OK phase**: every probed route must respond
       successfully within ``latency_budget_ms`` for ``required_consecutive``
       checks in a row.  This filters transient failures.

    2. **Sustained-clean phase**: after the consecutive gate passes,
       keep sampling for ``sustained_period_s`` seconds.  Any single
       failure or over-budget response during this window resets back
       to the consecutive-OK phase.  This catches "marginal recovery"
       where the cluster responds OK but is fragile — the failure
       mode observed when iterations score wildly differently with
       identical placements.  See the procedural-variance analysis
       in results/20260518-131302 for evidence: BAD iterations passed
       a 3-consecutive-OK gate with 0% subsequent prober errors yet
       still produced bimodal scores under chaos.

    K8s readiness probes may pass while the application isn't fully
    serving traffic (e.g. during connection pool warm-up).  This does
    actual probes from a probe pod to confirm end-to-end readiness of the
    **user-facing north-south ``http_routes``** (via HTTP) — whether the app
    can serve user traffic is the "ready for chaos" signal.

    East-west ``service_routes`` (``(source, target, host:port, protocol,
    desc)`` tuples) gate ONLY when ``gate_east_west`` is set: they are
    TCP-connect-probed to the real ``host:port`` (the correct probe for
    gRPC/TCP backends with no HTTP endpoint, when the pod has python3). By
    default they do NOT gate — they are covered by K8s deployment readiness
    and still measured by the latency prober — because gating on every
    internal edge makes the gate flap (and false-positive-taint iterations)
    on workloads with many east-west routes during post-restart churn.

    Returns ``True`` when the gate passed (or was skipped because no probe
    pod was available), and ``False`` when it timed out — the caller treats a
    timeout as a pre-chaos taint reason (``app_ready_timeout``) so the
    iteration is excluded from the healthy-only statistics.
    """
    from chaosprobe.metrics.base import exec_in_pod, find_probe_pod

    core = k8s_client.CoreV1Api()
    pod = find_probe_pod(
        core,
        namespace,
        require_python3=False,
        exclude_prefixes=[target_deployment],
    )
    if not pod:
        click.echo("    Warning: no probe pod for app-ready check, skipping")
        # No probe pod to assess readiness — this is a skip, not a timeout, so
        # don't taint the iteration on it (preserves prior behaviour).
        return True

    # Build the list of URLs to check: all probed routes + healthz fallback
    urls_to_check = []
    if http_routes:
        seen = set()
        for service, path, _desc, _method in http_routes:
            url = f"http://{service}.{namespace}.svc.cluster.local{path}"
            if url not in seen:
                urls_to_check.append((path, url))
                seen.add(url)
    if not urls_to_check:
        urls_to_check = [("/_healthz", f"http://frontend.{namespace}.svc.cluster.local/_healthz")]

    # East-west gRPC/TCP targets (host:port), deduplicated, gated via a python3
    # socket connect when the probe pod supports it — ONLY when gate_east_west is
    # set. By default the readiness gate is the user-facing north-south routes:
    # whether the app can serve user traffic is the right "ready for chaos" signal,
    # and the east-west service edges are already covered by K8s deployment
    # readiness and are still MEASURED by the latency prober. Gating on all
    # east-west edges too made the gate flap on workloads with many internal edges
    # (hotelReservation's 11 routes during the post-restart re-registration window),
    # false-positive-tainting every iteration even though all routes served fine.
    tcp_targets: List[Tuple[str, str]] = []
    if service_routes and gate_east_west:
        seen_hosts = set()
        for _src, _tgt, host, _proto, label in service_routes:
            if host and host not in seen_hosts:
                tcp_targets.append((label, host))
                seen_hosts.add(host)

    # Tri-state cache: None = not yet probed, False = probe pod lacks
    # python3 (skip all TCP checks), True = python3 available.
    python3_supported: Optional[bool] = None

    def _tcp_connect_ok(host: str, budget_s: int) -> Optional[bool]:
        """TCP-connect to ``host`` (``host:port``) from the probe pod.

        Returns True on a successful connect, False on refusal/timeout,
        and None when python3 is unavailable in the pod (caller skips the
        TCP gate).
        """
        nonlocal pod
        if ":" in host:
            hostname, port = host.rsplit(":", 1)
        else:
            hostname, port = host, "80"
        py_script = (
            "import socket,sys\n"
            "try:\n"
            " s=socket.socket();s.settimeout(int(sys.argv[2]));"
            "s.connect((sys.argv[1],int(sys.argv[3])));s.close();print('OK')\n"
            "except Exception as ex:\n"
            " print('FAIL',str(ex)[:80])"
        )
        assert pod is not None
        out = exec_in_pod(
            core,
            namespace,
            pod,
            ["python3", "-c", py_script, hostname, str(budget_s), port],
        )
        low = out.lower()
        if (
            not out.strip()
            or "no such file" in low
            or "not found" in low
            or "executable file" in low
        ):
            return None
        return "OK" in out

    def _check_all_routes() -> bool:
        """Return True iff every route responds successfully within the budget.

        HTTP routes use ``wget``'s exit code as the success signal —
        busybox wget exits non-zero on connect failure, timeout, and
        4xx/5xx responses, so this is sufficient for "did the URL respond
        OK".  East-west service routes use a TCP connect to the real
        ``host:port`` (gRPC/TCP backends serve no HTTP), skipped when the
        probe pod has no python3.

        Previous attempt (``wget -q -S ... | grep ' 200'``) silently
        broke in this environment: busybox wget's ``-q`` flag suppresses
        the ``-S`` server-response output, so the grep never matched
        and the gate reported "0/5 consecutive OK in 240s" while the
        cluster was actually responding fine.  See the
        results/20260520-163953 trace where every iteration printed the
        timeout warning yet still scored normally.
        """
        nonlocal pod, python3_supported
        budget_s = max(1, latency_budget_ms // 1000 + 1)
        for _path, url in urls_to_check:
            cmd = (
                f"wget -q -O /dev/null --timeout={budget_s} '{shell_escape(url)}' "
                f"&& echo OK || echo FAIL"
            )
            # pod is guarded non-None above and only ever reassigned to a
            # non-None replacement; assert so the closure type-checks.
            assert pod is not None
            out = exec_in_pod(core, namespace, pod, ["sh", "-c", cmd])
            if "OK" not in out:
                # Pod may have been evicted — try to re-discover
                if "ERROR:" in out:
                    new_pod = find_probe_pod(
                        core,
                        namespace,
                        require_python3=False,
                        exclude_prefixes=[target_deployment],
                    )
                    if new_pod and new_pod != pod:
                        pod = new_pod
                return False

        # East-west gRPC/TCP gate, only while python3 is available.
        if tcp_targets and python3_supported is not False:
            for _label, host in tcp_targets:
                result = _tcp_connect_ok(host, budget_s)
                if result is None:
                    # Probe pod lacks python3 — stop trying TCP checks and
                    # rely on K8s-native gRPC readiness for these backends.
                    python3_supported = False
                    break
                python3_supported = True
                if not result:
                    return False
        return True

    # Pre-gate warm-up (opt-in, default off): some workloads only become
    # reachable under SUSTAINED traffic.  hotelReservation's gRPC clients
    # enter a `too_many_pings`/GoAway keepalive reconnection storm in the
    # no-traffic window after a restart; it settles only once traffic keeps
    # the gRPC streams active.  The post-gate warmup below cannot help -- it
    # runs only after the consecutive-OK gate passes, which the storm
    # prevents (the gate's intermittent single probes never sustain traffic).
    # When `pre_gate_warmup_s` > 0, pump sustained load on the probed routes
    # BEFORE the gate so such workloads settle and the gate can then pass
    # cleanly, turning a spurious `app_ready_timeout` taint into a real ready
    # signal.  Fast-recovering workloads (Online Boutique) leave it at 0.
    # Runs before the deadline is set, so it never eats the gate's budget.
    if pre_gate_warmup_s > 0 and urls_to_check:
        click.echo(
            f"    Pre-gate warm-up: pumping sustained load on "
            f"{len(urls_to_check)} route(s) for {pre_gate_warmup_s}s to "
            f"settle traffic-dependent recovery..."
        )
        warmup_application(core, namespace, pod, urls_to_check, duration_s=pre_gate_warmup_s)

    # Sustained-during-gate load: keep the probed routes (and the gRPC streams
    # they drive) hot THROUGHOUT the gate, not just before it.  A one-shot
    # pre-gate warm-up is undone by the gate's own intermittent probing: between
    # the 3s single-route probes the gRPC streams go idle, the too_many_pings
    # keepalive storm re-triggers, /hotels flaps, and the consecutive-OK count
    # never builds.  A daemon thread pumps short warm-up bursts until the gate
    # exits, so the gate's probes land on a continuously-exercised app.  Off by
    # default (Online Boutique does not need it).
    _gate_stop = threading.Event()
    _gate_loader: Optional[threading.Thread] = None
    if sustained_gate_load and urls_to_check:

        def _pump_gate_load() -> None:
            while not _gate_stop.is_set():
                assert pod is not None  # narrowed in the main body; never re-None'd
                warmup_application(core, namespace, pod, urls_to_check, duration_s=6)

        _gate_loader = threading.Thread(
            target=_pump_gate_load, name="gate-sustained-load", daemon=True
        )
        _gate_loader.start()
        click.echo("    Sustained-gate load: keeping routes hot during the readiness gate...")

    try:
        consecutive_ok = 0
        deadline = time.time() + timeout
        attempt = 0
        sustained_until: Optional[float] = None  # absolute time

        while time.time() < deadline:
            all_ok = _check_all_routes()

            if all_ok:
                if sustained_until is None:
                    consecutive_ok += 1
                    if consecutive_ok >= required_consecutive:
                        sustained_until = time.time() + sustained_period_s
                        click.echo(
                            f"    App reachable ({consecutive_ok} consecutive OK); "
                            f"verifying stability for {sustained_period_s}s..."
                        )
                else:
                    # In sustained-clean phase — wait until the period elapses.
                    if time.time() >= sustained_until:
                        # Warmup phase: pump concurrent load on every probed
                        # route for ~20s to warm gRPC connection pools, JVM
                        # JIT caches, and CoreDNS resolution before the
                        # iteration's pre-chaos baseline starts.  Without
                        # this, frontend's outbound connection pool to
                        # productcatalog is at cold-start size when chaos
                        # hits, which sometimes triggers a thundering-herd
                        # retry cascade and produces a 33-mode score on an
                        # iteration that would otherwise score 75 with the
                        # same placement.
                        #
                        # Increased from 10s→20s: under colocate (11 services
                        # on 1 node), 10s was insufficient to warm all pools
                        # because CPU contention slows connection establishment.
                        # 20s ensures ≥5 full request cycles per route at the
                        # typical 3-4s response time under colocate pressure.
                        warmup_application(
                            core,
                            namespace,
                            pod,
                            urls_to_check,
                            duration_s=20,
                        )

                        # Post-warmup latency convergence check: verify that
                        # response times have stabilised below a threshold.
                        # Without this, strategies that place services across
                        # nodes (adversarial, spread) can start chaos with
                        # elevated baseline latency because gRPC connection
                        # pools inside Go/Java services warm slowly even after
                        # wget has warmed the Service VIP path.  The fix:
                        # take 3 quick latency samples; if any route exceeds
                        # the convergence threshold, run another 10s warmup
                        # burst and re-check (up to 2 extra rounds).
                        convergence_threshold_ms = 300
                        for _warmup_round in range(2):
                            converged = True
                            for _path, url in urls_to_check:
                                # Time a single request to check latency
                                safe_url = shell_escape(url)
                                cmd = (
                                    f"S=$(date +%s%N 2>/dev/null); "
                                    f"wget -q -O /dev/null --timeout=5 '{safe_url}'; "
                                    f"E=$(date +%s%N 2>/dev/null); "
                                    f"echo $(( (E - S) / 1000000 ))"
                                )
                                out = exec_in_pod(core, namespace, pod, ["sh", "-c", cmd])
                                try:
                                    lat_ms = int(out.strip())
                                    if lat_ms > convergence_threshold_ms:
                                        converged = False
                                        break
                                except (ValueError, TypeError):
                                    converged = False
                                    break
                            if converged:
                                break
                            # Not converged — run another warmup burst
                            click.echo(
                                f"    Latency not converged (>{convergence_threshold_ms}ms), "
                                f"extending warmup..."
                            )
                            warmup_application(
                                core,
                                namespace,
                                pod,
                                urls_to_check,
                                duration_s=10,
                            )

                        click.echo(
                            f"    App ready after {attempt} checks "
                            f"({len(urls_to_check)} routes, "
                            f"{required_consecutive} consecutive + "
                            f"{sustained_period_s}s sustained + warmup)"
                        )
                        return True
            else:
                if sustained_until is not None:
                    click.echo("    App stability check failed — restarting consecutive-OK count")
                    # The probe pod may have been evicted during the sustained
                    # check (e.g. rollout of another service completed and K8s
                    # reclaimed resources).  Re-discover to avoid exec'ing into
                    # a dead pod for the remaining timeout.
                    new_pod = find_probe_pod(
                        core,
                        namespace,
                        require_python3=False,
                        exclude_prefixes=[target_deployment],
                    )
                    if new_pod and new_pod != pod:
                        pod = new_pod
                consecutive_ok = 0
                sustained_until = None

            attempt += 1
            time.sleep(3)

        if sustained_until is not None:
            click.echo("    Warning: app-ready timed out in sustained phase — proceeding anyway")
        else:
            click.echo(
                f"    Warning: app-ready check timed out after {timeout}s — "
                f"only {consecutive_ok}/{required_consecutive} consecutive OK. "
                f"Iteration may start with degraded system."
            )
        # Gate timed out: signal the caller so it can taint the iteration.
        return False
    finally:
        if _gate_loader is not None:
            _gate_stop.set()
            _gate_loader.join(timeout=10)
