from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from loopguard.decision import LoopDecision
from loopguard.diff import unified_diff
from loopguard.normalize import normalize_event

console = Console()


def show_warning(decision: LoopDecision) -> None:
    console.print(Panel.fit("[bold red]LoopGuard tripped[/bold red]", border_style="yellow"))
    console.print(f"Detector: [bold]{decision.detector}[/bold]")
    if decision.similarity is not None:
        console.print(f"Similarity: {decision.similarity:.2f}")
    events = decision.matching_events
    tools = sorted({event.tool_name for event in events if event.tool_name})
    if tools:
        console.print(f"Tool: {', '.join(tools)}")
    console.print(
        f"Estimated tokens: {sum(e.tokens for e in events)}  cost: ${sum(e.cost_usd for e in events):.3f}"
    )
    table = Table(title="Recent events")
    for col in ["#", "agent", "kind", "tool", "input/error"]:
        table.add_column(col)
    for i, e in enumerate(events, 1):
        table.add_row(
            str(i),
            e.agent,
            e.kind,
            e.tool_name or "",
            (e.error or e.input_text or e.output_text or "")[:80],
        )
    console.print(table)
    if len(events) >= 2:
        console.print("[bold]Diff:[/bold]")
        console.print(
            unified_diff(normalize_event(events[0]), normalize_event(events[-1]))
            or "(normalized states are identical)"
        )


def pause_for_action(decision: LoopDecision) -> LoopDecision:
    show_warning(decision)
    console.print("[t] terminate  [c] continue once  [a] allowlist  [i] inject correction")
    choice = Prompt.ask("Action", choices=["t", "c", "a", "i"], default="t")
    mapping = {"t": "terminate", "c": "continue_once", "a": "allowlist", "i": "inject"}
    decision.developer_action = mapping[choice]  # type: ignore[assignment]
    decision.allowed = choice in {"c", "a", "i"}
    if choice == "i":
        decision.suggested_message = Prompt.ask("Correction prompt")
        console.print("Injected correction accepted.")
    return decision
