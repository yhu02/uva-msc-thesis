//! ChaosProbe resilience probe: check-redis
//!
//! Verifies that Redis is reachable and responding to PING commands.
//! Used as a LitmusChaos cmdProbe to test redis-cart health during
//! chaos experiments in the online-boutique scenario.
//!
//! Environment variables:
//!   PROBE_REDIS_ADDR — Redis address (default: redis-cart.online-boutique.svc.cluster.local:6379)

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::process;
use std::time::Duration;

fn main() {
    match run_check() {
        Ok(output) => print!("{}", output),
        Err(e) => {
            eprintln!("probe check-redis error: {}", e);
            process::exit(1);
        }
    }
}

fn run_check() -> Result<String, String> {
    let addr = std::env::var("PROBE_REDIS_ADDR")
        .unwrap_or_else(|_| "redis-cart.online-boutique.svc.cluster.local:6379".to_string());

    // Resolve hostname to socket address (supports DNS names)
    let sock_addr = addr
        .to_socket_addrs()
        .map_err(|e| format!("DNS resolve '{}': {}", addr, e))?
        .next()
        .ok_or_else(|| format!("DNS resolve '{}': no addresses", addr))?;

    // Connect with timeout
    let mut stream = TcpStream::connect_timeout(&sock_addr, Duration::from_secs(5))
        .map_err(|e| format!("connect failed: {}", e))?;

    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .map_err(|e| format!("set timeout: {}", e))?;

    // Send Redis inline PING command
    stream
        .write_all(b"PING\r\n")
        .map_err(|e| format!("write failed: {}", e))?;

    // Read response
    let mut buf = [0u8; 64];
    let n = stream
        .read(&mut buf)
        .map_err(|e| format!("read failed: {}", e))?;

    let response = String::from_utf8_lossy(&buf[..n]);
    let trimmed = response.trim();

    // Redis responds with "+PONG" for inline PING
    if trimmed == "+PONG" {
        Ok("REDIS_OK".to_string())
    } else {
        Ok(format!("REDIS_FAIL: unexpected response '{}'", trimmed))
    }
}
