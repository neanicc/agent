#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: LOOPGUARD_REHEARSAL_DATABASE_URL=... \
       LOOPGUARD_OLD_APP_PROBE=/absolute/probe \
       LOOPGUARD_NEW_APP_PROBE=/absolute/probe \
       scripts/rehearse_migration.sh
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

: "${LOOPGUARD_REHEARSAL_DATABASE_URL:?isolated rehearsal database URL is required}"
: "${LOOPGUARD_OLD_APP_PROBE:?old release probe executable is required}"
: "${LOOPGUARD_NEW_APP_PROBE:?new release probe executable is required}"
[[ "$LOOPGUARD_REHEARSAL_DATABASE_URL" != "${LOOPGUARD_API_DATABASE_URL:-}" ]] || {
  echo "Migration rehearsal database must not be the production database" >&2
  exit 77
}
for probe in "$LOOPGUARD_OLD_APP_PROBE" "$LOOPGUARD_NEW_APP_PROBE"; do
  [[ "$probe" == /* && -f "$probe" && -x "$probe" && ! -L "$probe" ]] || {
    echo "Migration probes must be absolute executable non-symlink files" >&2
    exit 66
  }
done

command -v alembic >/dev/null || {
  echo "alembic is required" >&2
  exit 69
}

expand_revision="${LOOPGUARD_MIGRATION_EXPAND_REVISION:-0002}"
read_switch_revision="${LOOPGUARD_MIGRATION_READ_SWITCH_REVISION:-0003}"
contract_revision="${LOOPGUARD_MIGRATION_CONTRACT_REVISION:-head}"
maximum_transition_ms="${LOOPGUARD_MIGRATION_MAX_TRANSITION_MS:-30000}"
[[ "$maximum_transition_ms" =~ ^[1-9][0-9]*$ ]] || {
  echo "Migration transition budget must be a positive integer" >&2
  exit 64
}

export LOOPGUARD_API_DATABASE_URL="$LOOPGUARD_REHEARSAL_DATABASE_URL"
alembic upgrade "$expand_revision"
"$LOOPGUARD_OLD_APP_PROBE"

started_ns="$(python3 -c 'import time; print(time.monotonic_ns())')"
alembic upgrade "$read_switch_revision"
"$LOOPGUARD_OLD_APP_PROBE"
"$LOOPGUARD_NEW_APP_PROBE"
alembic upgrade "$contract_revision"
"$LOOPGUARD_NEW_APP_PROBE"
finished_ns="$(python3 -c 'import time; print(time.monotonic_ns())')"
transition_ms="$(((finished_ns - started_ns) / 1000000))"

if ((transition_ms > maximum_transition_ms)); then
  echo "Migration transition took ${transition_ms}ms; budget is ${maximum_transition_ms}ms" >&2
  exit 70
fi
echo "Migration rehearsal passed in ${transition_ms}ms"
