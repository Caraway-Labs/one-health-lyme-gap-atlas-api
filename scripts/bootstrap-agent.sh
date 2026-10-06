#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v git >/dev/null 2>&1; then
  echo "git is required to fetch pinned GitHub dependencies" >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  UV_BIN="$(command -v uv)"
else
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 with venv is required to install the local uv tool" >&2
    exit 1
  fi
  if [ ! -x .agent-tools/bin/python ]; then
    python3 -m venv .agent-tools
  fi
  .agent-tools/bin/python -m pip install --disable-pip-version-check 'uv==0.10.9'
  UV_BIN="$PWD/.agent-tools/bin/uv"
fi

"$UV_BIN" sync --extra dev --locked
"$UV_BIN" run python -c 'import lyme_gap_atlas_api; print("Atlas API environment ready")'
echo "uv command: $UV_BIN"
