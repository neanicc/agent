#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  echo "Usage: LOOPGUARD_RESTORE_DATABASE_URL=... LOOPGUARD_BACKUP_AGE_IDENTITY=... $0 BACKUP.age OUTPUT_DIRECTORY"
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ $# -ne 2 ]]; then
  usage >&2
  exit 64
fi

: "${LOOPGUARD_RESTORE_DATABASE_URL:?isolated restore database URL is required}"
: "${LOOPGUARD_BACKUP_AGE_IDENTITY:?age identity path is required}"
if [[ -n "${LOOPGUARD_API_DATABASE_URL:-}" &&
  "$LOOPGUARD_RESTORE_DATABASE_URL" == "$LOOPGUARD_API_DATABASE_URL" ]]; then
  echo "Restore target must not equal the source/production database" >&2
  exit 77
fi

for command in pg_restore psql age python3; do
  command -v "$command" >/dev/null || {
    echo "Required restore command is missing: $command" >&2
    exit 69
  }
done

backup="$1"
destination="$2"
[[ -f "$backup" && ! -L "$backup" ]] || {
  echo "Backup must be a regular non-symlink file" >&2
  exit 66
}
[[ ! -e "$destination" ]] || {
  echo "Restore evidence directory must not already exist" >&2
  exit 73
}
[[ -f "$LOOPGUARD_BACKUP_AGE_IDENTITY" && ! -L "$LOOPGUARD_BACKUP_AGE_IDENTITY" ]] || {
  echo "Age identity must be a regular non-symlink file" >&2
  exit 66
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
workspace="$(mktemp -d "${TMPDIR:-/tmp}/loopguard-restore.XXXXXXXX")"
cleanup() {
  rm -rf -- "$workspace"
}
trap cleanup EXIT INT TERM

age \
  --decrypt \
  --identity "$LOOPGUARD_BACKUP_AGE_IDENTITY" \
  --output "$workspace/recovery.tar" \
  "$backup"
python3 "$script_dir/recovery_integrity.py" extract \
  --archive "$workspace/recovery.tar" \
  --destination "$workspace/bundle"

pg_restore \
  --dbname "$LOOPGUARD_RESTORE_DATABASE_URL" \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --exit-on-error \
  "$workspace/bundle/database.dump"
psql \
  --dbname "$LOOPGUARD_RESTORE_DATABASE_URL" \
  --set ON_ERROR_STOP=1 \
  --tuples-only \
  --no-align \
  --file "$script_dir/snapshot_integrity.sql" \
  >"$workspace/restored-state.json"
python3 "$script_dir/recovery_integrity.py" compare \
  --source "$workspace/bundle/database-state.json" \
  --restored "$workspace/restored-state.json"

mkdir -m 700 -- "$destination"
cp -- "$workspace/bundle/recovery-manifest.json" "$destination/"
cp -- "$workspace/bundle/object-manifest.json" "$destination/"
cp -- "$workspace/restored-state.json" "$destination/"
chmod 600 "$destination"/*
echo "Restore integrity verified; evidence written to $destination"
