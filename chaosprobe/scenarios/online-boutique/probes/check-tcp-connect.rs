//! ChaosProbe resilience probe: check-tcp-connect
//!
//! Measures TCP three-way handshake latency to a target host:port.
//! Distinguishes pure connection-setup degradation (kube-proxy, conntrack
//! saturation, kernel pressure) from application-layer slowness that the
//! HTTP probes capture. Useful as an Edge-mode probe to take a single
//! pre/post-chaos sample.
//!
//! Environment:
//!   PROBE_TARGET            — host:port (default frontend in online-boutique)
//!   PROBE_CONNECT_MS_MAX    — fail threshold in ms (default 500)
//!   PROBE_TIMEOUT_MS        — connect timeout in ms (default 5000)
//!
//! Output:
//!   TCP_OK <ms>             connected within threshold
//!   TCP_SLOW <ms>           connected but over threshold
//!   TCP_FAIL <error>        DNS or connect failed

use std::env;
use std::net::{TcpStream, ToSocketAddrs};
use std::process;
use std::time::{Duration, Instant};

fn main() {
    match run_check() {
        Ok(output) => print!("{}", output),
        Err(e) => {
            eprintln!("probe check-tcp-connect error: {}", e);
            process::exit(1);
        }
    }
}

fn run_check() -> Result<String, String> {
    let target = env::var("PROBE_TARGET")
        .unwrap_or_else(|_| "frontend.online-boutique.svc.cluster.local:80".to_string());
    let max_ms: u128 = env::var("PROBE_CONNECT_MS_MAX")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(500);
    let timeout_ms: u64 = env::var("PROBE_TIMEOUT_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(5000);

    let sock_addr = match target.to_socket_addrs() {
        Ok(mut addrs) => match addrs.next() {
            Some(a) => a,
            None => return Ok("TCP_FAIL no_addresses".to_string()),
        },
        Err(e) => return Ok(format!("TCP_FAIL dns: {}", e)),
    };

    let started = Instant::now();
    match TcpStream::connect_timeout(&sock_addr, Duration::from_millis(timeout_ms)) {
        Ok(_) => {
            let elapsed_ms = started.elapsed().as_millis();
            if elapsed_ms > max_ms {
                Ok(format!("TCP_SLOW {} (max={})", elapsed_ms, max_ms))
            } else {
                Ok(format!("TCP_OK {}", elapsed_ms))
            }
        }
        Err(e) => Ok(format!("TCP_FAIL connect: {}", e)),
    }
}
