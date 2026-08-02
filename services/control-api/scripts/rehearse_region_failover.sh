#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: LOOPGUARD_FAILOVER_CONFIRM=REHEARSE_STAGING_DR \
       LOOPGUARD_FAILOVER_APPROVAL=/absolute/operator-approval.txt \
       LOOPGUARD_FAILOVER_FENCE=/absolute/fence-primary \
       LOOPGUARD_FAILOVER_PROMOTE=/absolute/promote-dr \
       LOOPGUARD_FAILOVER_VALIDATE=/absolute/validate-dr \
       LOOPGUARD_FAILOVER_FAILBACK=/absolute/failback-primary \
       LOOPGUARD_FAILOVER_EVIDENCE_DIR=/absolute/evidence \
       rehearse_region_failover.sh

Each executable receives the evidence directory as its only argument. This command is intentionally
adapter-driven: account-specific fencing and promotion stay in operator-owned wrappers.
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 0 ]]; then
  usage >&2
  exit 64
fi

[[ "${LOOPGUARD_FAILOVER_CONFIRM:-}" == "REHEARSE_STAGING_DR" ]] || {
  echo "Refusing to run without explicit staging/DR confirmation" >&2
  exit 77
}
: "${LOOPGUARD_FAILOVER_APPROVAL:?operator approval evidence is required}"
: "${LOOPGUARD_FAILOVER_EVIDENCE_DIR:?absolute evidence directory is required}"

approval="$LOOPGUARD_FAILOVER_APPROVAL"
evidence="$LOOPGUARD_FAILOVER_EVIDENCE_DIR"
[[ "$approval" == /* && -f "$approval" && ! -L "$approval" ]] || {
  echo "Approval evidence must be an absolute regular non-symlink file" >&2
  exit 66
}
grep -Eq '^environment=staging-dr$' "$approval" || {
  echo "Approval evidence does not authorize staging-dr" >&2
  exit 77
}
grep -Eq '^approved_by=[A-Za-z0-9@._ -]{2,128}$' "$approval" || {
  echo "Approval evidence requires a bounded operator identity" >&2
  exit 77
}
[[ "$evidence" == /* && ! -L "$evidence" ]] || {
  echo "Evidence directory must be absolute and cannot be a symlink" >&2
  exit 66
}
mkdir -p -- "$evidence"
chmod 700 "$evidence"
[[ -d "$evidence" && ! -L "$evidence" ]] || exit 66

for variable in \
  LOOPGUARD_FAILOVER_FENCE \
  LOOPGUARD_FAILOVER_PROMOTE \
  LOOPGUARD_FAILOVER_VALIDATE \
  LOOPGUARD_FAILOVER_FAILBACK
do
  executable="${!variable:-}"
  [[ "$executable" == /* && -f "$executable" && -x "$executable" && ! -L "$executable" ]] || {
    echo "$variable must be an absolute executable non-symlink file" >&2
    exit 66
  }
done

record() {
  local phase="$1"
  local status="$2"
  printf '%s\t%s\t%s\n' \
    "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    "$phase" \
    "$status" >>"$evidence/timeline.tsv"
}

started="$(date +%s)"
record preflight started
cp -- "$approval" "$evidence/operator-approval.txt"
record preflight passed

record fence_primary started
"$LOOPGUARD_FAILOVER_FENCE" "$evidence"
record fence_primary passed

record promote_dr started
"$LOOPGUARD_FAILOVER_PROMOTE" "$evidence"
promoted="$(date +%s)"
record promote_dr passed

record validate_dr started
"$LOOPGUARD_FAILOVER_VALIDATE" "$evidence"
record validate_dr passed

record failback_primary started
"$LOOPGUARD_FAILOVER_FAILBACK" "$evidence"
finished="$(date +%s)"
record failback_primary passed

rto_seconds="$((promoted - started))"
duration_seconds="$((finished - started))"
cat >"$evidence/summary.json" <<EOF
{
  "schema_version": 1,
  "environment": "staging-dr",
  "rto_seconds": ${rto_seconds},
  "total_duration_seconds": ${duration_seconds},
  "required_evidence": [
    "rpo.json",
    "object-checksums.json",
    "database-integrity.json",
    "idempotency.json",
    "stream-reconnect.json",
    "rollback.json"
  ]
}
EOF

for artifact in \
  rpo.json \
  object-checksums.json \
  database-integrity.json \
  idempotency.json \
  stream-reconnect.json \
  rollback.json
do
  [[ -s "$evidence/$artifact" && ! -L "$evidence/$artifact" ]] || {
    echo "Failover adapter did not produce required evidence: $artifact" >&2
    exit 70
  }
done

python3 - "$evidence" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve(strict=True)
for name in (
    "rpo.json",
    "object-checksums.json",
    "database-integrity.json",
    "idempotency.json",
    "stream-reconnect.json",
    "rollback.json",
):
    value = json.loads((root / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("passed") is not True:
        raise SystemExit(f"{name} does not contain passed=true")
PY

echo "Staging/DR failover rehearsal passed; evidence: $evidence"
