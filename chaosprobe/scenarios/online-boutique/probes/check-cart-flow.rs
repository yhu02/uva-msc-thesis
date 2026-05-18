//! ChaosProbe resilience probe: check-cart-flow
//!
//! Multi-route user journey probe. Walks frontend routes that exercise
//! different downstream services and verifies each one returns the
//! expected status within a per-route budget. Detects cascade failures
//! that single-route probes miss — e.g. cart works but product fails,
//! or homepage works but checkout times out.
//!
//! Always exits 0 with a comparator-parseable line.  Errors on any
//! route map to `FLOW_FAIL ...` so LitmusChaos records a clean Fail
//! verdict instead of dropping the tick into the retry/timeout path.
//!
//! Routes (each must return 200 within PROBE_ROUTE_MS_MAX):
//!   GET /                       — frontend + recommendation + ad + currency
//!   GET /product/OLJCESPC7Z     — frontend + productcatalog + currency
//!   GET /cart                   — frontend + cartservice + redis-cart
//!   GET /_healthz               — frontend self-check (control)
//!
//! Environment:
//!   PROBE_HOST           — host:port (default frontend.online-boutique.svc.cluster.local:80)
//!   PROBE_ROUTE_MS_MAX   — per-route budget in ms (default 1500)
//!   PROBE_TIMEOUT_MS     — per-route TCP/DNS bound in ms (default 2000)
//!   PROBE_ROUTES         — comma-separated path override (rarely needed)
//!
//! Output:
//!   FLOW_OK <total_ms>           every route OK and within budget
//!   FLOW_SLOW <route> <ms>       first route over budget (still 200)
//!   FLOW_FAIL <route> <reason>   first route with bad status / error

use std::env;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

const DEFAULT_ROUTES: &[&str] = &["/", "/product/OLJCESPC7Z", "/cart", "/_healthz"];

fn main() {
    print!("{}", run_check());
}

fn run_check() -> String {
    let host_port = env::var("PROBE_HOST")
        .unwrap_or_else(|_| "frontend.online-boutique.svc.cluster.local:80".to_string());
    let route_max_ms: u128 = env::var("PROBE_ROUTE_MS_MAX")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(1500);
    let timeout_ms: u64 = env::var("PROBE_TIMEOUT_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(2000);

    let routes_owned: Vec<String>;
    let routes: Vec<&str> = match env::var("PROBE_ROUTES") {
        Ok(s) => {
            routes_owned = s.split(',').map(|r| r.trim().to_string()).collect();
            routes_owned.iter().map(|r| r.as_str()).collect()
        }
        Err(_) => DEFAULT_ROUTES.to_vec(),
    };

    let (host, port) = match split_host_port(&host_port) {
        Ok(v) => v,
        Err(e) => return format!("FLOW_FAIL parse {}", e),
    };
    let timeout = Duration::from_millis(timeout_ms);

    let started = Instant::now();
    for route in &routes {
        let route_start = Instant::now();
        let status = match http_get(&host, port, route, timeout) {
            Ok(s) => s,
            Err(e) => return format!("FLOW_FAIL {} {}", route, e),
        };
        let route_ms = route_start.elapsed().as_millis();

        if status != 200 {
            return format!("FLOW_FAIL {} status={}", route, status);
        }
        if route_ms > route_max_ms {
            return format!(
                "FLOW_SLOW {} {} (max={})",
                route, route_ms, route_max_ms
            );
        }
    }

    format!("FLOW_OK {}", started.elapsed().as_millis())
}

fn split_host_port(s: &str) -> Result<(String, u16), String> {
    let i = s.rfind(':').ok_or_else(|| format!("missing port in '{}'", s))?;
    let port: u16 = s[i + 1..]
        .parse()
        .map_err(|e| format!("bad port in '{}': {}", s, e))?;
    Ok((s[..i].to_string(), port))
}

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

    let mut stream =
        TcpStream::connect_timeout(&sock_addr, timeout).map_err(|e| format!("connect: {}", e))?;
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
    let head = String::from_utf8_lossy(&chunk[..n]);
    let line = head
        .lines()
        .next()
        .ok_or_else(|| "empty response".to_string())?;
    let mut parts = line.split_whitespace();
    let _ = parts.next().ok_or_else(|| "no version".to_string())?;
    let code = parts.next().ok_or_else(|| "no status".to_string())?;
    code.parse::<u16>()
        .map_err(|e| format!("bad status '{}': {}", code, e))
}
