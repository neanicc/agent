## LoopGuard verified pipeline repair

### Original reproduction

- Failure fingerprint: `ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff`
- Pipeline / step: `coordinate-import` / `normalize`
- Command (argument array): `[&quot;python&quot;, &quot;pipeline.py&quot;]`
- Evidence: `artifact-reproduction`

### Candidate matrix

| Candidate | Strategy | Replay | Regression | Security | Contract | Files | Lines |
|---|---|---:|---:|---:|---|---:|---:|
| candidate-boundary | normalize_ingestion_boundary | pass | pass | pass | compatible | 1 | 2 |

Winning rationale: boundary fix has the least verified risk

### Root cause and repair

The verified `candidate-boundary` candidate applies the
`normalize_ingestion_boundary`
boundary strategy. The original failure reproduced before repair, and the same replay passed after
the patch.

### Data contract delta

No contract changes detected.

### Verification evidence

- `artifact-evaluation`

Verification duration: 120 ms. Risk penalty: 0.
Winning patch: `sha256:a844b5c03a2f61b2a453d6a1a8f2d2d573ccf7a45b08d3cca49bff815f5ee8a1`.

### Rollback

Revert the single repair commit and rerun the original pipeline.
