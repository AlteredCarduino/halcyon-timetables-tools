#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${TIMETABLED_ROOMS_ENV:-/etc/registration.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
elif [[ -f "$ROOT/signInApp_server/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT/signInApp_server/.env"
  set +a
fi
exec "$ROOT/.venv/bin/python" "$ROOT/signInApp_server/timetabled_rooms_sync.py" "$@"
