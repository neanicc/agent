# Secure release process

LoopGuard releases are built from a reviewed tag by isolated jobs. Pull requests can build and
verify candidates, but cannot receive registry, signing, Apple, cloud, or production credentials.
Publishing requires a protected GitHub environment and a human approval.

## What a release contains

`artifacts/release/manifest.json` is the inventory for every output:

- the Python wheel and source distribution from `loopguard/dist`;
- the `@loopguard/playwright` npm tarball;
- control API, worker, and web OCI candidates plus the protected publish job's immutable ECR
  digest record;
- the iOS verification archive and checksum.

Each entry binds the exact build commit, platform, SHA-256, SPDX or CycloneDX SBOM, SLSA/in-toto
provenance, and Sigstore bundle. Paths are relative to the manifest and may not escape the release
directory. Images use `registry/repository@sha256:...`, never a mutable tag.

Verify a downloaded candidate with the expected release identity:

```bash
cosign version
scripts/verify_release.sh artifacts/release/manifest.json
```

The verifier checks every checksum and evidence binding, then calls `cosign verify-blob` or
`cosign verify` with the manifest's exact GitHub workflow identity and OIDC issuer. A missing tool,
missing evidence file, mismatched commit, wrong digest, symlink, traversal path, or failed
transparency/signature verification fails the entire release and lists all detected defects.

## CI trust boundaries

- All actions are pinned to full immutable commit SHAs and updated through reviewed dependency PRs.
- Workflow and job permissions start read-only. Only the approved publish job receives
  `id-token: write` and the minimum registry permission.
- Untrusted PR code never runs in `pull_request_target`, never receives secrets, and never shares a
  writable cache with a release job.
- Dependency audit, license policy, secret scanning, SAST, lockfile installs, container scanning,
  SBOM generation, and test suites must pass before candidate signing.
- Build outputs move between jobs only through checksummed artifacts. Release jobs rebuild from the
  reviewed tag and compare source commit and expected package ownership.

## Operator flow

1. Confirm the version, changelog, migration/rollback notes, capacity result, restore rehearsal, and
   dependency/license report.
2. Create a signed `vX.Y.Z` tag from a protected, green commit.
3. Let the release workflow build, scan, produce SBOM/provenance, keylessly sign, assemble the
   manifest, and run `verify_release.sh`.
4. Approve the protected release environment only after verifying the manifest and repository/tag
   identity.
5. Publish private hosted images by immutable ECR digest. Smoke-test installation and `/health`
   from a clean environment.
6. Attach the verified manifest and non-sensitive evidence to the release; retain rollback digests.

The registry names remain configurable until the owner reserves and verifies them. Public Python,
npm, and App Store publication remains blocked until the owner supplies distribution terms and
proves package/account ownership. Do not document an unverified package name as generally
installable. After legal approval and registry smoke tests prove ownership:

```bash
pipx install 'loopguard==X.Y.Z'
uvx 'loopguard==X.Y.Z' quickstart
npm install --save-dev '@loopguard/playwright@X.Y.Z'
```

Never run a publish command from an implementation agent or an ordinary pull request. Revocation,
compromise, or a bad release follows the incident process: stop promotion, revoke/rotate affected
credentials, publish a signed advisory, preserve the bad digest for forensics, and issue a new
version rather than replacing artifacts in place.
