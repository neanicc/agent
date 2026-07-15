from __future__ import annotations

import os
import sys


def main() -> None:
    try:
        cpu_seconds, memory_bytes, file_bytes, command = _parse(sys.argv[1:])
        if os.name == "posix":
            _apply_posix_limits(cpu_seconds, memory_bytes, file_bytes)
        os.execve(command[0], command, dict(os.environ))
    except (OSError, ValueError):
        print("LoopGuard verification wrapper could not start the approved command", file=sys.stderr)
        raise SystemExit(126) from None


def _apply_posix_limits(cpu_seconds: int, memory_bytes: int, file_bytes: int) -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    if sys.platform != "darwin" and hasattr(resource, "RLIMIT_AS"):
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def _parse(arguments: list[str]) -> tuple[int, int, int, list[str]]:
    if len(arguments) < 8 or arguments[0] != "--cpu":
        raise ValueError("invalid wrapper arguments")
    if arguments[2] != "--memory" or arguments[4] != "--file" or arguments[6] != "--":
        raise ValueError("invalid wrapper arguments")
    values = tuple(int(arguments[index]) for index in (1, 3, 5))
    if any(value <= 0 for value in values) or not arguments[7]:
        raise ValueError("invalid wrapper limit")
    return values[0], values[1], values[2], arguments[7:]


if __name__ == "__main__":
    main()
