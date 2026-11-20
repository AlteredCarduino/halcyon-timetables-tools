#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/app.py" ]]; then
  APP_ROOT="$SCRIPT_DIR"
else
  APP_ROOT="$SCRIPT_DIR"
  if [[ ! -f "$APP_ROOT/app.py" && -f "$SCRIPT_DIR/../signInApp_server/app.py" ]]; then
    APP_ROOT="$(cd "$SCRIPT_DIR/../signInApp_server" && pwd)"
  fi
fi

ENV_FILE="${REGISTRATION_WHOLEYEAR_ENV:-/etc/registration.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
elif [[ -f "$APP_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$APP_ROOT/.env"
  set +a
fi

PY="${PWA_PYTHON:-python3}"
if [[ -x "$APP_ROOT/../.venv/bin/python" ]]; then
  PY="$APP_ROOT/../.venv/bin/python"
fi

if [[ $# -eq 0 ]]; then
  set -- --sync auto
fi
exec "$PY" "$APP_ROOT/registration_data_wholeyear.py" "$@"
