from __future__ import annotations

import argparse
from pathlib import Path

from typer.testing import CliRunner

from .cli import app


_COMMANDS = (
    ("loopguard", ()),
    ("loopguard quickstart", ("quickstart",)),
    ("loopguard setup", ("setup",)),
    ("loopguard uninstall", ("uninstall",)),
    ("loopguard sessions", ("sessions",)),
    ("loopguard feedback", ("feedback",)),
    ("loopguard daemon", ("daemon",)),
    ("loopguard daemon start", ("daemon", "start")),
    ("loopguard daemon status", ("daemon", "status")),
    ("loopguard daemon doctor", ("daemon", "doctor")),
    ("loopguard doctor", ("doctor",)),
    ("loopguard explain", ("explain",)),
    ("loopguard config", ("config",)),
    ("loopguard config path", ("config", "path")),
    ("loopguard config show", ("config", "show")),
    ("loopguard config validate", ("config", "validate")),
    ("loopguard integrations", ("integrations",)),
    ("loopguard integrations install", ("integrations", "install")),
    ("loopguard integrations install codex", ("integrations", "install", "codex")),
    ("loopguard integrations install claude", ("integrations", "install", "claude")),
    ("loopguard integrations verify", ("integrations", "verify")),
    ("loopguard integrations verify codex", ("integrations", "verify", "codex")),
    ("loopguard integrations verify claude", ("integrations", "verify", "claude")),
    ("loopguard integrations uninstall", ("integrations", "uninstall")),
    ("loopguard integrations uninstall codex", ("integrations", "uninstall", "codex")),
    ("loopguard integrations uninstall claude", ("integrations", "uninstall", "claude")),
    ("loopguard data", ("data",)),
    ("loopguard data purge", ("data", "purge")),
    ("loopguard dx", ("dx",)),
    ("loopguard dx report", ("dx", "report")),
    ("loopguard init-config", ("init-config",)),
    ("loopguard demo", ("demo",)),
    ("loopguard projects", ("projects",)),
    ("loopguard run", ("run",)),
    ("loopguard inspect", ("inspect",)),
    ("loopguard serve", ("serve",)),
)


def render_cli_reference() -> str:
    runner = CliRunner()
    sections = [
        "# CLI reference",
        "",
        "This file is generated from the installed Typer command tree. Do not edit help blocks by hand.",
        "Regenerate it with `python -m loopguard.cli_docs` from the package directory.",
        "",
    ]
    for label, command in _COMMANDS:
        result = runner.invoke(
            app,
            [*command, "--help"],
            color=False,
            terminal_width=100,
            prog_name="loopguard",
        )
        if result.exit_code != 0:
            raise RuntimeError(f"could not render help for {label}")
        help_text = "\n".join(line.rstrip() for line in result.stdout.splitlines())
        sections.extend((f"## `{label}`", "", "```text", help_text, "```", ""))
    return "\n".join(sections).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate LoopGuard CLI reference help.")
    parser.add_argument("--check", action="store_true", help="Fail when the output file drifts.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "docs/reference/cli.md",
    )
    arguments = parser.parse_args()
    rendered = render_cli_reference()
    if arguments.check:
        if not arguments.output.exists() or arguments.output.read_text() != rendered:
            raise SystemExit("CLI reference is out of date; run python -m loopguard.cli_docs")
        return
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered)


if __name__ == "__main__":
    main()
