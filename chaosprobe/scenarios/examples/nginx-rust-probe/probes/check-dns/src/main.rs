//! ChaosProbe resilience probe: check-dns
//!
//! Verifies that Kubernetes DNS resolution is working by looking up
//! a service name. This is a full Cargo project probe.

use std::net::ToSocketAddrs;
use std::process;

fn main() {
    match run_check() {
        Ok(output) => print!("{}", output),
        Err(e) => {
            eprintln!("probe check-dns error: {}", e);
            process::exit(1);
        }
    }
}

fn run_check() -> Result<String, String> {
    let service = std::env::var("PROBE_SERVICE")
        .unwrap_or_else(|_| "nginx-service.default.svc.cluster.local:80".to_string());

    match service.to_socket_addrs() {
        Ok(addrs) => {
            let resolved: Vec<String> = addrs.map(|a| a.to_string()).collect();
            if resolved.is_empty() {
                Ok("DNS_EMPTY".to_string())
            } else {
                Ok(format!("DNS_OK: {}", resolved.join(", ")))
            }
        }
        Err(e) => Ok(format!("DNS_FAIL: {}", e)),
    }
}
