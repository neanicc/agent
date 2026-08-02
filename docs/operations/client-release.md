# Client release operations

This workflow packages the web console and native iOS app without deploying either client. A
normal pull request produces verification artifacts; an explicitly approved manual release can
publish a signed web image digest and upload a signed build to TestFlight. It never deploys the
web image or submits the iOS build for App Review.

## Local verification

Prerequisites are Node 24, Docker with BuildKit, Xcode 26, and XcodeGen.

```bash
cd apps/web
npm ci
npm test
npm run typecheck
npm run lint
npm run build
docker build --build-arg BUILD_SHA=test-sha -t loopguard-web:test .

cd ../ios
xcodegen generate
xcodebuild archive \
  -project LoopGuard.xcodeproj \
  -scheme LoopGuard \
  -archivePath build/LoopGuard.xcarchive \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO
```

Create the ordinary verification artifacts:

```bash
mkdir -p ../../artifacts/client-release
docker buildx build \
  --build-arg BUILD_SHA=test-sha \
  --attest type=provenance,mode=max \
  --attest type=sbom \
  --metadata-file ../../artifacts/client-release/web-metadata.json \
  --output type=oci,dest=../../artifacts/client-release/loopguard-web.oci.tar \
  .
ditto -c -k --sequesterRsrc --keepParent \
  ../ios/build/LoopGuard.xcarchive \
  ../../artifacts/client-release/LoopGuard-verification.xcarchive.zip
```

Write `artifacts/client-release/web-image-reference.txt` as
`ghcr.io/OWNER/REPOSITORY/web@sha256:DIGEST`, using the digest from BuildKit metadata. Then:

```bash
cd ../..
(
  cd artifacts/client-release
  shasum -a 256 loopguard-web.oci.tar LoopGuard-verification.xcarchive.zip > SHA256SUMS
)
scripts/verify_client_release.sh
```

The verifier checks the immutable web digest, both checksums, iOS bundle/version, embedded privacy
manifest, and credential-free App Store Connect export settings. It fails closed when any artifact
or identity is missing.

## CI artifacts

`client-release.yml` runs web tests/build/container packaging on Ubuntu and native tests/archive on
the `macos-26` runner. Ordinary runs upload:

- An OCI web image layout with BuildKit SBOM and provenance attestations.
- Its immutable prospective registry reference.
- An unsigned iOS verification archive.
- SHA-256 checksum manifests.

Artifacts are evidence, not a deployment.

## Approved release

Start the workflow manually with `release=true`. GitHub environment protection must approve both
release jobs.

- `client-web-production` needs GHCR package write and OIDC token permissions. It publishes the
  already-tested source by digest and signs that digest with keyless Cosign. No deployment runs.
- `client-ios-production` needs an App Store distribution certificate, certificate password, App
  Store Connect issuer/key identifiers, and the `.p8` private key stored as environment secrets.
  It archives with a unique CI build number, exports an IPA, and uploads to TestFlight only.

Required iOS secret names:

- `APP_STORE_CERTIFICATE_BASE64`
- `APP_STORE_CERTIFICATE_PASSWORD`
- `APP_STORE_CONNECT_API_KEY_ID`
- `APP_STORE_CONNECT_API_ISSUER_ID`
- `APP_STORE_CONNECT_API_PRIVATE_KEY`
- `APPLE_TEAM_ID`

Rotate credentials through environment protection, not source changes. A release operator reviews
TestFlight results and starts App Review separately in App Store Connect.

## Rollback and retention

The web release identity is its immutable digest; rollback selects a previously verified digest in
the deployment system. TestFlight builds are immutable and can be expired, not overwritten. Keep
the checksum/provenance artifacts for the support lifetime of each released version.
