from __future__ import annotations

import json
import os

from rich.console import Console

from .config import LoopGuardConfig
from .event import LoopEvent
from .exceptions import LoopDetectedError
from .guard import LoopGuard

console = Console()

_SYSTEM = (
    "You are a JavaScript repo inspector agent. This is an npm project. "
    "Your job is to find and read the package.json file. "
    "Use the read_file tool. The file must be named package.json — keep trying different directory paths until you find it."
)

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the project filesystem.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path to the file."}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    }
]

_FS_AFTER_CORRECTION = {
    "pyproject.toml": (
        '[project]\nname = "loopguard"\nversion = "0.1.0"\n'
        'description = "A local semantic circuit breaker for AI agents."'
    ),
}


def _read_file(path: str, correction_injected: bool = False) -> str:
    if correction_injected:
        for name, content in _FS_AFTER_CORRECTION.items():
            if name in path:
                return content
    return f"Error: {path} not found."


def run(use_guard: bool = True) -> None:
    if not os.getenv("CEREBRAS_API_KEY"):
        console.print(
            "[red]CEREBRAS_API_KEY is not set.[/red]\n"
            'Set it with: export CEREBRAS_API_KEY="..."\n'
            "Or run the local demo: loopguard demo"
        )
        return

    from .integrations.cerebras_client import CerebrasLLMClient

    try:
        client = CerebrasLLMClient()
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    console.rule(f"[bold cyan]LoopGuard × Cerebras  ({client.model})")
    if use_guard:
        console.print(
            "[dim]The agent will try to find package.json — and loop. Watch LoopGuard trip.[/dim]\n"
        )
        guard = LoopGuard(
            LoopGuardConfig(
                trip_count=3,
                semantic_threshold=0.86,
                action="pause",
                enable_budget=True,
                max_tool_calls=15,
            )
        )
    else:
        console.print(
            "[bold yellow]UNGUARDED run:[/bold yellow] no LoopGuard — the agent will loop "
            "and burn tokens until the step cap.\n"
        )
        # Detectors disabled so the guard only records events and never trips.
        guard = LoopGuard(
            LoopGuardConfig(
                enable_exact=False,
                enable_semantic=False,
                enable_pingpong=False,
                enable_budget=False,
            )
        )

    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": "Find package.json for this npm project."},
    ]

    correction_injected = False

    try:
        _run_loop(client, guard, messages, correction_injected, use_guard)
    finally:
        guard.export_jsonl("runs/last.jsonl")


def _run_loop(
    client,
    guard: LoopGuard,
    messages: list[dict],
    correction_injected: bool,
    use_guard: bool,
) -> None:
    for step in range(1, 14):
        try:
            response = client.chat(messages, tools=_TOOLS)
        except Exception as exc:
            console.print(f"[red]API error: {exc}[/red]")
            return

        msg = response.choices[0].message

        assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if not msg.tool_calls:
            console.print(f"\n[bold green]Agent:[/bold green] {msg.content}")
            break

        correction: str | None = None
        tokens = getattr(getattr(response, "usage", None), "total_tokens", 0) or 0
        cost = round(0.0004 * step, 4)

        for tc in msg.tool_calls:
            raw_args = tc.function.arguments
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            path = args.get("path", "?")
            result = _read_file(path, correction_injected=correction_injected)
            is_err = result.startswith("Error:")

            console.print(
                f"[{'red' if is_err else 'green'}]"
                f"[{step}] read_file(\"{path}\") → {result[:70]}"
                f"[/{'red' if is_err else 'green'}]"
            )

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            event = LoopEvent(
                run_id="cerebras-demo",
                agent="repo-inspector",
                kind="tool_call",
                tool_name=tc.function.name,
                tool_args=args,
                output_text=None if is_err else result,
                error=result if is_err else None,
                tokens=tokens,
                cost_usd=cost,
            )
            try:
                decision = guard.observe(event)
                if use_guard and decision.suggested_message:
                    correction = decision.suggested_message
                elif use_guard and decision.tripped and not decision.allowed:
                    console.print("[bold red]LoopGuard terminated the agent.[/bold red]")
                    _print_summary(guard)
                    return
            except LoopDetectedError:
                console.print("[bold red]LoopGuard raised: agent terminated.[/bold red]")
                _print_summary(guard)
                return

        if correction:
            correction_injected = True
            messages.append({"role": "user", "content": correction})
            console.print(f"\n[bold green]Injected correction:[/bold green] {correction}\n")

    _print_summary(guard)
    if use_guard:
        console.print("\n[bold green]Demo complete.[/bold green]")
    else:
        console.print("\n[bold yellow]Unguarded run complete — burned tokens with no protection.[/bold yellow]")
    console.print("[dim]Saved run to runs/last.jsonl[/dim]")


def _print_summary(guard: LoopGuard) -> None:
    s = guard.summary()
    console.print(
        f"\n[dim]Total: {s['events']} events · {s['tokens']} tokens · ${s['cost_usd']:.4f}[/dim]"
    )
