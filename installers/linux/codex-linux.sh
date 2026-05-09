#!/bin/bash
# Codex for Linux — Launcher
# Starts: proxy stack (LiteLLM + filter) → Electron app
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"  # codex-linux/ directory
STACK_DIR="$(cd "${CODEX_DIR}/stack" && pwd 2>/dev/null || echo "${CODEX_DIR}/../stack")"

export ELECTRON_RENDERER_URL="file://${CODEX_DIR}/webview/index.html"

# Config — edit this or set env var
export OPENAI_API_KEY="${OPENAI_API_KEY:-sk-PEGAR_TU_DEEPSEEK_KEY_AQUI}"

# Find Codex CLI
if [ -z "${CODEX_CLI_PATH:-}" ]; then
  if command -v codex >/dev/null 2>&1; then
    export CODEX_CLI_PATH="$(command -v codex)"
  elif [ -f "${CODEX_DIR}/bin/codex-fallback" ]; then
    export CODEX_CLI_PATH="${CODEX_DIR}/bin/codex-fallback"
  fi
fi

# Start shared stack
START_STACK="${STACK_DIR}/launcher/start-stack.sh"
if [ -f "$START_STACK" ]; then
  bash "$START_STACK"
else
  echo "[launcher] Warning: start-stack.sh not found, assuming services are running"
fi

# Launch Electron
exec "${CODEX_DIR}/node_modules/.bin/electron" "${CODEX_DIR}" --no-sandbox "$@"
