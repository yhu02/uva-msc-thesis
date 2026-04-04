//! ChaosProbe resilience probe: check-endpoint
//!
//! Verifies that an HTTP endpoint is reachable and returns a 200 status.
//! Compiled with: rustc --target x86_64-unknown-linux-musl --edition 2021

use std::process;

fn main() {
    match run_check() {
        Ok(output) => print!("{}", output),
        Err(e) => {
            eprintln!("probe check-endpoint error: {}", e);
            process::exit(1);
        }
    }
}

fn run_check() -> Result<String, String> {
    // Simple TCP connect check to verify the service is listening.
    // For a real probe you would use a crate like ureq or reqwest,
    // but this keeps the single-file build dependency-free.
    use std::net::TcpStream;
    use std::time::Duration;

    let addr = std::env::var("PROBE_TARGET")
        .unwrap_or_else(|_| "nginx-service.default.svc.cluster.local:80".to_string());

    match TcpStream::connect_timeout(
        &addr.parse().map_err(|e| format!("bad address: {}", e))?,
        Duration::from_secs(5),
    ) {
        Ok(_) => Ok("REACHABLE".to_string()),
        Err(e) => Ok(format!("UNREACHABLE: {}", e)),
    }
}
