import os


def run():
    if not os.getenv("CEREBRAS_API_KEY"):
        print(
            "CEREBRAS_API_KEY is not set. Run the local demo without Cerebras or set your Cerebras key."
        )
        return
    from loopguard.integrations.cerebras_client import CerebrasLLMClient

    print(f"Running Cerebras demo with {CerebrasLLMClient().model}; LoopGuard remains local.")


if __name__ == "__main__":
    run()
