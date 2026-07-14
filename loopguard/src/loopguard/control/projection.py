from loopguard.event import LoopEvent

from .events import ControlEvent, EventKind


def to_loop_event(event: ControlEvent) -> LoopEvent | None:
    if event.kind not in {EventKind.TOOL_CALL, EventKind.TOOL_RESULT}:
        return None

    payload = event.payload
    return LoopEvent(
        run_id=event.session.session_id,
        agent=str(payload.get("agent", event.source)),
        kind="tool_call" if event.kind == EventKind.TOOL_CALL else "tool_result",
        tool_name=payload.get("tool_name"),
        tool_args=payload.get("arguments"),
        error=payload.get("error"),
        tokens=int(payload.get("tokens", 0)),
        cost_usd=float(payload.get("cost_usd", 0.0)),
        metadata={
            "control_event_id": event.event_id,
            "repo_id": event.session.repo_id,
            "worktree_id": event.session.worktree_id,
        },
    )
