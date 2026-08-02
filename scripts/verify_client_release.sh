#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACT_DIR="${ARTIFACT_DIR:-${ROOT_DIR}/artifacts/client-release}"
WEB_IMAGE_REFERENCE_FILE="${WEB_IMAGE_REFERENCE_FILE:-${ARTIFACT_DIR}/web-image-reference.txt}"
WEB_ARTIFACT_PATH="${WEB_ARTIFACT_PATH:-${ARTIFACT_DIR}/loopguard-web.oci.tar}"
IOS_ARCHIVE_PATH="${IOS_ARCHIVE_PATH:-${ROOT_DIR}/apps/ios/build/LoopGuard.xcarchive}"
IOS_ARTIFACT_PATH="${IOS_ARTIFACT_PATH:-${ARTIFACT_DIR}/LoopGuard-verification.xcarchive.zip}"
CHECKSUM_FILE="${CHECKSUM_FILE:-${ARTIFACT_DIR}/SHA256SUMS}"
EXPORT_OPTIONS="${EXPORT_OPTIONS:-${ROOT_DIR}/apps/ios/ExportOptions.plist}"
EXPECTED_BUNDLE_ID="${EXPECTED_BUNDLE_ID:-dev.loopguard.ios}"
EXPECTED_VERSION="${EXPECTED_VERSION:-0.1.0}"

fail() {
  echo "client release verification failed: $*" >&2
  exit 1
}

[[ -f "${WEB_IMAGE_REFERENCE_FILE}" ]] || fail "missing web image reference"
web_reference="$(tr -d '[:space:]' < "${WEB_IMAGE_REFERENCE_FILE}")"
[[ "${web_reference}" =~ ^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$ ]] \
  || fail "web image reference must use an immutable sha256 digest"
[[ -s "${WEB_ARTIFACT_PATH}" ]] || fail "missing web OCI artifact"

app_path="${IOS_ARCHIVE_PATH}/Products/Applications/LoopGuard.app"
info_plist="${app_path}/Info.plist"
[[ -f "${info_plist}" ]] || fail "missing archived LoopGuard.app Info.plist"
bundle_id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "${info_plist}")"
version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${info_plist}")"
[[ "${bundle_id}" == "${EXPECTED_BUNDLE_ID}" ]] || fail "unexpected iOS bundle identifier ${bundle_id}"
[[ "${version}" == "${EXPECTED_VERSION}" ]] || fail "unexpected iOS version ${version}"
[[ -f "${app_path}/PrivacyInfo.xcprivacy" ]] || fail "privacy manifest is absent from the archive"
[[ -s "${IOS_ARTIFACT_PATH}" ]] || fail "missing zipped iOS verification archive"

[[ -f "${EXPORT_OPTIONS}" ]] || fail "missing iOS export options"
export_method="$(plutil -extract method raw -o - "${EXPORT_OPTIONS}")"
[[ "${export_method}" == "app-store-connect" ]] || fail "export method must be app-store-connect"
if grep -Eiq 'api[_ -]?key|issuer|password|private[_ -]?key|provisioningProfiles|signingCertificate' "${EXPORT_OPTIONS}"; then
  fail "export settings must not contain signing credentials or fixed signing material"
fi

[[ -s "${CHECKSUM_FILE}" ]] || fail "missing artifact checksum manifest"
grep -Fq "  $(basename "${WEB_ARTIFACT_PATH}")" "${CHECKSUM_FILE}" \
  || fail "web artifact checksum is absent"
grep -Fq "  $(basename "${IOS_ARTIFACT_PATH}")" "${CHECKSUM_FILE}" \
  || fail "iOS artifact checksum is absent"
(
  cd "$(dirname "${CHECKSUM_FILE}")"
  shasum -a 256 -c "$(basename "${CHECKSUM_FILE}")"
)

echo "client release artifacts verified"
