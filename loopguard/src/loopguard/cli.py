from __future__ import annotations

import os
from pathlib import Path
from enum import Enum

import typer
from rich import print
from rich.console import Console
from rich.table import Table

from .config import LoopGuardConfig
from .storage import read_jsonl

app = typer.Typer(help="LoopGuard semantic circuit breaker")


class DemoMode(str, Enum):
    guard = "guard"
    no_guard = "no-guard"


class DemoScenario(str, Enum):
    single = "single"
    pingpong = "pingpong"
    cerebras = "cerebras"


@app.command()
def demo(
    mode: DemoMode = DemoMode.guard,
    scenario: DemoScenario = DemoScenario.single,
):
    if scenario == DemoScenario.single:
        from .demos import run_broken_agent

        run_broken_agent(use_guard=mode != DemoMode.no_guard)
    elif scenario == DemoScenario.pingpong:
        from .demos import run_pingpong_demo

        run_pingpong_demo()
    else:
        from .cerebras_demo import run

        run(use_guard=mode != DemoMode.no_guard)


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
    console.print(
        f"[dim]{len(rows)} events · {total_tokens} tokens · ${total_cost:.4f}[/dim]"
    )


@app.command("init-config")
def init_config(path: Path = Path("loopguard.json")):
    path.write_text(LoopGuardConfig().model_dump_json(indent=2))
    print(f"Wrote {path}")
