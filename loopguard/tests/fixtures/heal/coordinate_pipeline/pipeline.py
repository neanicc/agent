from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    source = Path("/fixture/rows.json")
    rows = json.loads(source.read_text())
    try:
        for row in rows:
            float(row["lat"])
    except (TypeError, ValueError) as exc:
        observed = {
            "event_id": "coordinate-reproduction",
            "repo_id": os.environ["LOOPGUARD_REPOSITORY_ID"],
            "revision": os.environ["LOOPGUARD_REPOSITORY_SHA"],
            "pipeline": "coordinate_pipeline",
            "step": "normalize_coordinates",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "frames": [{"file": "pipeline.py", "function": "main", "line": 14}],
            "schema": {"lat": "string", "lon": "float"},
            "stack_trace_artifact_id": "artifact-coordinate-reproduction",
            "fixture_artifact_id": "artifact-coordinate-fixture",
        }
        Path("/artifacts/observed-failure.json").write_text(json.dumps(observed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
