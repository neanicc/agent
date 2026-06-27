from __future__ import annotations

import os
from pathlib import Path
from enum import Enum

import typer
from rich.console import Console
from rich.table import Table

from .config import LoopGuardConfig
from .storage import read_jsonl

app = typer.Typer(help="LoopGuard semantic circuit breaker")


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a local .env (cwd or repo root) without a dependency.

    Existing environment variables win, so an explicit `export` always overrides .env.
    """
    here = Path.cwd()
    for base in (here, *here.parents):
        env = base / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
            return


class DemoMode(str, Enum):
    pause = "pause"
    flag = "flag"
    auto = "auto"
    warn = "warn"


class DemoScenario(str, Enum):
    single = "single"
    pingpong = "pingpong"
    cerebras = "cerebras"


@app.command()
def demo(
    live: bool = typer.Option(False, "--live", help="Run a real LLM agent (needs an API key)."),
    model: str = typer.Option("cerebras/gpt-oss-120b", help="Model id (litellm routing string)."),
    provider: str = typer.Option("auto", help="auto | litellm | cerebras"),
    mode: DemoMode = typer.Option(DemoMode.pause, help="pause | flag | auto | warn"),
    scenario: DemoScenario = DemoScenario.single,
    guard: bool = typer.Option(True, "--guard/--no-guard"),
):
    if scenario == DemoScenario.pingpong:
        from .demos import run_pingpong_demo

        run_pingpong_demo(action=mode.value)
        return
    if live or scenario == DemoScenario.cerebras:
        _load_dotenv()
        from .live_demo import run_live

        run_live(model=model, provider=provider, mode=mode.value, use_guard=guard)
        return
    from .demos import run_broken_agent

    run_broken_agent(use_guard=guard, action=mode.value)


@app.command("projects")
def projects_cmd():
    """List the demo projects an agent can really run."""
    from .server.projects import PROJECTS

    console = Console()
    table = Table(title="LoopGuard demo projects")
    table.add_column("id", style="cyan")
    table.add_column("kind")
    table.add_column("what happens")
    for p in PROJECTS:
        table.add_row(p.id, p.kind, p.blurb)
    console.print(table)


@app.command("run")
def run_project(
    project_id: str = typer.Argument(..., help="Project id (see `loopguard projects`)."),
    mode: DemoMode = typer.Option(DemoMode.pause, help="pause | flag | auto | warn"),
    model: str = typer.Option("cerebras/gpt-oss-120b", help="Model id."),
    provider: str = typer.Option("auto", help="auto | litellm | cerebras"),
    task: str = typer.Option(None, help="Override the project task (custom project)."),
):
    """Run a real agent on a demo project and guard it live in the terminal.

    In `pause` mode you get the full [t]erminate / [c]ontinue / [a]llowlist / [i]nject
    flow; `auto` applies the judge's fix; `flag` reports without blocking.
    """
    _load_dotenv()
    from .agent import run_agent, run_multi_agent
    from .guard import LoopGuard
    from .judge import LLMJudge
    from .providers import make_provider
    from .server.projects import build_tools, get_project, workspace_listing

    console = Console()
    proj = get_project(project_id)
    if proj is None:
        from .server.projects import PROJECTS

        console.print(f"[red]Unknown project {project_id!r}.[/red] Try one of: "
                      + ", ".join(p.id for p in PROJECTS))
        raise typer.Exit(1)

    try:
        prov = make_provider(model, provider)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    guard = LoopGuard(
        LoopGuardConfig(action=mode.value, max_tool_calls=15),
        judge=LLMJudge(prov, context=workspace_listing(proj)),
    )
    schemas, impls = build_tools(proj)
    the_task = task or proj.task
    console.rule(f"[bold cyan]{proj.label} · {proj.kind} · {mode.value}")
    console.print(f"[dim]{proj.blurb}[/dim]\n[bold]Task:[/bold] {the_task}\n")

    def on_event(step, name, args, output):
        is_err = output.startswith("Error:")
        color = "red" if is_err else "green"
        path = args.get("path", "")
        console.print(f'[{color}][{step}] {name}({path!r}) -> {output[:70]}[/{color}]')

    try:
        if proj.kind == "multi":
            result = run_multi_agent(
                prov, agents=[(a.name, a.system) for a in proj.agents], task=the_task,
                tools_schema=schemas, tool_impls=impls, guard=guard, run_id=proj.id,
                max_steps=proj.max_steps, on_event=on_event,
            )
        else:
            agent = proj.agents[0]
            result = run_agent(
                prov, system=agent.system, task=the_task, tools_schema=schemas,
                tool_impls=impls, guard=guard, run_id=proj.id, agent_name=agent.name,
                max_steps=proj.max_steps, on_event=on_event,
            )
        if result.final_text:
            console.print(f"\n[bold green]Agent:[/bold green] {result.final_text}")
        if result.stopped_by_guard:
            console.print("[bold red]LoopGuard stopped the agent.[/bold red]")
    except Exception as exc:  # noqa: BLE001 - show real API/runtime errors, don't crash
        console.print(f"[red]Run error: {exc}[/red]")
        raise typer.Exit(1)
    finally:
        allow = guard.allowlisted_tools()
        if allow:
            console.print(f"[yellow]Allowlisted tools:[/yellow] {', '.join(allow)}")
        s = guard.summary()
        console.print(
            f"\n[dim]Total: {s['events']} events · {s['tokens']} tokens · "
            f"${s['cost_usd']:.4f} (+ judge ${s['judge_cost_usd']:.4f})[/dim]"
        )


@app.command("inspect")
def inspect_run(path: Path):
    console = Console()
    rows = read_jsonl(path)
    if not rows:
        console.print(f"[yellow]No events found in {path}.[/yellow]")
        return

    table = Table(title=f"LoopGuard run: {path}")
    table.add_column("#", justify="right")
    table.add_column("agent")
    table.add_column("kind")
    table.add_column("tool")
    table.add_column("input/error")
    table.add_column("tokens", justify="right")
    table.add_column("cost", justify="right")

    total_tokens = 0
    total_cost = 0.0
    for i, row in enumerate(rows, start=1):
        tokens = row.get("tokens", 0) or 0
        cost = row.get("cost_usd", 0) or 0
        total_tokens += tokens
        total_cost += cost
        detail = (row.get("error") or row.get("input_text") or row.get("output_text") or "")[:60]
        table.add_row(
            str(i),
            row.get("agent", ""),
            row.get("kind", ""),
            row.get("tool_name") or "",
            detail,
            str(row.get("tokens", 0)),
            f"${row.get('cost_usd', 0):.4f}",
        )

    console.print(table)
    console.print(f"[dim]{len(rows)} events · {total_tokens} tokens · ${total_cost:.4f}[/dim]")


@app.command("init-config")
def init_config(path: Path = Path("loopguard.json")):
    path.write_text(LoopGuardConfig().model_dump_json(indent=2))
    print(f"Wrote {path}")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000):
    _load_dotenv()
    import uvicorn

    from .server import create_app

    uvicorn.run(create_app(), host=host, port=port)
