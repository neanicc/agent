"""Acme API entrypoint. Configuration is loaded at startup from config.json."""
import json
from pathlib import Path


def load_config() -> dict:
    return json.loads((Path(__file__).parent / "config.json").read_text())
