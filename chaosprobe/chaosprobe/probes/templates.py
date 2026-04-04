"""Rust probe templates and scaffolding.

Generates Cargo.toml, main.rs, and Dockerfile content for
cmdProbe binaries that get compiled and packaged into containers.
"""


def generate_cargo_toml(name: str) -> str:
    """Generate a minimal Cargo.toml for a probe binary.

    Args:
        name: Probe name (used as the package and binary name).
    """
    # Sanitise for Cargo (hyphens are fine, but no spaces/specials)
    safe_name = name.replace(" ", "-")
    return f"""\
[package]
name = "{safe_name}"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "{safe_name}"
path = "src/main.rs"

[profile.release]
opt-level = 3
lto = true
strip = true
"""


def generate_main_rs(name: str) -> str:
    """Generate a starter main.rs for a probe binary.

    The generated binary:
    - Performs a placeholder health check
    - Prints a result string to stdout (matched by the comparator)
    - Exits 0 on success, 1 on failure

    Args:
        name: Probe name (used in comments).
    """
    return f"""\
//! ChaosProbe resilience probe: {name}
//!
//! This binary is executed as a LitmusChaos cmdProbe inside a
//! source container during chaos experiments.
//!
//! Contract:
//!   - Print the check result to stdout.
//!   - The ChaosEngine comparator matches against this output.
//!   - Exit code 0 means the check executed (verdict is from comparator).
//!   - Exit code non-zero means the probe itself failed to run.
//!
//! Edit the `run_check()` function to implement your probe logic.

use std::process;

fn main() {{
    match run_check() {{
        Ok(output) => {{
            print!("{{}}", output);
        }}
        Err(e) => {{
            eprintln!("probe {name} failed: {{}}", e);
            process::exit(1);
        }}
    }}
}}

/// Implement your probe logic here.
///
/// Return `Ok(String)` with the value the comparator should match.
/// Return `Err(String)` if the probe cannot execute.
fn run_check() -> Result<String, String> {{
    // Example: check that a file exists
    // let path = "/tmp/healthy";
    // if std::path::Path::new(path).exists() {{
    //     Ok("HEALTHY".to_string())
    // }} else {{
    //     Ok("UNHEALTHY".to_string())
    // }}

    // Placeholder — replace with real check
    Ok("OK".to_string())
}}
"""


def generate_single_file_rs(name: str) -> str:
    """Generate a minimal single-file Rust probe.

    Simpler than the full Cargo scaffold — just a main.rs that can
    be compiled directly with ``rustc``.

    Args:
        name: Probe name.
    """
    return f"""\
//! ChaosProbe resilience probe: {name}
//!
//! Compiled with: rustc --target x86_64-unknown-linux-musl --edition 2021

use std::process;

fn main() {{
    match run_check() {{
        Ok(output) => print!("{{}}", output),
        Err(e) => {{
            eprintln!("probe {name} error: {{}}", e);
            process::exit(1);
        }}
    }}
}}

fn run_check() -> Result<String, String> {{
    // TODO: implement your check
    Ok("OK".to_string())
}}
"""


def generate_dockerfile(binary_name: str) -> str:
    """Generate a minimal Dockerfile for a compiled probe binary.

    Uses ``scratch`` as the base image for the smallest possible
    container. The binary is placed at ``/probe/<name>``.

    Args:
        binary_name: Name of the binary file (must be in the build context).
    """
    return f"""\
FROM scratch
COPY {binary_name} /probe/{binary_name}
ENTRYPOINT ["/probe/{binary_name}"]
"""
