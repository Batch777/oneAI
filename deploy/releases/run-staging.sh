#!/bin/sh
set -eu
release=$(readlink -f "$HOME/oneai-staging/current")
cd "$release"
export ONEAI_RELEASE_COMMIT="$(basename "$release")"
export ONEAI_STATE_PATH="$HOME/oneai-staging/data/state"
export ONEAI_VAULT_PATH="$HOME/oneai-staging/data/vault"
export ONEAI_APP_ORIGIN=http://127.0.0.1:18765
export ONEAI_APP_DEV=1
export PYTHONDONTWRITEBYTECODE=1
exec "$HOME/oneai-runtime/.venv/bin/python" -m uvicorn oneai.web.app:app_factory --factory --host 127.0.0.1 --port 18765
