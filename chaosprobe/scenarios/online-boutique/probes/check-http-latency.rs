//! ChaosProbe resilience probe: check-http-latency
//!
//! Issues a single HTTP/1.1 GET, measures wall-clock response time, and
//! emits a tri-state verdict so the comparator can distinguish recovered
//! services from slow ones. Pure stdlib — no Rust deps.
//!
//! Environment:
//!   PROBE_URL              — full URL (default frontend homepage in online-boutique)
//!   PROBE_EXPECT_STATUS    — expected HTTP status code (default 200)
//!   PROBE_LATENCY_MS_MAX   — fail threshold in ms (default 1000)
//!   PROBE_TIMEOUT_MS       — TCP read/connect timeout in ms (default 5000)
//!
//! Output (matched by ChaosEngine cmdProbe comparator):
//!   LATENCY_OK <ms>           response within threshold
//!   LATENCY_SLOW <ms>         response over threshold
//!   LATENCY_FAIL <status>     status mismatch
//!   LATENCY_FAIL <error>      transport error

use std::env;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

fn main() {
    // Always exit 0 with a comparator-parseable line.  Any errors are
    // formatted as LATENCY_FAIL so the LitmusChaos cmdProbe comparator
    // records a clean Fail verdict instead of dropping the tick into
    // the retry/timeout path (which can trigger the post-chaos abort
    // cascade described in litmus-go pkg/probe/probe.go).
    print!("{}", run_check());
}

fn run_check() -> String {
    let url = env::var("PROBE_URL").unwrap_or_else(|_| {
        "http://frontend.online-boutique.svc.cluster.local/".to_string()
    });
    let expect_status: u16 = env::var("PROBE_EXPECT_STATUS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(200);
    let max_ms: u128 = env::var("PROBE_LATENCY_MS_MAX")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(1000);
    let timeout_ms: u64 = env::var("PROBE_TIMEOUT_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(2000);

    let (host, port, path) = match parse_url(&url) {
        Ok(v) => v,
        Err(e) => return format!("LATENCY_FAIL parse: {}", e),
    };
    let timeout = Duration::from_millis(timeout_ms);

    let started = Instant::now();
    let status = match http_get(&host, port, &path, timeout) {
        Ok(s) => s,
        Err(e) => {
            let elapsed_ms = started.elapsed().as_millis();
            return format!("LATENCY_FAIL {} elapsed_ms={}", e, elapsed_ms);
        }
    };
    let elapsed_ms = started.elapsed().as_millis();

    if status != expect_status {
        return format!(
            "LATENCY_FAIL status={} expected={} elapsed_ms={}",
            status, expect_status, elapsed_ms
        );
    }

    if elapsed_ms > max_ms {
        format!("LATENCY_SLOW {} (max={})", elapsed_ms, max_ms)
    } else {
        format!("LATENCY_OK {}", elapsed_ms)
    }
}

fn parse_url(url: &str) -> Result<(String, u16, String), String> {
    let rest = url
        .strip_prefix("http://")
        .ok_or_else(|| format!("only http:// supported, got '{}'", url))?;
    let (authority, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let (host, port) = match authority.rfind(':') {
        Some(i) => {
            let port: u16 = authority[i + 1..]
                .parse()
                .map_err(|e| format!("bad port: {}", e))?;
            (authority[..i].to_string(), port)
        }
        None => (authority.to_string(), 80),
    };
    Ok((host, port, path.to_string()))
}

// std::net::ToSocketAddrs has no per-call timeout — under DNS pressure
// it can hang past LitmusChaos's probeTimeout, killing the tick.  Bound
// the resolve manually with a thread + recv_timeout.
fn bounded_resolve(host: &str, port: u16, timeout: Duration) -> Result<SocketAddr, String> {
    let host_port = format!("{}:{}", host, port);
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

fn http_get(host: &str, port: u16, path: &str, timeout: Duration) -> Result<u16, String> {
    let sock_addr = bounded_resolve(host, port, timeout)?;

    let mut stream = TcpStream::connect_timeout(&sock_addr, timeout)
        .map_err(|e| format!("connect: {}", e))?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|e| format!("set timeout: {}", e))?;

    let req = format!(
        "GET {} HTTP/1.1\r\nHost: {}\r\nUser-Agent: chaosprobe/1.0\r\nConnection: close\r\nAccept: */*\r\n\r\n",
        path, host
    );
    stream
        .write_all(req.as_bytes())
        .map_err(|e| format!("write: {}", e))?;

    let mut chunk = [0u8; 256];
    let n = stream
        .read(&mut chunk)
        .map_err(|e| format!("read: {}", e))?;

    parse_status_line(&chunk[..n])
}

fn parse_status_line(buf: &[u8]) -> Result<u16, String> {
    let head = String::from_utf8_lossy(buf);
    let line = head
        .lines()
        .next()
        .ok_or_else(|| "empty response".to_string())?;
    let mut parts = line.split_whitespace();
    let _version = parts.next().ok_or_else(|| "no version".to_string())?;
    let code = parts.next().ok_or_else(|| "no status code".to_string())?;
    code.parse::<u16>()
        .map_err(|e| format!("bad status code '{}': {}", code, e))
}
