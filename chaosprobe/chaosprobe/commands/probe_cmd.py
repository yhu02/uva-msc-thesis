"""CLI commands for Rust cmdProbe builder."""

import sys
from pathlib import Path

import click

from chaosprobe.probes.builder import DEFAULT_REGISTRY


@click.group()
def probe():
    """Build and manage Rust cmdProbe binaries.

    Create Rust probes that are compiled to static Linux binaries,
    packaged into minimal container images, and automatically injected
    into ChaosEngine cmdProbe specs.

    \b
    Workflow:
      1. chaosprobe probe init <name> --scenario <path>
      2. Edit the generated Rust source
      3. chaosprobe probe build <scenario>
      4. chaosprobe run ... (auto-builds if probes/ dir exists)
    """
    pass


@probe.command("init")
@click.argument("name")
@click.option(
    "--scenario",
    "-s",
    type=click.Path(exists=True, file_okay=False),
    required=True,
    help="Scenario directory to create the probe in",
)
@click.option(
    "--single-file",
    is_flag=True,
    help="Create a single .rs file instead of a full Cargo project",
)
def probe_init(name: str, scenario: str, single_file: bool):
    """Scaffold a new Rust cmdProbe.

    Creates a ready-to-edit probe in SCENARIO/probes/NAME/.

    \b
    Examples:
      chaosprobe probe init check-db -s scenarios/online-boutique
      chaosprobe probe init health --single-file -s scenarios/nginx
    """
    from chaosprobe.probes.templates import (
        generate_cargo_toml,
        generate_main_rs,
        generate_single_file_rs,
    )

    scenario_path = Path(scenario).resolve()
    probes_dir = scenario_path / "probes"
    probes_dir.mkdir(exist_ok=True)

    if single_file:
        target = probes_dir / f"{name}.rs"
        if target.exists():
            click.echo(f"Error: {target} already exists", err=True)
            sys.exit(1)
        target.write_text(generate_single_file_rs(name))
        click.echo(f"Created single-file probe: {target}")
    else:
        proj_dir = probes_dir / name
        if proj_dir.exists():
            click.echo(f"Error: {proj_dir} already exists", err=True)
            sys.exit(1)
        src_dir = proj_dir / "src"
        src_dir.mkdir(parents=True)
        (proj_dir / "Cargo.toml").write_text(generate_cargo_toml(name))
        (src_dir / "main.rs").write_text(generate_main_rs(name))
        click.echo(f"Created Cargo probe project: {proj_dir}/")
        click.echo(f"  Edit: {src_dir / 'main.rs'}")
        click.echo(f"  Build: chaosprobe probe build {scenario}")

    click.echo(
        f"\nAdd a cmdProbe referencing this probe to your experiment YAML:\n"
        f"  - name: {name}\n"
        f"    type: cmdProbe\n"
        f"    mode: Edge\n"
        f"    cmdProbe/inputs:\n"
        f"      command: /probe/{name}\n"
        f"      comparator:\n"
        f"        type: string\n"
        f"        criteria: contains\n"
        f"        value: \"OK\"\n"
        f"      source:\n"
        f"        image: auto  # patched at build time\n"
        f"    runProperties:\n"
        f"      probeTimeout: 10s\n"
        f"      interval: 5s\n"
        f"      retry: 2"
    )


@probe.command("build")
@click.argument("scenario", type=click.Path(exists=True))
@click.option(
    "--registry",
    "-r",
    default=DEFAULT_REGISTRY,
    envvar="CHAOSPROBE_REGISTRY",
    show_default=True,
    help=(
        "Container registry host (env: CHAOSPROBE_REGISTRY)."
        " The image namespace comes from CHAOSPROBE_REGISTRY_USER."
    ),
)
@click.option(
    "--push",
    is_flag=True,
    help="Push built images to the container registry",
)
def probe_build(scenario: str, registry: str, push: bool):
    """Compile Rust probes and build container images.

    Discovers .rs files and Cargo projects in SCENARIO/probes/,
    compiles them to static Linux binaries, and packages them as
    container images.

    \b
    Examples:
      chaosprobe probe build scenarios/online-boutique
      chaosprobe probe build scenarios/nginx -r ghcr.io/user --push
    """
    from chaosprobe.probes.builder import ProbeBuilderError, RustProbeBuilder

    scenario_path = str(Path(scenario).resolve())
    builder = RustProbeBuilder(registry=registry, push=push)

    probes = builder.discover_probes(scenario_path)
    if not probes:
        click.echo(f"No Rust probes found in {scenario_path}/probes/")
        return

    click.echo(f"Found {len(probes)} probe(s):")
    for p in probes:
        click.echo(f"  {p['name']} ({p['kind']})")

    click.echo()
    try:
        images = builder.build_all(scenario_path)
    except ProbeBuilderError as e:
        click.echo(f"Build failed: {e}", err=True)
        sys.exit(1)

    click.echo(f"\nBuilt {len(images)} image(s):")
    for name, tag in images.items():
        click.echo(f"  {name}: {tag}")


@probe.command("list")
@click.argument("scenario", type=click.Path(exists=True))
def probe_list(scenario: str):
    """List Rust probes discovered in a scenario directory."""
    from chaosprobe.probes.builder import RustProbeBuilder

    scenario_path = str(Path(scenario).resolve())
    probes = RustProbeBuilder.discover_probes(scenario_path)

    if not probes:
        click.echo(f"No Rust probes found in {scenario_path}/probes/")
        return

    click.echo(f"Probes in {scenario_path}/probes/:")
    for p in probes:
        kind_label = "Cargo project" if p["kind"] == "cargo" else "single file"
        click.echo(f"  {p['name']} ({kind_label}) — {p['path']}")
