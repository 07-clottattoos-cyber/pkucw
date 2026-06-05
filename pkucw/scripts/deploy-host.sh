#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${1:-${PKUCW_DEPLOY_HOST:-}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${REMOTE_HOST}" ]]; then
  echo "Usage: $0 user@host [remote-dir]" >&2
  echo "Or set PKUCW_DEPLOY_HOST=user@host." >&2
  exit 1
fi

"${SCRIPT_DIR}/deploy-remote.sh" "${REMOTE_HOST}" "${PKUCW_REMOTE_DIR:-pkucw-cli}"
