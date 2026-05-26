#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${COURSEWEB_INSTALL_ROOT:-$HOME/.local/share/courseweb-cli}"
BIN_DIR="${COURSEWEB_BIN_DIR:-$HOME/.local/bin}"
PATH_EXPORT_LINE="export PATH=\"${BIN_DIR}:\$PATH\""
COMPLETION_ROOT="${INSTALL_ROOT}/completions"

pick_python() {
  if [[ -n "${COURSEWEB_PYTHON:-}" ]]; then
    printf '%s\n' "${COURSEWEB_PYTHON}"
    return 0
  fi

  local candidate
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

python_is_supported() {
  local candidate="$1"
  "${candidate}" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

install_uv_python() {
  local uv_bin="${HOME}/.local/bin/uv"
  if [[ ! -x "${uv_bin}" ]]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  export PATH="${HOME}/.local/bin:${PATH}"
  "${uv_bin}" python install 3.12
  "${uv_bin}" python find 3.12
}

pick_profile_file() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"

  case "${shell_name}" in
    zsh)
      printf '%s\n' "${HOME}/.zprofile"
      ;;
    bash)
      printf '%s\n' "${HOME}/.bash_profile"
      ;;
    *)
      printf '%s\n' "${HOME}/.profile"
      ;;
  esac
}

pick_completion_profile_file() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"

  case "${shell_name}" in
    zsh)
      printf '%s\n' "${HOME}/.zshrc"
      ;;
    bash)
      printf '%s\n' "${HOME}/.bashrc"
      ;;
    *)
      printf '%s\n' "${HOME}/.profile"
      ;;
  esac
}

ensure_path_config() {
  local profile_file="$1"
  mkdir -p "$(dirname "${profile_file}")"
  touch "${profile_file}"
  if ! grep -F "${PATH_EXPORT_LINE}" "${profile_file}" >/dev/null 2>&1; then
    {
      echo
      echo "# Added by courseweb-cli installer"
      echo "${PATH_EXPORT_LINE}"
    } >> "${profile_file}"
    printf '%s\n' "${profile_file}"
    return 0
  fi

  return 1
}

ensure_profile_line() {
  local profile_file="$1"
  local marker="$2"
  local line="$3"
  mkdir -p "$(dirname "${profile_file}")"
  touch "${profile_file}"
  if ! grep -F "${line}" "${profile_file}" >/dev/null 2>&1; then
    {
      echo
      echo "${marker}"
      echo "${line}"
    } >> "${profile_file}"
    return 0
  fi

  return 1
}

remove_profile_line() {
  local profile_file="$1"
  local line="$2"
  [[ -f "${profile_file}" ]] || return 0

  local tmp_file
  tmp_file="$(mktemp)"
  awk -v target="${line}" '$0 != target { print }' "${profile_file}" > "${tmp_file}"
  mv "${tmp_file}" "${profile_file}"
}

PYTHON_BIN="$(pick_python || true)"
if [[ -z "${PYTHON_BIN}" ]] || ! python_is_supported "${PYTHON_BIN}"; then
  echo "Python 3.10+ was not found on PATH. Installing a user-local Python with uv..." >&2
  PYTHON_BIN="$(install_uv_python)"
fi

"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("courseweb-cli needs Python 3.10 or newer")
PY

VENV_DIR="${INSTALL_ROOT}/.venv"
mkdir -p "${INSTALL_ROOT}" "${BIN_DIR}" "${HOME}/.courseweb" "${COMPLETION_ROOT}"

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
if ! "${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel; then
  echo "Warning: failed to upgrade pip/setuptools/wheel; continuing with bundled versions." >&2
fi
"${VENV_DIR}/bin/pip" install --no-build-isolation -e "${PROJECT_DIR}"
"${VENV_DIR}/bin/python" -m playwright install chromium

ln -sf "${VENV_DIR}/bin/pkucw" "${BIN_DIR}/pkucw"
ln -sf "${VENV_DIR}/bin/pkucw-cli" "${BIN_DIR}/pkucw-cli"
ln -sf "${VENV_DIR}/bin/courseweb" "${BIN_DIR}/courseweb"
ln -sf "${VENV_DIR}/bin/cw" "${BIN_DIR}/cw"
export PATH="${BIN_DIR}:${PATH}"

PROFILE_FILE="$(pick_profile_file)"
UPDATED_PROFILE=""
if UPDATED_PROFILE="$(ensure_path_config "${PROFILE_FILE}")"; then
  PROFILE_NOTE="Updated ${UPDATED_PROFILE} so future shells can find pkucw."
else
  PROFILE_NOTE="PATH already includes ${BIN_DIR} in ${PROFILE_FILE}."
fi

SHELL_NAME="$(basename "${SHELL:-}")"
COMPLETION_NOTE="Shell completion was not configured automatically."
case "${SHELL_NAME}" in
  zsh|bash)
    COMPLETION_PROFILE_FILE="$(pick_completion_profile_file)"
    COMPLETION_FILE="${COMPLETION_ROOT}/pkucw.${SHELL_NAME}"
    "${BIN_DIR}/pkucw" completion "${SHELL_NAME}" > "${COMPLETION_FILE}"
    COMPLETION_LINE="[[ -f \"${COMPLETION_FILE}\" ]] && source \"${COMPLETION_FILE}\""
    remove_profile_line "${PROFILE_FILE}" "${COMPLETION_LINE}"
    if ensure_profile_line "${COMPLETION_PROFILE_FILE}" "# Added by courseweb-cli installer" "${COMPLETION_LINE}"; then
      COMPLETION_NOTE="Installed ${SHELL_NAME} completion at ${COMPLETION_FILE} via ${COMPLETION_PROFILE_FILE}."
    else
      COMPLETION_NOTE="Completion already configured at ${COMPLETION_FILE} via ${COMPLETION_PROFILE_FILE}."
    fi
    ;;
  fish)
    COMPLETION_FILE="${HOME}/.config/fish/completions/pkucw.fish"
    mkdir -p "$(dirname "${COMPLETION_FILE}")"
    "${BIN_DIR}/pkucw" completion fish > "${COMPLETION_FILE}"
    COMPLETION_NOTE="Installed fish completion at ${COMPLETION_FILE}."
    ;;
esac

echo
echo "Installed courseweb-cli."
echo "  Python: ${PYTHON_BIN}"
echo "  Venv:   ${VENV_DIR}"
echo "  Bins:   ${BIN_DIR}/pkucw, ${BIN_DIR}/pkucw-cli, ${BIN_DIR}/courseweb, ${BIN_DIR}/cw"
echo "  Shell:  ${PROFILE_NOTE}"
echo "  Finish: ${COMPLETION_NOTE}"

case ":$PATH:" in
  *":${BIN_DIR}:"*) ;;
  *)
    echo
    echo "Add this to your shell profile if needed:"
    echo "  export PATH=\"${BIN_DIR}:\$PATH\""
    ;;
esac

echo
if "${BIN_DIR}/pkucw" doctor --json >/dev/null 2>&1; then
  echo "Quick check: pkucw doctor --json passed."
else
  echo "Quick check: pkucw doctor --json failed. Run `pkucw doctor --json` for details." >&2
fi
