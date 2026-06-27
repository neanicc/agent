from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import typer
from rich import print

from .config import LoopGuardConfig
from .storage import read_jsonl

app = typer.Typer(help="LoopGuard semantic circuit breaker")


@app.command()
def demo(
    mode: Literal["guard", "no-guard"] = "guard",
    scenario: Literal["single", "pingpong", "cerebras"] = "single",
):
    if scenario == "single":
        from examples.broken_agent import run

        run(use_guard=mode != "no-guard")
    elif scenario == "pingpong":
        from examples.broken_multi_agent import run

        run()
    else:
        if not os.getenv("CEREBRAS_API_KEY"):
            print(
                "CEREBRAS_API_KEY is not set. Run the local demo without Cerebras or set your Cerebras key."
            )
            print('Setup: export CEREBRAS_API_KEY="..." && pip install "loopguard[cerebras]"')
            raise typer.Exit(0)
        from examples.cerebras_tool_loop_demo import run

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
