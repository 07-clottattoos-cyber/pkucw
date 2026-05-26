#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_SOURCE_DIR="${PROJECT_DIR}/skills/pkucw-cli"

OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
OPENCLAW_WORKSPACE="${OPENCLAW_WORKSPACE:-${OPENCLAW_HOME}/workspace}"
OPENCLAW_SKILLS_DIR="${OPENCLAW_SKILLS_DIR:-${OPENCLAW_WORKSPACE}/skills}"
OPENCLAW_SKILL_NAME="${OPENCLAW_SKILL_NAME:-pkucw-cli}"
OPENCLAW_SKILL_TARGET="${OPENCLAW_SKILLS_DIR}/${OPENCLAW_SKILL_NAME}"
OPENCLAW_EXTENSION_BIN_DIR="${OPENCLAW_HOME}/extensions/${OPENCLAW_SKILL_NAME}/bin"
OPENCLAW_WORKSPACE_PKUCW="${OPENCLAW_WORKSPACE}/pkucw"
OPENCLAW_WORKSPACE_PKUCW_CLI="${OPENCLAW_WORKSPACE}/pkucw-cli"

PKUCW_SKIP_TOOL_INSTALL="${PKUCW_SKIP_TOOL_INSTALL:-0}"
PKUCW_SKILL_INSTALL_MODE="${PKUCW_SKILL_INSTALL_MODE:-symlink}"

install_tool() {
  if [[ "${PKUCW_SKIP_TOOL_INSTALL}" == "1" ]]; then
    echo "Skipping pkucw tool installation because PKUCW_SKIP_TOOL_INSTALL=1"
    return 0
  fi

  echo "Installing pkucw tool..."
  "${PROJECT_DIR}/install.sh"
}

install_skill() {
  mkdir -p "${OPENCLAW_SKILLS_DIR}"
  rm -rf "${OPENCLAW_SKILL_TARGET}"

  case "${PKUCW_SKILL_INSTALL_MODE}" in
    symlink)
      ln -s "${SKILL_SOURCE_DIR}" "${OPENCLAW_SKILL_TARGET}"
      ;;
    copy)
      cp -R "${SKILL_SOURCE_DIR}" "${OPENCLAW_SKILL_TARGET}"
      ;;
    *)
      echo "Unsupported PKUCW_SKILL_INSTALL_MODE: ${PKUCW_SKILL_INSTALL_MODE}" >&2
      exit 1
      ;;
  esac
}

install_extension_shims() {
  mkdir -p "${OPENCLAW_EXTENSION_BIN_DIR}"
  ln -sf "${PROJECT_DIR}/pkucw" "${OPENCLAW_EXTENSION_BIN_DIR}/pkucw"
  ln -sf "${PROJECT_DIR}/pkucw-cli" "${OPENCLAW_EXTENSION_BIN_DIR}/pkucw-cli"
}

install_workspace_shims() {
  mkdir -p "${OPENCLAW_WORKSPACE}"
  ln -sf "${PROJECT_DIR}/pkucw" "${OPENCLAW_WORKSPACE_PKUCW}"
  ln -sf "${PROJECT_DIR}/pkucw-cli" "${OPENCLAW_WORKSPACE_PKUCW_CLI}"
}

install_tool

echo "Installing pkucw OpenClaw skill..."
install_skill
install_extension_shims
install_workspace_shims

echo
echo "OpenClaw installation complete."
echo "  Project:       ${PROJECT_DIR}"
echo "  Skill source:  ${SKILL_SOURCE_DIR}"
echo "  Skill target:  ${OPENCLAW_SKILL_TARGET}"
echo "  Skill mode:    ${PKUCW_SKILL_INSTALL_MODE}"
echo "  Extension bin: ${OPENCLAW_EXTENSION_BIN_DIR}"
echo "  Workspace bin: ${OPENCLAW_WORKSPACE_PKUCW}"

if command -v pkucw >/dev/null 2>&1; then
  echo
  echo "pkucw version:"
  pkucw --version
fi

if command -v openclaw >/dev/null 2>&1; then
  echo
  echo "Detected openclaw on PATH."
  echo "You can verify the skill with:"
  echo "  openclaw skills list | grep pkucw-cli"
else
  echo
  echo "openclaw was not found on PATH."
  echo "After installing OpenClaw, verify the skill with:"
  echo "  openclaw skills list | grep pkucw-cli"
fi
