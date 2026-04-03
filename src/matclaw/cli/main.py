"""
matclaw CLI — talks to the MatClaw server via REST/SSE.

Usage:
  matclaw run "plot a sine wave"
  matclaw matlab "x=1:10; plot(x)"
  matclaw python "print('hello')"
  matclaw shell "ls -la"
  matclaw status
  matclaw schedule list
  matclaw schedule add --name "audit" --cron "0 * * * *" --runtime shell --command "echo audit"
  matclaw tools list
  matclaw agents list
  matclaw audit
"""
from __future__ import annotations

import json
import os
import sys

import click

try:
    import httpx
except ImportError:
    print("httpx is required. Install with: pip install httpx", file=sys.stderr)
    sys.exit(1)

SERVER = os.environ.get("MATCLAW_SERVER", "http://localhost:8000")
API_KEY = os.environ.get("MATCLAW_API_KEY", "")


def _headers() -> dict:
    h: dict = {}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def _post_stream(endpoint: str, payload: dict) -> None:
    """POST and stream SSE response to stdout."""
    try:
        with httpx.stream(
            "POST", f"{SERVER}{endpoint}",
            json=payload, headers=_headers(), timeout=180,
        ) as resp:
            if resp.status_code != 200:
                click.echo(f"Error {resp.status_code}: {resp.read().decode()}", err=True)
                sys.exit(1)
            buffer = ""
            for chunk in resp.iter_text():
                buffer += chunk
                lines = buffer.split("\n")
                buffer = lines.pop()
                for line in lines:
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if "token" in data:
                                click.echo(data["token"], nl=False)
                            elif "output" in data:
                                click.echo(f"\n{data['output']}")
                            elif "error" in data:
                                click.echo(f"\n✗ {data['error']}", err=True)
                            elif "plots" in data and data["plots"]:
                                for p in data["plots"]:
                                    click.echo(f"\n[plot] {SERVER}{p}")
                        except json.JSONDecodeError:
                            pass
        click.echo()
    except httpx.ConnectError:
        click.echo(f"Cannot connect to MatClaw server at {SERVER}", err=True)
        click.echo("Start the server with: matclaw-server", err=True)
        sys.exit(1)


# ── CLI root ───────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("0.1.0", prog_name="matclaw")
def cli():
    """MatClaw — Autonomous Agent Platform for Engineering"""


# ── Run commands ───────────────────────────────────────────────────────────────

@cli.command()
@click.argument("prompt")
@click.option("--runtime", "-r", default=None,
              help="Force runtime: matlab | python | shell")
def run(prompt: str, runtime: str | None):
    """Run a natural language prompt (streams output)."""
    click.echo(f"▶ {prompt}", err=True)
    payload: dict = {"message": prompt}
    if runtime:
        payload["force_runtime"] = runtime
    _post_stream("/api/run/stream", payload)


@cli.command()
@click.argument("code")
def matlab(code: str):
    """Execute MATLAB code directly."""
    click.echo("▶ [matlab]", err=True)
    _post_stream("/api/run/stream", {"message": code, "force_runtime": "matlab"})


@cli.command(name="python")
@click.argument("code")
def python_cmd(code: str):
    """Execute Python code directly."""
    click.echo("▶ [python]", err=True)
    _post_stream("/api/run/stream", {"message": code, "force_runtime": "python"})


@cli.command()
@click.argument("code")
def shell(code: str):
    """Execute a shell command directly."""
    click.echo("▶ [shell]", err=True)
    _post_stream("/api/run/stream", {"message": code, "force_runtime": "shell"})


# ── Status ─────────────────────────────────────────────────────────────────────

@cli.command()
def status():
    """Show daemon health, running jobs, and MATLAB status."""
    try:
        r = httpx.get(f"{SERVER}/health", headers=_headers(), timeout=5)
        h = r.json()
        click.echo(f"Server:    {'✓ ok' if h.get('status') == 'ok' else '✗ error'}")
        click.echo(f"MATLAB:    {'✓ connected' if h.get('matlab') else '✗ disconnected'}")
    except Exception as e:
        click.echo(f"Cannot reach server: {e}", err=True)
        return

    try:
        r2 = httpx.get(f"{SERVER}/api/daemon/status", headers=_headers(), timeout=5)
        d = r2.json()
        click.echo(f"Uptime:    {d.get('uptime_seconds', '?')}s")
        click.echo(f"Scheduler: {'running' if d.get('scheduler_running') else 'stopped'}")
        jobs = d.get("jobs", {})
        if jobs:
            click.echo(f"Jobs:      {jobs.get('running', 0)} running, "
                       f"{jobs.get('queued', 0)} queued, "
                       f"{jobs.get('done', 0)} done")
    except Exception:
        pass


# ── Scheduler ──────────────────────────────────────────────────────────────────

@cli.group()
def schedule():
    """Manage scheduled background tasks."""


@schedule.command("list")
def schedule_list():
    """List all scheduled tasks."""
    r = httpx.get(f"{SERVER}/api/scheduler/tasks", headers=_headers(), timeout=10)
    tasks = r.json()
    if not tasks:
        click.echo("No scheduled tasks. Add one with: matclaw schedule add")
        return
    click.echo(f"{'ENABLED':<8} {'ID':<10} {'NAME':<24} {'CRON':<20} {'RUNTIME':<10} LAST")
    click.echo("-" * 90)
    for t in tasks:
        enabled = "●" if t["enabled"] else "○"
        last = t.get("last_status") or "-"
        click.echo(
            f"{enabled:<8} {t['id'][:8]:<10} {t['name']:<24} "
            f"{t['cron_expression']:<20} {t['runtime']:<10} {last}"
        )


@schedule.command("add")
@click.option("--name", required=True, help="Task name")
@click.option("--cron", required=True, help="Cron: @hourly | @daily | '*/15 * * * *'")
@click.option("--runtime", default="shell", help="Runtime: matlab|python|shell")
@click.option("--command", required=True, help="Command or code to run")
def schedule_add(name: str, cron: str, runtime: str, command: str):
    """Add a new scheduled task."""
    r = httpx.post(
        f"{SERVER}/api/scheduler/tasks", headers=_headers(),
        json={"name": name, "cron_expression": cron,
              "runtime": runtime, "command": command, "enabled": True},
        timeout=10,
    )
    if r.status_code == 200:
        task = r.json()
        click.echo(f"✓ Scheduled [{task['id'][:8]}]: {task['name']} ({task['cron_expression']})")
    else:
        click.echo(f"Error: {r.status_code} {r.text}", err=True)


@schedule.command("remove")
@click.argument("task_id")
def schedule_remove(task_id: str):
    """Remove a scheduled task by ID."""
    r = httpx.delete(f"{SERVER}/api/scheduler/tasks/{task_id}", headers=_headers(), timeout=10)
    if r.status_code == 200:
        click.echo(f"✓ Removed task {task_id}")
    else:
        click.echo(f"Error: {r.status_code}", err=True)


# ── Agents ─────────────────────────────────────────────────────────────────────

@cli.group()
def agents():
    """Manage agents."""


@agents.command("list")
def agents_list():
    """List all registered agents."""
    r = httpx.get(f"{SERVER}/api/agents", headers=_headers(), timeout=10)
    for a in r.json():
        tools_str = ", ".join(a.get("allowed_tools", [])) or "none"
        click.echo(f"• {a['id']:<30} {a['name']:<30} tools: {tools_str}")


# ── Tools ──────────────────────────────────────────────────────────────────────

@cli.group()
def tools():
    """Manage registered tools."""


@tools.command("list")
def tools_list():
    """List all registered tools."""
    try:
        r = httpx.get(f"{SERVER}/api/tools", headers=_headers(), timeout=10)
        for t in r.json():
            tags = ", ".join(t.get("tags", []))
            click.echo(f"• {t['name']:<24} [{t['runtime']}]  {t['description']}")
            if tags:
                click.echo(f"  tags: {tags}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)


# ── Auth ───────────────────────────────────────────────────────────────────────

@cli.group()
def auth():
    """Manage API keys."""


@auth.command("create")
@click.option("--label", required=True, help="Key label / description")
@click.option("--role", default="developer",
              type=click.Choice(["admin", "developer", "viewer"]))
@click.option("--tenant", default="default", help="Tenant ID")
@click.option("--rpm", default=60, help="Rate limit (requests per minute)")
def auth_create(label: str, role: str, tenant: str, rpm: int):
    """Create a new API key."""
    r = httpx.post(
        f"{SERVER}/api/auth/keys", headers=_headers(),
        json={"label": label, "role": role, "tenant_id": tenant, "rate_limit_rpm": rpm},
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json()
        click.echo(f"✓ Key created for '{label}' ({role})")
        click.echo(f"  Key: {data['raw_key']}")
        click.echo("  SAVE THIS — it won't be shown again.")
    else:
        click.echo(f"Error: {r.text}", err=True)


@auth.command("list")
def auth_list():
    """List all API keys."""
    r = httpx.get(f"{SERVER}/api/auth/keys", headers=_headers(), timeout=10)
    for k in r.json():
        enabled = "●" if k["enabled"] else "○"
        click.echo(
            f"{enabled} {k['id'][:8]}  {k['label']:<30} "
            f"{k['role']:<12} {k['key_prefix']}...  "
            f"tenant={k['tenant_id']}"
        )


@auth.command("revoke")
@click.argument("key_id")
def auth_revoke(key_id: str):
    """Revoke an API key."""
    r = httpx.delete(f"{SERVER}/api/auth/keys/{key_id}", headers=_headers(), timeout=10)
    click.echo("✓ Revoked." if r.status_code == 200 else f"Error: {r.text}", err=True)


# ── Audit ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--limit", default=50, help="Number of entries to show")
def audit(limit: int):
    """Show recent audit log entries."""
    r = httpx.get(f"{SERVER}/api/audit", params={"limit": limit}, headers=_headers(), timeout=10)
    entries = r.json()
    if not entries:
        click.echo("No audit entries.")
        return
    click.echo(f"{'TIME':<12} {'METHOD':<8} {'PATH':<45} {'STATUS':<8} {'MS'}")
    click.echo("-" * 90)
    for e in entries:
        import datetime
        ts = datetime.datetime.fromtimestamp(e.get("ts", 0)).strftime("%H:%M:%S")
        click.echo(
            f"{ts:<12} {e.get('method',''):<8} {e.get('path',''):<45} "
            f"{e.get('status_code',''):<8} {e.get('duration_ms','')}ms"
        )


# ── Metrics ────────────────────────────────────────────────────────────────────

@cli.command()
def metrics():
    """Show server metrics snapshot."""
    r = httpx.get(f"{SERVER}/metrics", headers=_headers(), timeout=10)
    data = r.json()
    click.echo(f"Uptime:          {data.get('uptime_seconds', '?')}s")
    click.echo(f"Total requests:  {data.get('total_requests', 0)}")
    click.echo(f"Total errors:    {data.get('total_errors', 0)}")
    click.echo()
    endpoints = data.get("endpoints", {})
    if endpoints:
        click.echo(f"{'ENDPOINT':<50} {'REQ':>6} {'ERR':>6} {'AVG_MS':>8} {'P95_MS':>8}")
        click.echo("-" * 85)
        for k, v in sorted(endpoints.items(), key=lambda x: -x[1]["requests"]):
            click.echo(
                f"{k:<50} {v['requests']:>6} {v['errors']:>6} "
                f"{v['avg_duration_ms']:>8.0f} {v['p95_duration_ms']:>8}"
            )


def main():
    # Ensure the project root is in sys.path so `src.matclaw.*` imports resolve
    # when the CLI is invoked from anywhere (not just the project directory).
    import sys, pathlib
    _project_root = pathlib.Path(__file__).resolve().parents[4]  # …/MatClaw
    _project_root_str = str(_project_root)
    if _project_root_str not in sys.path:
        sys.path.insert(0, _project_root_str)
    cli()


if __name__ == "__main__":
    main()
