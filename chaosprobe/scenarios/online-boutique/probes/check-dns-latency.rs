//! ChaosProbe resilience probe: check-dns-latency
//!
//! Times Kubernetes DNS resolution. CoreDNS lives on the control plane
//! and is sensitive to node-level resource pressure; resolve latency
//! climbs under colocate/adversarial placements where workers are
//! starved. This probe captures that signal independently of the
//! application-layer HTTP probes.
//!
//! Environment:
//!   PROBE_HOST          — host:port to resolve (default frontend in online-boutique)
//!   PROBE_DNS_MS_MAX    — fail threshold in ms (default 250)
//!
//! Output:
//!   DNS_OK <ms> <addr>          resolved within threshold
//!   DNS_SLOW <ms> <addr>        resolved but over threshold
//!   DNS_FAIL <error>            resolve failed entirely

use std::env;
use std::net::ToSocketAddrs;
use std::process;
use std::time::Instant;

fn main() {
    match run_check() {
        Ok(output) => print!("{}", output),
        Err(e) => {
            eprintln!("probe check-dns-latency error: {}", e);
            process::exit(1);
        }
    }
}

fn run_check() -> Result<String, String> {
    let host = env::var("PROBE_HOST")
        .unwrap_or_else(|_| "frontend.online-boutique.svc.cluster.local:80".to_string());
    let max_ms: u128 = env::var("PROBE_DNS_MS_MAX")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(250);

    let started = Instant::now();
    let result = host.to_socket_addrs();
    let elapsed_ms = started.elapsed().as_millis();

    match result {
        Ok(mut addrs) => {
            let first = match addrs.next() {
                Some(a) => a.to_string(),
                None => return Ok("DNS_FAIL no_addresses".to_string()),
            };
            if elapsed_ms > max_ms {
                Ok(format!("DNS_SLOW {} {} (max={})", elapsed_ms, first, max_ms))
            } else {
                Ok(format!("DNS_OK {} {}", elapsed_ms, first))
            }
        }
        Err(e) => Ok(format!("DNS_FAIL {}", e)),
    }
}
