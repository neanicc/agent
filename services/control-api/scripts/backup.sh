#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  echo "Usage: LOOPGUARD_API_DATABASE_URL=... LOOPGUARD_OBJECT_MANIFEST=... LOOPGUARD_BACKUP_AGE_RECIPIENT=... $0 OUTPUT.age"
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 1 ]]; then
  usage >&2
  exit 64
fi

: "${LOOPGUARD_API_DATABASE_URL:?database URL is required}"
: "${LOOPGUARD_OBJECT_MANIFEST:?object manifest path is required}"
: "${LOOPGUARD_BACKUP_AGE_RECIPIENT:?age recipient is required}"

for command in pg_dump psql age tar python3; do
  command -v "$command" >/dev/null || {
    echo "Required backup command is missing: $command" >&2
    exit 69
  }
done

output="$1"
[[ ! -e "$output" && ! -e "$output.partial" ]] || {
  echo "Refusing to overwrite an existing backup" >&2
  exit 73
}
[[ -f "$LOOPGUARD_OBJECT_MANIFEST" && ! -L "$LOOPGUARD_OBJECT_MANIFEST" ]] || {
  echo "Object manifest must be a regular non-symlink file" >&2
  exit 66
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
workspace="$(mktemp -d "${TMPDIR:-/tmp}/loopguard-backup.XXXXXXXX")"
cleanup() {
  rm -rf -- "$workspace"
  rm -f -- "$output.partial"
}
trap cleanup EXIT INT TERM

pg_dump \
  --dbname "$LOOPGUARD_API_DATABASE_URL" \
  --format custom \
  --no-owner \
  --no-privileges \
  --file "$workspace/database.dump"
psql \
  --dbname "$LOOPGUARD_API_DATABASE_URL" \
  --set ON_ERROR_STOP=1 \
  --tuples-only \
  --no-align \
  --file "$script_dir/snapshot_integrity.sql" \
  >"$workspace/database-state.json"
cp -- "$LOOPGUARD_OBJECT_MANIFEST" "$workspace/object-manifest.json"

python3 "$script_dir/recovery_integrity.py" prepare \
  --database-dump "$workspace/database.dump" \
  --database-state "$workspace/database-state.json" \
  --object-manifest "$workspace/object-manifest.json" \
  --output "$workspace/recovery-manifest.json"

tar -C "$workspace" -cf "$workspace/recovery.tar" \
  database.dump database-state.json object-manifest.json recovery-manifest.json
age \
  --recipient "$LOOPGUARD_BACKUP_AGE_RECIPIENT" \
  --output "$output.partial" \
  "$workspace/recovery.tar"
chmod 600 "$output.partial"
mv -- "$output.partial" "$output"
trap - EXIT INT TERM
rm -rf -- "$workspace"
echo "Encrypted recovery bundle written to $output"
