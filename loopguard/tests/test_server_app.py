from fastapi.testclient import TestClient

from loopguard.judge import JudgeVerdict
from loopguard.providers.base import LLMResult
from loopguard.server.app import create_app


class _TC:
    def __init__(self, i):
        self.id = f"c{i}"
        self.type = "function"
        self.function = type("F", (), {"name": "read_file",
                                       "arguments": '{"path": "package.json"}'})()


class _QuickProvider:
    """Emits two tool calls then a final answer: a fast run that produces events."""

    model = "fake/model"

    def __init__(self):
        self.n = 0

    def complete(self, messages, *, tools=None, temperature=0.2, max_tokens=512,
                 response_format=None):
        self.n += 1
        if self.n <= 2:
            return LLMResult(text="", tool_calls=[_TC(self.n)], prompt_tokens=5,
                             completion_tokens=2, total_tokens=7, cost_usd=0.0001)
        return LLMResult(text="Found it.", prompt_tokens=5, completion_tokens=2, total_tokens=7)


class _Judge:
    def judge(self, events, task=None, detector=None):
        return JudgeVerdict(is_loop=True, reasoning="x")


def _app():
    return create_app(provider_factory=lambda model, provider: _QuickProvider(),
                      judge_factory=lambda provider, context=None: _Judge())


def test_health():
    client = TestClient(_app())
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert "version" in r.json()


def test_start_run_returns_id_and_lists():
    client = TestClient(_app())
    r = client.post("/runs", json={"mode": "auto", "model": "fake/model"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert client.get(f"/runs/{run_id}").status_code == 200
    assert any(s["id"] == run_id for s in client.get("/runs").json())


def test_unknown_run_404():
    client = TestClient(_app())
    assert client.get("/runs/nope").status_code == 404


def test_projects_endpoint_lists_real_projects():
    client = TestClient(_app())
    projects = client.get("/projects").json()
    ids = {p["id"] for p in projects}
    assert {"npm-manifest", "config-hunt", "two-agent-manifest", "custom"} <= ids
    custom = next(p for p in projects if p["id"] == "custom")
    assert custom["customizable"] is True and custom["hint"]
    multi = next(p for p in projects if p["id"] == "two-agent-manifest")
    assert multi["kind"] == "multi" and len(multi["agents"]) == 2


def test_unknown_project_is_400():
    client = TestClient(_app())
    r = client.post("/runs", json={"project_id": "does-not-exist"})
    assert r.status_code == 400 and "does-not-exist" in r.json()["detail"]


def test_run_detail_exposes_new_sections():
    client = TestClient(_app())
    run_id = client.post("/runs", json={"project_id": "npm-manifest", "mode": "auto"}).json()[
        "run_id"
    ]
    body = client.get(f"/runs/{run_id}").json()
    for key in ("project_id", "agents", "task", "auto_actions", "allowlist_log", "allowlist"):
        assert key in body
    assert body["project_id"] == "npm-manifest"


def test_allowlist_endpoint_starts_empty():
    client = TestClient(_app())
    assert client.get("/allowlist").json() == []


def test_websocket_streams_until_done():
    client = TestClient(_app())
    with client.websocket_connect("/runs/auto-run/ws?start=auto&model=fake/model") as ws:
        types = []
        for _ in range(20):
            msg = ws.receive_json()
            types.append(msg["type"])
            if msg["type"] == "done":
                break
        assert "event" in types
        assert types[-1] == "done"
