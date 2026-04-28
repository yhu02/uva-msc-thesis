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
use std::net::{TcpStream, ToSocketAddrs};
use std::process;
use std::time::{Duration, Instant};

fn main() {
    match run_check() {
        Ok(output) => print!("{}", output),
        Err(e) => {
            eprintln!("probe check-http-latency error: {}", e);
            process::exit(1);
        }
    }
}

fn run_check() -> Result<String, String> {
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
        .unwrap_or(5000);

    let (host, port, path) = parse_url(&url)?;
    let timeout = Duration::from_millis(timeout_ms);

    let started = Instant::now();
    let status = http_get(&host, port, &path, timeout)?;
    let elapsed_ms = started.elapsed().as_millis();

    if status != expect_status {
        return Ok(format!(
            "LATENCY_FAIL status={} expected={} elapsed_ms={}",
            status, expect_status, elapsed_ms
        ));
    }

    if elapsed_ms > max_ms {
        Ok(format!("LATENCY_SLOW {} (max={})", elapsed_ms, max_ms))
    } else {
        Ok(format!("LATENCY_OK {}", elapsed_ms))
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

fn http_get(host: &str, port: u16, path: &str, timeout: Duration) -> Result<u16, String> {
    let sock_addr = (host, port)
        .to_socket_addrs()
        .map_err(|e| format!("DNS resolve {}:{}: {}", host, port, e))?
        .next()
        .ok_or_else(|| format!("DNS resolve {}:{}: no addresses", host, port))?;

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

    let mut buf = Vec::with_capacity(256);
    let mut chunk = [0u8; 256];
    let n = stream
        .read(&mut chunk)
        .map_err(|e| format!("read: {}", e))?;
    buf.extend_from_slice(&chunk[..n]);

    parse_status_line(&buf)
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
