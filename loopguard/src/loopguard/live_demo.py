from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .agent import run_agent
from .config import LoopGuardConfig
from .guard import LoopGuard
from .judge import LLMJudge
from .providers import make_provider
from .tools import LIST_DIR_SCHEMA, READ_FILE_SCHEMA, list_dir, read_file

console = Console()

_SYSTEM = (
    "You are a JavaScript repo inspector. This is an npm project. Find and read the "
    "package.json file using the read_file tool. The file must be named package.json — "
    "keep trying different directory paths until you find it."
)
_TASK = "Find package.json for this npm project."


def _repo_root() -> Path:
    # src/loopguard/live_demo.py -> repo root is three parents up.
    return Path(__file__).resolve().parents[2]


def run_live(
    model: str = "cerebras/gpt-oss-120b",
    provider: str = "auto",
    mode: str = "pause",
    root: str | None = None,
    use_guard: bool = True,
) -> None:
    try:
        prov = make_provider(model, provider)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    workspace = Path(root) if root else _repo_root()
    console.rule(f"[bold cyan]LoopGuard × {getattr(prov, 'model', model)}")

    guard = None
    if use_guard:
        judge = LLMJudge(prov)
        guard = LoopGuard(LoopGuardConfig(action=mode, max_tool_calls=15), judge=judge)
        console.print(
            "[dim]Agent will hunt for package.json (a Python repo). Watch it loop, "
            "then watch the judge route it to the real file.[/dim]\n"
        )
    else:
        console.print("[bold yellow]UNGUARDED run:[/bold yellow] no LoopGuard — burns tokens.\n")

    def on_event(step, name, args, output):
        is_err = output.startswith("Error:")
        path = args.get("path", "?")
        color = "red" if is_err else "green"
        console.print(f'[{color}][{step}] {name}("{path}") -> {output[:70]}[/{color}]')

    try:
        result = run_agent(
            prov,
            system=_SYSTEM,
            task=_TASK,
            tools_schema=[READ_FILE_SCHEMA, LIST_DIR_SCHEMA],
            tool_impls={
                "read_file": lambda path: read_file(path, root=workspace),
                "list_dir": lambda path=".": list_dir(path, root=workspace),
            },
            guard=guard,
            run_id="live-demo",
            agent_name="repo-inspector",
            max_steps=15,
            on_event=on_event,
        )
        if result.final_text:
            console.print(f"\n[bold green]Agent:[/bold green] {result.final_text}")
        if result.stopped_by_guard:
            console.print("[bold red]LoopGuard stopped the agent.[/bold red]")
    except Exception as exc:  # noqa: BLE001 - real API/runtime errors shown, not crashed
        console.print(f"[red]Run error: {exc}[/red]")
    finally:
        if guard is not None:
            s = guard.summary()
            console.print(
                f"\n[dim]Total: {s['events']} events · {s['tokens']} tokens · "
                f"${s['cost_usd']:.4f} (+ judge ${s['judge_cost_usd']:.4f})[/dim]"
            )
            guard.export_jsonl("runs/last.jsonl")
            console.print("[dim]Saved run to runs/last.jsonl[/dim]")
