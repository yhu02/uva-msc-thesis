//! ChaosProbe resilience probe: check-tcp-connect
//!
//! Measures TCP three-way handshake latency to a target host:port.
//! Distinguishes pure connection-setup degradation (kube-proxy, conntrack
//! saturation, kernel pressure) from application-layer slowness that the
//! HTTP probes capture. Useful as an Edge-mode probe to take a single
//! pre/post-chaos sample.
//!
//! Always exits 0 with a comparator-parseable line.  Errors map to a
//! `TCP_FAIL ...` string so LitmusChaos records a clean Fail verdict
//! instead of dropping the tick into the retry/timeout path.
//!
//! Environment:
//!   PROBE_TARGET            — host:port (default frontend in online-boutique)
//!   PROBE_CONNECT_MS_MAX    — fail threshold in ms (default 500)
//!   PROBE_TIMEOUT_MS        — connect + DNS bound in ms (default 2000)
//!
//! Output:
//!   TCP_OK <ms>             connected within threshold
//!   TCP_SLOW <ms>           connected but over threshold
//!   TCP_FAIL <error>        DNS or connect failed

use std::env;
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

fn main() {
    print!("{}", run_check());
}

fn run_check() -> String {
    let target = env::var("PROBE_TARGET")
        .unwrap_or_else(|_| "frontend.online-boutique.svc.cluster.local:80".to_string());
    let max_ms: u128 = env::var("PROBE_CONNECT_MS_MAX")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(500);
    let timeout_ms: u64 = env::var("PROBE_TIMEOUT_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(2000);
    let timeout = Duration::from_millis(timeout_ms);

    let sock_addr = match bounded_resolve(&target, timeout) {
        Ok(s) => s,
        Err(e) => return format!("TCP_FAIL {}", e),
    };

    let started = Instant::now();
    match TcpStream::connect_timeout(&sock_addr, timeout) {
        Ok(_) => {
            let elapsed_ms = started.elapsed().as_millis();
            if elapsed_ms > max_ms {
                format!("TCP_SLOW {} (max={})", elapsed_ms, max_ms)
            } else {
                format!("TCP_OK {}", elapsed_ms)
            }
        }
        Err(e) => format!("TCP_FAIL connect: {}", e),
    }
}

fn bounded_resolve(host_port: &str, timeout: Duration) -> Result<SocketAddr, String> {
    let host_port = host_port.to_string();
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let result = host_port
            .to_socket_addrs()
            .map_err(|e| format!("dns: {}", e))
            .and_then(|mut addrs| {
                addrs.next().ok_or_else(|| "dns: no addresses".to_string())
            });
        let _ = tx.send(result);
    });
    match rx.recv_timeout(timeout) {
        Ok(r) => r,
        Err(_) => Err(format!("dns: timeout after {}ms", timeout.as_millis())),
    }
}
