from __future__ import annotations

import os


def run() -> None:
    if not os.getenv("CEREBRAS_API_KEY"):
        print(
            "CEREBRAS_API_KEY is not set. Run the local demo without Cerebras or set your Cerebras key."
        )
        return
    from .integrations.cerebras_client import CerebrasLLMClient

    client = CerebrasLLMClient()
    print(f"Running Cerebras demo with {client.model}; LoopGuard remains local.")
