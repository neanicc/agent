# Shared Context and Multi-Agent Coordination Implementation Plan

> **For implementation agents:** Execute this plan task-by-task and track every checkbox. In Codex, use the native plan, debugging, review, and verification tools available in the host. In Claude Code, use `superpowers:subagent-driven-development` or `superpowers:executing-plans` when installed. A missing named workflow is never a blocker; perform the equivalent TDD and verification steps directly.

> **Command convention:** Resolve one absolute, supported virtualenv interpreter as `$PY`. In every shell snippet, read bare `python` as `$PY`, `python -m pip` as `$PY -m pip`, and `ruff` as `$PY -m ruff`; never assume those executables are on `PATH`.

**Goal:** Detect repository changes from every actor, maintain cursor-based shared state, provide compact handoffs, and warn concurrent agents before their work overlaps.

**Architecture:** A Change Journal derives repository state from control events and filesystem reconciliation. Agents work in separate worktrees, acquire advisory file/symbol leases, consume bounded digests, and fetch exact detail through MCP.

**Tech Stack:** Python 3.11+, SQLite, Git CLI, watchfiles, tree-sitter-language-pack, MCP Python SDK, pytest

All helpers shown in test snippets (`make_git_repo`, `write`, `clock`, `change_records`,
`context_fixture`, and MCP invocation helpers) must be implemented in
`loopguard/tests/context/conftest.py` or an explicitly listed test-support module. No committed test
may depend on an undefined illustrative helper.

---

### Task 1: Persist normalized change records

**Files:**
- Create: `loopguard/src/loopguard/context/__init__.py`
- Create: `loopguard/src/loopguard/context/models.py`
- Create: `loopguard/src/loopguard/context/journal.py`
- Create: `loopguard/tests/context/conftest.py`
- Test: `loopguard/tests/context/test_journal.py`

- [ ] **Step 1: Write failing cursor and provenance tests**

```python
from loopguard.context.journal import ChangeJournal
from loopguard.context.models import ChangeObservation


def test_journal_orders_changes_and_preserves_actor(tmp_path):
    journal = ChangeJournal(tmp_path / "context.db")
    first = journal.record(ChangeObservation(
        observation_id="obs_1", control_event_id="evt_1", repo_id="r", repo_seq=1,
        worktree_id="w1", path="src/a.py",
        actor="codex:s1", before_hash="old", after_hash="new",
    ))
    second = journal.record(ChangeObservation(
        observation_id="obs_2", control_event_id="evt_2", repo_id="r", repo_seq=2,
        worktree_id="w2", path="src/b.py",
        actor="claude:s2", before_hash=None, after_hash="hash",
    ))
    assert first.repo_seq == 1 and second.repo_seq == 2
    assert journal.since("r", repo_seq=1)[0].actor == "claude:s2"
```

- [ ] **Step 2: Verify missing context package**

Run: `cd loopguard && python -m pytest -q tests/context/test_journal.py`
Expected: FAIL with missing module.

- [ ] **Step 3: Implement `ChangeRecord` and SQLite journal**

```python
class ChangeObservation(BaseModel):
    observation_id: str
    control_event_id: str
    repo_id: str
    repo_seq: int
    worktree_id: str
    path: str
    actor: str
    before_hash: str | None
    after_hash: str | None
    patch: str | None = None
    symbols: list[str] = Field(default_factory=list)
    verification_ids: list[str] = Field(default_factory=list)


class ChangeRecord(BaseModel):
    record_id: str
    content_fingerprint: str
    repo_id: str
    repo_seq: int
    worktree_id: str
    path: str
    actor: str
    before_hash: str | None
    after_hash: str | None
    patch: str | None = None
    symbols: list[str] = Field(default_factory=list)
    verification_ids: list[str] = Field(default_factory=list)
    provenance: list[ChangeObservation] = Field(default_factory=list)


class ContextCheckpoint(BaseModel):
    repo_id: str
    repo_seq: int
    commit_sha: str | None
    worktree_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

Consume the `repo_seq` assigned by `EventStore`; do not allocate or rename another generic cursor.
`observation_id` is unique per source observation. `content_fingerprint` describes the path/hash
transition and is not globally unique. Store observations in a related table and transactionally
merge hook/filesystem provenance only when repository, worktree, path, hashes, and the active
reconciliation window identify the same physical transition. The same content transition repeated
later produces a new `record_id` and `repo_seq`, preserving history.

Index `(repo_id, repo_seq)`, `(repo_id, path)`, content fingerprint, control event, actor, and
verification links. The journal is a registered daemon handler from the foundation composition
root; add an integration test proving a persisted `file.changed` event reaches the journal once,
including crash replay.

- [ ] **Step 4: Run journal tests**

Run: `cd loopguard && python -m pytest -q tests/context/test_journal.py`
Expected: PASS.

- [ ] **Step 5: Commit the Change Journal**

```bash
git add loopguard/src/loopguard/context loopguard/tests/context
git commit -m "feat: add cursor-based change journal"
```

### Task 2: Reconcile hook and filesystem changes

**Files:**
- Create: `loopguard/src/loopguard/context/hashing.py`
- Create: `loopguard/src/loopguard/context/watcher.py`
- Create: `loopguard/src/loopguard/context/git_state.py`
- Modify: `loopguard/pyproject.toml`
- Test: `loopguard/tests/context/test_watcher.py`

- [ ] **Step 1: Write failing deduplication tests**

```python
from loopguard.context.watcher import ChangeReconciler


def test_hook_and_filesystem_event_become_one_change(tmp_path):
    repo = make_git_repo(tmp_path)
    reconciler = ChangeReconciler(repo)
    write(repo / "src/a.py", "value = 2\n")
    hook = reconciler.from_hook("src/a.py", actor="codex:s1")
    fs = reconciler.from_filesystem("src/a.py")
    record = reconciler.merge([hook, fs])
    assert hook.observation_id != fs.observation_id
    assert hook.content_fingerprint == fs.content_fingerprint
    assert {item.actor for item in record.provenance} == {"codex:s1", "filesystem"}
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/context/test_watcher.py`
Expected: FAIL because `ChangeReconciler` does not exist.

- [ ] **Step 3: Implement content-addressed reconciliation**

Add `watchfiles>=0.24` and `tree-sitter-language-pack` to the `context` optional dependency and
extend the `all-dev` extra plus its metadata test.
Compute a content fingerprint:

```python
content_fingerprint = sha256(
    f"{repo_id}\0{worktree_id}\0{relative_path}\0{before_hash}\0{after_hash}".encode()
).hexdigest()
```

Generate `observation_id` from source event identity, not content. Use Git blob hashes when
available and SHA-256 for untracked content. Debounce filesystem events for 75 ms, but immediately
accept hook provenance. Ignore `.git`, LoopGuard state, build output, and configured binary/size
exclusions. Run a full `git status --porcelain=v2 -z` reconciliation after watcher overflow or
daemon restart.

Restart reconciliation establishes the current tree state and emits explicit
`reconciliation.current_state` observations for drift since the last checkpoint. It does not
invent exact historical actors, timestamps, or intermediate changes that were never observed.
Tests distinguish “current state recovered” from “history complete,” cover atomic-save rename
patterns, repeated identical transitions at different times, symlinks, case-folding filesystems,
watcher overflow, repo/worktree removal, and files changing during reconciliation.

- [ ] **Step 4: Run watcher tests**

Run: `cd loopguard && python -m pytest -q tests/context/test_watcher.py`
Expected: PASS for modify, create, delete, rename, duplicate, overflow, and binary exclusion.

- [ ] **Step 5: Commit change reconciliation**

```bash
git add loopguard/src/loopguard/context loopguard/tests/context loopguard/pyproject.toml
git commit -m "feat: reconcile agent and filesystem changes"
```

### Task 3: Index symbols and change impact edges

**Files:**
- Create: `loopguard/src/loopguard/context/symbols.py`
- Create: `loopguard/src/loopguard/context/index.py`
- Modify: `loopguard/pyproject.toml`
- Test: `loopguard/tests/context/test_symbols.py`
- Test fixtures: `loopguard/tests/fixtures/context/python_repo/`
- Test fixtures: `loopguard/tests/fixtures/context/typescript_repo/`

- [ ] **Step 1: Write failing Python and TypeScript symbol tests**

```python
from loopguard.context.symbols import SymbolExtractor


def test_extracts_definitions_and_imports():
    extractor = SymbolExtractor()
    result = extractor.extract("src/service.py", """
from app.models import User
def load_user(user_id: str) -> User:
    return User.get(user_id)
""")
    assert result.definitions == ["load_user"]
    assert "app.models.User" in result.imports
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/context/test_symbols.py`
Expected: FAIL because the extractor is missing.

- [ ] **Step 3: Implement bounded language extractors**

Use Python `ast` for Python. Use `tree-sitter-language-pack` for TypeScript/TSX. Emit:

```python
class SymbolSnapshot(BaseModel):
    path: str
    language: str
    definitions: list[str]
    imports: list[str]
    test_names: list[str]
```

Unknown languages emit an empty snapshot rather than invoking a model. Store symbol-to-path and
import edges in SQLite. Re-index only changed files.

- [ ] **Step 4: Run extractor and index tests**

Run: `cd loopguard && python -m pytest -q tests/context/test_symbols.py`
Expected: PASS.

- [ ] **Step 5: Commit the symbol index**

```bash
git add loopguard/src/loopguard/context loopguard/tests/context \
  loopguard/tests/fixtures/context loopguard/pyproject.toml
git commit -m "feat: index changed symbols and imports"
```

### Task 4: Add advisory leases and collision decisions

**Files:**
- Create: `loopguard/src/loopguard/context/leases.py`
- Test: `loopguard/tests/context/test_leases.py`

- [ ] **Step 1: Write failing overlap and expiry tests**

```python
from datetime import timedelta

from loopguard.context.leases import LeaseManager, LeaseScope


def test_overlapping_file_leases_warn_other_session(tmp_path, clock):
    manager = LeaseManager(tmp_path / "leases.db", clock=clock)
    manager.acquire("r", "s1", LeaseScope.file("src/auth.py"), ttl=timedelta(minutes=10))
    result = manager.acquire(
        "r", "s2", LeaseScope.symbol("src/auth.py", "login"), ttl=timedelta(minutes=10)
    )
    assert result.status == "conflict"
    assert result.owner_session_id == "s1"


def test_expired_lease_does_not_conflict(tmp_path, clock):
    manager = LeaseManager(tmp_path / "leases.db", clock=clock)
    manager.acquire("r", "s1", LeaseScope.file("src/a.py"), ttl=timedelta(seconds=1))
    clock.advance(seconds=2)
    assert manager.acquire("r", "s2", LeaseScope.file("src/a.py")).status == "acquired"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/context/test_leases.py`
Expected: FAIL because `LeaseManager` is missing.

- [ ] **Step 3: Implement transactional lease acquisition**

File scope overlaps every symbol in that file. Symbol scope overlaps the same normalized symbol
and any file lease. Store owner, acquired time, expiry, and enforcement mode. Return
`acquired`, `renewed`, or `conflict`; never steal an unexpired lease. Release all session leases on
clean stop and expire abandoned leases by time.

- [ ] **Step 4: Run concurrency tests**

Run: `cd loopguard && python -m pytest -q tests/context/test_leases.py`
Expected: PASS, including two-thread acquisition where exactly one contender succeeds.

- [ ] **Step 5: Commit coordination leases**

```bash
git add loopguard/src/loopguard/context/leases.py loopguard/tests/context/test_leases.py
git commit -m "feat: warn on overlapping agent work"
```

### Task 5: Generate bounded context digests and handoffs

**Files:**
- Create: `loopguard/src/loopguard/context/digest.py`
- Create: `loopguard/src/loopguard/context/handoff.py`
- Test: `loopguard/tests/context/test_digest.py`
- Test: `loopguard/tests/context/test_handoff.py`

- [ ] **Step 1: Write failing deterministic-budget tests**

```python
from loopguard.context.digest import DigestBuilder, DigestBudget


def test_digest_is_deterministic_and_bounded(change_records):
    builder = DigestBuilder()
    first = builder.build(change_records, since=4, budget=DigestBudget(max_chars=800))
    second = builder.build(reversed(change_records), since=4, budget=DigestBudget(max_chars=800))
    assert first.text == second.text
    assert len(first.text) <= 800
    assert first.next_repo_seq == max(record.repo_seq for record in change_records)
    assert "hidden reasoning" not in first.text.lower()
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/context/test_digest.py tests/context/test_handoff.py`
Expected: FAIL because digest and handoff types are missing.

- [ ] **Step 3: Implement relevance ordering and structured handoff**

Order digest entries by collision, failing verification, direct dependency, then recency. Render
paths, actors, symbols, verification state, and cursors. Truncate whole entries rather than
cutting JSON or paths.

Define:

```python
class Handoff(BaseModel):
    goal: str
    accepted_decisions: list[str]
    changed_paths: list[str]
    verification_ids: list[str]
    unresolved: list[str]
    risks: list[str]
    repo_seq: int
    commit_sha: str | None
```

Handoffs reject fields named `reasoning`, `chain_of_thought`, or `scratchpad`.

- [ ] **Step 4: Run digest and handoff tests**

Run: `cd loopguard && python -m pytest -q tests/context/test_digest.py tests/context/test_handoff.py`
Expected: PASS.

- [ ] **Step 5: Commit bounded context artifacts**

```bash
git add loopguard/src/loopguard/context loopguard/tests/context
git commit -m "feat: build bounded agent context handoffs"
```

### Task 6: Expose exact context through MCP

**Files:**
- Create: `loopguard/src/loopguard/context/mcp_server.py`
- Create: `loopguard/src/loopguard/context/capability.py`
- Modify: `loopguard/pyproject.toml`
- Test: `loopguard/tests/context/test_mcp_server.py`
- Modify: `loopguard/src/loopguard/cli.py`

- [ ] **Step 1: Write failing MCP tool tests**

```python
from loopguard.context.mcp_server import build_context_server


def test_get_changes_since_returns_cursor_and_records(context_fixture):
    server = build_context_server(
        context_fixture.services,
        capability=context_fixture.capability(repo_id="r", session_id="s"),
    )
    result = invoke_tool(server, "get_changes_since", {"repo_seq": 3})
    assert result["next_repo_seq"] == 5
    assert [item["path"] for item in result["changes"]] == ["src/a.py", "tests/test_a.py"]
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/context/test_mcp_server.py`
Expected: FAIL because the MCP server is absent.

- [ ] **Step 3: Implement the narrow MCP API**

Expose:

- `get_repo_state()`
- `get_changes_since(repo_seq, limit=50)`
- `get_verification_status(verification_id=None)`
- `claim_work(scopes, ttl_seconds)`
- `release_work(scopes=None)`
- `create_handoff()`
- `read_handoff(handoff_id)`

Do not accept caller-supplied `repo_id` or `session_id`. The daemon launches each stdio MCP process
with a short-lived, unforgeable local capability bound to one host/repository/session, allowed
tools, expiry, and nonce. The server loads it from an inherited descriptor or owner-only file,
validates it before startup, consumes/rotates it as configured, and injects the bound identities
into service calls. Reject cross-repository handoff IDs and verification IDs. Redact tool output
and bound list sizes/patch bytes.

Add `loopguard context-mcp` as a stdio entrypoint used only through daemon launch. Add `mcp>=1.0`
to the `context` optional dependency and refresh the `all-dev` lock/metadata assertion. Register the
context journal, watcher, index, lease manager,
digest service, worktree manager, and MCP launcher in `DaemonServices`; add a daemon integration
test so these modules cannot exist without being active.

- [ ] **Step 4: Run all context tests**

Run: `cd loopguard && python -m pytest -q tests/context`
Expected: PASS.

- [ ] **Step 5: Commit the context MCP server**

```bash
git add loopguard/src/loopguard/context loopguard/tests/context \
  loopguard/pyproject.toml loopguard/src/loopguard/cli.py
git commit -m "feat: expose shared agent context through mcp"
```

### Task 7: Require separate worktrees for managed concurrent runs

**Files:**
- Create: `loopguard/src/loopguard/context/worktrees.py`
- Test: `loopguard/tests/context/test_worktrees.py`
- Modify: `loopguard/src/loopguard/control/daemon.py`

- [ ] **Step 1: Write failing worktree allocation tests**

```python
from loopguard.context.worktrees import WorktreeManager


def test_concurrent_sessions_receive_different_worktrees(git_repo, tmp_path):
    manager = WorktreeManager(root=tmp_path / "worktrees")
    first = manager.allocate(git_repo, session_id="s1")
    second = manager.allocate(git_repo, session_id="s2")
    assert first.path != second.path
    assert first.branch == "loopguard/s1"
    assert second.branch == "loopguard/s2"
```

- [ ] **Step 2: Verify failure**

Run: `cd loopguard && python -m pytest -q tests/context/test_worktrees.py`
Expected: FAIL because `WorktreeManager` is missing.

- [ ] **Step 3: Implement non-destructive allocation and cleanup**

Use `git worktree add -b loopguard/<session-id> <path> <base-sha>`. Validate clean allocation paths,
but never interpolate raw repository/session text into a branch, path, or command. Derive a short
slug plus stable hash, pass subprocess arguments without a shell, contain real paths beneath the
configured worktree root, reject symlink/path traversal, and verify the requested base SHA belongs
to the bound repository. Persist a lease/allocation state machine so retry, daemon restart,
partially-created branch, failed checkout, branch collision, and cleanup are idempotent.

Never delete a worktree with uncommitted/untracked changes, an active process, or an unmerged
branch; quarantine abandoned worktrees for user review. Managed runs with the same repo ID must not
receive the primary working tree concurrently.

Attached sessions cannot be transparently moved out of a worktree they already use. Detect
attached sessions that share the primary writable tree, emit a collision risk, and apply the
configured warn/block policy before a mutating tool. Guarantee separate worktrees only for managed
sessions; capability/docs must not imply the guarantee for ordinary attached vendor sessions.

- [ ] **Step 4: Run Git-backed tests**

Run: `cd loopguard && python -m pytest -q tests/context/test_worktrees.py`
Expected: PASS.

- [ ] **Step 5: Commit worktree isolation**

```bash
git add loopguard/src/loopguard/context loopguard/tests/context \
  loopguard/src/loopguard/control/daemon.py
git commit -m "feat: isolate concurrent managed agents in worktrees"
```

## Completion gate

Run:

```bash
cd loopguard
python -m pytest -q tests/context tests/control tests/adapters
```

Expected: all tests pass; restarting the daemon recovers current state without fabricating exact
history; digest output stays within budget; MCP callers cannot spoof repository/session identity;
worktree allocation survives partial failure safely; two managed sessions cannot share a writable
worktree; and attached sessions sharing the primary tree receive the configured explicit warning
or block.
