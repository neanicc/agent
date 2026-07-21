# LoopGuard Control API

The hosted LoopGuard control plane accepts authenticated, redacted local events and provides
tenant-scoped monitoring and control APIs. The service is under active roadmap implementation;
the health and error-contract boundary is available first, while database, authentication, relay,
streaming, action, artifact, workflow, and client endpoints land in the following tasks.

For local development, install `.[dev]` and run `python -m pytest -q`. Production settings have no
usable secret defaults: external signing/KMS references, PostgreSQL, OIDC, storage, Temporal,
exact browser origins, and trusted proxy networks must all be configured explicitly.
