from __future__ import annotations

import json
import os
from pathlib import Path
from enum import Enum

import typer
from rich import print

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

        run()


@app.command("inspect")
def inspect_run(path: Path):
    rows = read_jsonl(path)
    print(f"Loaded {len(rows)} events from {path}")
    print(json.dumps(rows[-3:], indent=2, default=str))


@app.command("init-config")
def init_config(path: Path = Path("loopguard.json")):
    path.write_text(LoopGuardConfig().model_dump_json(indent=2))
    print(f"Wrote {path}")
