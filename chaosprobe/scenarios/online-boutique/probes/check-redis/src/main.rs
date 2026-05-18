//! ChaosProbe resilience probe: check-redis
//!
//! Verifies that Redis is reachable and responding to PING commands.
//! Used as a LitmusChaos cmdProbe to test redis-cart health during
//! chaos experiments in the online-boutique scenario.
//!
//! Always exits 0 with a comparator-parseable line.  Errors map to a
//! `REDIS_FAIL ...` string so the LitmusChaos cmdProbe comparator can
//! record a clean Fail verdict instead of dropping the tick into the
//! retry/timeout path (which can trigger the post-chaos abort cascade
//! described in litmus-go pkg/probe/probe.go).
//!
//! Environment variables:
//!   PROBE_REDIS_ADDR — Redis address (default: redis-cart.online-boutique.svc.cluster.local:6379)
//!   PROBE_TIMEOUT_MS — overall I/O timeout in ms (default 2000)

use std::env;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

fn main() {
    print!("{}", run_check());
}

fn run_check() -> String {
    let addr = env::var("PROBE_REDIS_ADDR")
        .unwrap_or_else(|_| "redis-cart.online-boutique.svc.cluster.local:6379".to_string());
    let timeout_ms: u64 = env::var("PROBE_TIMEOUT_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(2000);
    let timeout = Duration::from_millis(timeout_ms);

    let sock_addr = match bounded_resolve(&addr, timeout) {
        Ok(s) => s,
        Err(e) => return format!("REDIS_FAIL {}", e),
    };

    let mut stream = match TcpStream::connect_timeout(&sock_addr, timeout) {
        Ok(s) => s,
        Err(e) => return format!("REDIS_FAIL connect: {}", e),
    };
    if let Err(e) = stream.set_read_timeout(Some(timeout)) {
        return format!("REDIS_FAIL set_timeout: {}", e);
    }
    if let Err(e) = stream.write_all(b"PING\r\n") {
        return format!("REDIS_FAIL write: {}", e);
    }

    let mut buf = [0u8; 64];
    let n = match stream.read(&mut buf) {
        Ok(n) => n,
        Err(e) => return format!("REDIS_FAIL read: {}", e),
    };

    let trimmed = String::from_utf8_lossy(&buf[..n]).trim().to_string();
    if trimmed == "+PONG" {
        "REDIS_OK".to_string()
    } else {
        format!("REDIS_FAIL: unexpected response '{}'", trimmed)
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
