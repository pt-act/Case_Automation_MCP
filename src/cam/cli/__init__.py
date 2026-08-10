"""casemat CLI — zero-friction local bootstrap + connector scaffold.

Commands:
  casemat start [--domain <pack>] [--no-browser] [--port <n>]
  casemat init --connector <type> [--name <name>]
  casemat status
  casemat stop
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import webbrowser
from pathlib import Path

import typer

app = typer.Typer(
    name="casemat",
    help="Case Automation MCP — zero-friction local bootstrap and management.",
    no_args_is_help=True,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and return the result. Raises on failure."""
    return subprocess.run(
        cmd, capture_output=True, text=True, check=True, cwd=cwd,
    )


def _docker_running() -> bool:
    """Check if Docker is running."""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _wait_for_health(service: str, timeout: int = 60) -> bool:
    """Wait for a docker-compose service to be healthy."""
    for _ in range(timeout):
        result = subprocess.run(
            ["docker", "compose", "ps", service, "--format", "json"],
            capture_output=True, text=True, cwd=_PROJECT_ROOT,
        )
        if result.returncode == 0 and "healthy" in result.stdout:
            return True
        time.sleep(1)
    return False


def _port_in_use(port: int) -> bool:
    """Check if a port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def _seed_data(database_url: str) -> None:
    """Seed sample data if the database is empty."""
    import asyncio
    import json

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _seed() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            count = await conn.scalar(text("SELECT count(*) FROM contacts"))
            if count and count > 0:
                typer.echo("  Seed data already present — skipping.")
                return

            seed_dir = Path(__file__).parent / "seed"
            for fixture_file in ["contacts.json", "matters.json", "deadlines.json"]:
                fixture_path = seed_dir / fixture_file
                if not fixture_path.exists():
                    continue
                data = json.loads(fixture_path.read_text())
                table = fixture_file.replace(".json", "")
                for row in data:
                    cols = ", ".join(row.keys())
                    vals = ", ".join(f":{k}" for k in row)
                    await conn.execute(
                        text(f"INSERT INTO {table} ({cols}) VALUES ({vals})"),
                        row,
                    )
                typer.echo(f"  Seeded {len(data)} {table}")

        await engine.dispose()

    asyncio.run(_seed())


# ---------------------------------------------------------------------------
# casemat start
# ---------------------------------------------------------------------------


@app.command()
def start(
    domain: str = typer.Option(
        "immigration", "--domain", "-d", help="Domain pack to activate."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Skip opening the browser."
    ),
    port: int = typer.Option(8001, "--port", "-p", help="Sidecar port."),
) -> None:
    """Bootstrap Postgres + Redis, run migrations, seed data, start sidecar."""

    # 1. Check Docker
    if not _docker_running():
        typer.echo(
            "Error: Docker is not running. "
            "Start Docker Desktop and try again."
        )
        raise typer.Exit(1)

    typer.echo("Starting services...")

    # 2. Start Postgres + Redis
    try:
        _run(
            ["docker", "compose", "up", "-d", "postgres", "redis"],
            cwd=_PROJECT_ROOT,
        )
    except subprocess.CalledProcessError as exc:
        typer.echo(f"Error: Failed to start services: {exc.stderr}")
        raise typer.Exit(1) from exc

    typer.echo("  Waiting for Postgres...")
    if not _wait_for_health("postgres"):
        typer.echo("Error: Postgres did not become healthy within 60s.")
        raise typer.Exit(1)
    typer.echo("  Waiting for Redis...")
    if not _wait_for_health("redis"):
        typer.echo("Error: Redis did not become healthy within 60s.")
        raise typer.Exit(1)

    # 3. Run migrations
    typer.echo("  Running migrations...")
    try:
        _run(["uv", "run", "alembic", "upgrade", "head"], cwd=_PROJECT_ROOT)
    except subprocess.CalledProcessError as exc:
        typer.echo(f"Error: Migration failed: {exc.stderr}")
        raise typer.Exit(1) from exc

    # 4. Seed data
    db_url = os.environ.get(
        "CAM_DATABASE_URL",
        "postgresql+asyncpg://cam:cam_dev_pw@localhost:5432/cam",
    )
    typer.echo("  Seeding sample data...")
    _seed_data(db_url)

    # 5. Validate domain pack
    typer.echo(f"  Validating domain pack: {domain}...")
    try:
        from cam.packs import activate_configured_pack

        pack = activate_configured_pack(domain)
        typer.echo(f"  Pack active: {pack.name} v{pack.version}")
    except Exception as exc:
        from cam.packs.base import list_registered_pack_names

        registered = ", ".join(list_registered_pack_names())
        typer.echo(f"Error: Pack '{domain}' is not valid. {exc}")
        typer.echo(f"  Registered packs: {registered}")
        raise typer.Exit(1) from exc

    # 6. Check port
    if _port_in_use(port):
        typer.echo(f"Error: Port {port} is in use. Try --port {port + 1}.")
        raise typer.Exit(1)

    # 7. Start sidecar
    typer.echo(f"\n  Pack:      {pack.name}")
    typer.echo(f"  Sidecar:   http://localhost:{port}")
    typer.echo("  Postgres:  localhost:5432")
    typer.echo("  Redis:     localhost:6379")
    typer.echo("\n  Press Ctrl+C to stop.\n")

    if not no_browser:
        webbrowser.open(f"http://localhost:{port}")

    try:
        subprocess.run(
            [
                "uv", "run", "uvicorn",
                "cam.sidecar.main:app",
                "--host", "0.0.0.0",
                "--port", str(port),
            ],
            cwd=_PROJECT_ROOT,
        )
    except KeyboardInterrupt:
        typer.echo("\nStopping sidecar...")


# ---------------------------------------------------------------------------
# casemat init --connector
# ---------------------------------------------------------------------------


@app.command()
def init(
    connector: str = typer.Option(
        ..., "--connector", "-c", help="Connector type (e.g. clio, salesforce)."
    ),
    name: str = typer.Option(
        None, "--name", "-n", help="Connector name (defaults to connector type)."
    ),
) -> None:
    """Scaffold a new connector adapter from a template."""

    connector_name = name or connector
    connector_dir = _PROJECT_ROOT / "src" / "cam" / "connectors" / connector_name

    if connector_dir.exists():
        typer.echo(f"Error: Directory already exists: {connector_dir}")
        raise typer.Exit(1)

    connector_dir.mkdir(parents=True)

    # Template substitution context
    ctx = {
        "name": connector_name,
        "name_title": connector_name.title(),
    }

    # Generate files from templates
    template_files = {
        "__init__.py.tmpl": "__init__.py",
        "client.py.tmpl": "client.py",
        "adapter.py.tmpl": "adapter.py",
        "health.py.tmpl": "health.py",
        "README.md.tmpl": "README.md",
    }

    for tmpl_name, out_name in template_files.items():
        tmpl_path = _TEMPLATES_DIR / tmpl_name
        if tmpl_path.exists():
            content = tmpl_path.read_text().format(**ctx)
            (connector_dir / out_name).write_text(content)

    # Contract test (goes in tests/)
    test_dir = _PROJECT_ROOT / "tests"
    test_tmpl = _TEMPLATES_DIR / "test_contract.py.tmpl"
    if test_tmpl.exists():
        test_content = test_tmpl.read_text().format(**ctx)
        test_file = test_dir / f"test_{connector_name}_contract.py"
        test_file.write_text(test_content)

    typer.echo(f"Created connector scaffold: {connector_dir}")
    for f in sorted(template_files.values()):
        typer.echo(f"  - {f}")
    typer.echo(f"  - tests/test_{connector_name}_contract.py")
    typer.echo("\nNext steps:")
    typer.echo("  1. Fill in the TODO markers in the generated files")
    typer.echo(f"  2. Run: uv run pytest tests/test_{connector_name}_contract.py")


# ---------------------------------------------------------------------------
# casemat status
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Check if services are running and healthy."""

    typer.echo(f"{'Service':<12} {'Status':<10} {'Details'}")
    typer.echo(f"{'-------':<12} {'------':<10} {'-------'}")

    services = [
        ("Postgres", 5432, "localhost:5432"),
        ("Redis", 6379, "localhost:6379"),
        ("Sidecar", 8001, "http://localhost:8001"),
    ]

    for name, port, details in services:
        running = "running" if _port_in_use(port) else "stopped"
        typer.echo(f"{name:<12} {running:<10} {details}")


# ---------------------------------------------------------------------------
# casemat stop
# ---------------------------------------------------------------------------


@app.command()
def stop() -> None:
    """Tear down docker-compose services."""

    if not _docker_running():
        typer.echo("Docker is not running — nothing to stop.")
        return

    try:
        _run(["docker", "compose", "down"], cwd=_PROJECT_ROOT)
        typer.echo("Services stopped.")
    except subprocess.CalledProcessError as exc:
        typer.echo(f"Error: Failed to stop services: {exc.stderr}")
        raise typer.Exit(1) from exc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the casemat CLI."""
    app()


if __name__ == "__main__":
    main()
