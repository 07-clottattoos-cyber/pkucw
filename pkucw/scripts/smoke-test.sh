#!/usr/bin/env bash
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"
COURSEWEB_BIN="${COURSEWEB_BIN:-pkucw}"

run() {
  echo
  echo "+ $*"
  "$@"
}

run_quiet() {
  echo
  echo "+ $*"
  "$@" >/dev/null
}

run "${COURSEWEB_BIN}" --version
run_quiet "${COURSEWEB_BIN}" doctor --json
run_quiet "${COURSEWEB_BIN}" completion zsh
run_quiet "${COURSEWEB_BIN}" --help
run_quiet "${COURSEWEB_BIN}" recordings --help
