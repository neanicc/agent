from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import os
from collections.abc import Callable, Sequence
from typing import Any

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from .repair_temporal import ACTIVITIES, TemporalRepairWorkflow

ActivityFactory = Callable[[], Sequence[Callable[..., Any]]]
_SUPPORTING_ACTIVITIES = ("publish", "release_resources")


def load_activity_factory(reference: str) -> ActivityFactory:
    """Load an explicitly configured activity adapter without executing user input."""

    module_name, separator, attribute_name = reference.partition(":")
    if (
        separator != ":"
        or not module_name
        or not attribute_name
        or any(part.startswith("_") for part in module_name.split("."))
        or attribute_name.startswith("_")
    ):
        raise ValueError("activity factory must be a public module:function reference")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute_name, None)
    if factory is None or not callable(factory):
        raise ValueError("configured repair activity factory is not callable")
    return factory


def configured_activities(reference: str) -> tuple[Callable[..., Any], ...]:
    factory = load_activity_factory(reference)
    result = factory()
    if inspect.isawaitable(result) or isinstance(result, (str, bytes)) or not isinstance(
        result, Sequence
    ):
        raise ValueError("repair activity factory must return a synchronous sequence")
    activities = tuple(result)
    expected = {*ACTIVITIES, *_SUPPORTING_ACTIVITIES}
    names: list[str] = []
    for function in activities:
        if not callable(function):
            raise ValueError("repair activity factory returned a non-callable")
        definition = activity._Definition.must_from_callable(function)
        names.append(definition.name)
    if len(names) != len(set(names)) or set(names) != expected:
        raise ValueError(
            "repair activity factory must register exactly: " + ", ".join(sorted(expected))
        )
    return activities


async def serve() -> None:
    factory_reference = _required_env("LOOPGUARD_REPAIR_ACTIVITY_FACTORY")
    endpoint = _required_env("LOOPGUARD_API_TEMPORAL_ENDPOINT")
    namespace = _required_env("LOOPGUARD_TEMPORAL_NAMESPACE")
    task_queue = os.environ.get("LOOPGUARD_REPAIR_TASK_QUEUE", "loopguard-repair-v1").strip()
    if not task_queue or len(task_queue) > 256:
        raise ValueError("repair task queue must be non-empty and bounded")
    api_key = os.environ.get("LOOPGUARD_TEMPORAL_API_KEY", "").strip()
    if os.environ.get("LOOPGUARD_API_ENVIRONMENT") in {"staging", "production"} and not api_key:
        raise ValueError("hosted repair worker requires a Temporal API key")
    client = await Client.connect(
        endpoint,
        namespace=namespace,
        tls=True,
        api_key=api_key or None,
    )
    maximum = _bounded_positive_int(
        os.environ.get("LOOPGUARD_API_TEMPORAL_MAX_CONCURRENT_ACTIVITIES", "20"),
        maximum=1_000,
    )
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[TemporalRepairWorkflow],
        activities=list(configured_activities(factory_reference)),
        max_concurrent_activities=maximum,
    )
    await worker.run()


def check_configuration() -> None:
    configured_activities(_required_env("LOOPGUARD_REPAIR_ACTIVITY_FACTORY"))
    _required_env("LOOPGUARD_API_TEMPORAL_ENDPOINT")
    _required_env("LOOPGUARD_TEMPORAL_NAMESPACE")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated LoopGuard Temporal repair worker")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate worker adapter configuration without connecting to Temporal",
    )
    args = parser.parse_args()
    if args.check:
        check_configuration()
        return
    asyncio.run(serve())


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or "\x00" in value or len(value) > 2_048:
        raise ValueError(f"{name} must be explicitly configured and bounded")
    return value


def _bounded_positive_int(value: str, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("worker concurrency must be an integer") from exc
    if not 1 <= parsed <= maximum:
        raise ValueError(f"worker concurrency must be 1-{maximum}")
    return parsed


if __name__ == "__main__":
    main()
