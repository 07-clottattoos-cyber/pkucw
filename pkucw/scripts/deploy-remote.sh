#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 user@host [remote-dir]" >&2
  exit 1
fi

REMOTE_HOST="$1"
REMOTE_DIR="${2:-${PKUCW_REMOTE_DIR:-pkucw-cli}}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

tar \
  --exclude='.DS_Store' \
  --exclude='output' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  -czf - -C "${PROJECT_DIR}" . \
| ssh "${REMOTE_HOST}" "\
  rm -rf ~/${REMOTE_DIR} && \
  mkdir -p ~/${REMOTE_DIR} && \
  tar xzf - -C ~/${REMOTE_DIR} && \
  cd ~/${REMOTE_DIR} && \
  chmod +x ./install.sh ./scripts/*.sh && \
  ./install.sh"

ssh "${REMOTE_HOST}" "cd ~/${REMOTE_DIR} && ./scripts/smoke-test.sh && zsh -lc 'command -v pkucw && pkucw --version'"
