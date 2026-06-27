"""Demo project registry.

Each project is a REAL workspace under ``loopguard/demo_projects/`` plus an agent
"bias" (system prompt) that points a genuine LLM agent at the wrong file, while the
task is actually achievable by reading a real file that IS present. The agent loops
on its wrong assumption (real tokens, real cost — nothing hardcoded); LoopGuard trips;
the judge sees the real file in the agent's own ``list_dir`` output and redirects it.

No loop is scripted. The misdirection lives in the prompt; the recovery lives in the
filesystem. Swap the workspace/task and it still works on real code.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..tools import LIST_DIR_SCHEMA, READ_FILE_SCHEMA, list_dir, read_file

_DEMO_ROOT = Path(__file__).resolve().parent.parent / "demo_projects"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    system: str


@dataclass(frozen=True)
class Project:
    id: str
    label: str
    blurb: str
    kind: str  # "single" | "multi"
    task: str
    workspace: str
    agents: tuple[AgentSpec, ...]
    tools: tuple[str, ...] = ("read_file", "list_dir")
    model: str = "cerebras/gpt-oss-120b"
    max_steps: int = 18
    customizable: bool = False
    hint: str | None = None

    def to_summary(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "blurb": self.blurb,
            "kind": self.kind,
            "task": self.task,
            "agents": [a.name for a in self.agents],
            "customizable": self.customizable,
            "hint": self.hint,
        }


_TOOLS: dict[str, tuple[dict, Callable]] = {
    "read_file": (READ_FILE_SCHEMA, read_file),
    "list_dir": (LIST_DIR_SCHEMA, list_dir),
}


def workspace_path(project: Project) -> Path:
    return _DEMO_ROOT / project.workspace


def workspace_listing(project: Project, limit: int = 60) -> str:
    """Relative paths of the real files in a project's workspace.

    Passed to the judge as context so it can name the exact file the stuck agent
    should read instead of guessing the extension.
    """
    root = workspace_path(project)
    if not root.is_dir():
        return ""
    files = sorted(
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    return "\n".join(files[:limit])


def build_tools(project: Project) -> tuple[list[dict], dict[str, Callable[..., str]]]:
    """Bind the project's tools to its real workspace directory (sandboxed)."""
    root = workspace_path(project)
    schemas: list[dict] = []
    impls: dict[str, Callable[..., str]] = {}
    for name in project.tools:
        schema, fn = _TOOLS[name]
        schemas.append(schema)
        if name == "read_file":
            impls["read_file"] = lambda path, _root=root: read_file(path, root=_root)
        elif name == "list_dir":
            impls["list_dir"] = lambda path=".", _root=root: list_dir(path, root=_root)
    return schemas, impls


# "Stuck" projects expose read_file ONLY. With no directory listing to wander into, a real
# agent fixates on its wrong assumption and genuinely repeats near-identical reads, which
# trips the semantic detector reliably. The judge then names the real file from project
# context (these are all "wrong-ecosystem assumption about a well-known file" scenarios that
# an LLM reliably knows the answer to), and the agent reads it and finishes the task.
_READ_ONLY = ("read_file",)

PROJECTS: tuple[Project, ...] = (
    Project(
        id="npm-manifest",
        label="JS dev on a Python repo",
        blurb="An npm-minded agent hunts for package.json in a project that only has "
        "pyproject.toml. It loops on the missing manifest until the judge redirects it.",
        kind="single",
        task="Report this project's package name and version from its manifest.",
        workspace="npm-manifest",
        tools=_READ_ONLY,
        agents=(
            AgentSpec(
                name="repo-inspector",
                system=(
                    "You are an npm/JavaScript build expert. A project's name and version live "
                    "in its package.json manifest. The manifest MUST be named package.json — use "
                    "read_file and keep trying different directory paths until you find "
                    "package.json. Do not give up and do not assume another file."
                ),
            ),
        ),
    ),
    Project(
        id="requirements-hunt",
        label="requirements.txt that isn't there",
        blurb="An agent is sure dependencies live in requirements.txt and keeps probing for it; "
        "this project declares them in pyproject.toml.",
        kind="single",
        task="List this project's dependencies.",
        workspace="npm-manifest",  # real pyproject.toml with a dependencies list
        tools=_READ_ONLY,
        agents=(
            AgentSpec(
                name="dep-auditor",
                system=(
                    "You are a Python packaging expert. A project's dependencies are ALWAYS "
                    "listed in a file named requirements.txt. Use read_file and keep trying "
                    "different directory paths until you find requirements.txt. Do not give up."
                ),
            ),
        ),
    ),
    Project(
        id="config-hunt",
        label="Config in the wrong file",
        blurb="An agent insists configuration lives in settings.yaml and keeps probing for it, "
        "while the real settings sit in config.json.",
        kind="single",
        task="Report the database host and port from this service's configuration.",
        workspace="config-hunt",
        tools=_READ_ONLY,
        agents=(
            AgentSpec(
                name="config-reader",
                system=(
                    "You are a DevOps engineer. Service configuration is ALWAYS stored in a YAML "
                    "file named settings.yaml. Use read_file and keep trying different directory "
                    "paths until you find settings.yaml. Do not give up."
                ),
            ),
        ),
    ),
    Project(
        id="two-agent-manifest",
        label="Two agents, same wrong idea",
        blurb="A planner and a reviewer both believe the project uses package.json and pass the "
        "failure back and forth (A-B-A-B) until LoopGuard catches the oscillation.",
        kind="multi",
        task="Agree on this project's package name and version from its manifest.",
        workspace="npm-manifest",
        tools=_READ_ONLY,
        agents=(
            AgentSpec(
                name="planner",
                system=(
                    "You are the PLANNER on a two-agent team inspecting an npm project. The "
                    "manifest MUST be package.json. Use read_file to locate and read it; if a "
                    "path fails, try another package.json path. Do not give up."
                ),
            ),
            AgentSpec(
                name="reviewer",
                system=(
                    "You are the REVIEWER on a two-agent team inspecting an npm project. The "
                    "manifest MUST be package.json. Use read_file to verify it; if a path fails, "
                    "try another package.json path. Do not give up."
                ),
            ),
        ),
        max_steps=10,
    ),
    Project(
        id="custom",
        label="Custom task (sandbox)",
        blurb="Point a real agent at a sample repo and give it any task you type. It runs for "
        "real with read_file + list_dir; LoopGuard watches and steps in if it gets stuck.",
        kind="single",
        task="Report the project name and version, and summarize what the data pipeline does.",
        workspace="sandbox",
        tools=("read_file", "list_dir"),
        agents=(
            AgentSpec(
                name="agent",
                system=(
                    "You are a helpful software agent working inside a code repository. Use the "
                    "read_file and list_dir tools to inspect real files and complete the task. "
                    "Base every answer on file contents you actually read."
                ),
            ),
        ),
        customizable=True,
        hint='Try: "Read secrets.yaml and report the database password." (secrets.yaml does not '
        "exist here — the agent loops, then the judge redirects it to config.json.)",
    ),
)

_BY_ID = {p.id: p for p in PROJECTS}


def list_projects() -> list[dict]:
    return [p.to_summary() for p in PROJECTS]


def get_project(project_id: str) -> Project | None:
    return _BY_ID.get(project_id)
