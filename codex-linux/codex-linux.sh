#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export ELECTRON_RENDERER_URL="file://${SCRIPT_DIR}/webview/index.html"

# DeepSeek API key — obtenerla en https://platform.deepseek.com → API Keys
# Reemplazar sk-PEGAR_TU_KEY_AQUI con tu key real
export OPENAI_API_KEY="sk-fd99272e38e04814b5136f87b05c5f65"

if [ -z "${CODEX_CLI_PATH:-}" ]; then
  if command -v codex >/dev/null 2>&1; then
    export CODEX_CLI_PATH="$(command -v codex)"
  else
    export CODEX_CLI_PATH="${SCRIPT_DIR}/bin/codex-fallback"
  fi
fi

# Iniciar LiteLLM en puerto 4001 (traduce Responses API → DeepSeek Chat Completions)
# y proxy de filtro en puerto 4000 (elimina herramientas con type≠function)
LITELLM_CONFIG="$HOME/.codex/litellm-config.yaml"
LITELLM_LOG="$HOME/.codex/litellm.log"
PROXY_LOG="$HOME/.codex/proxy.log"

if ! curl -sf http://localhost:4001/health >/dev/null 2>&1; then
  echo "[launcher] Iniciando LiteLLM en puerto 4001..."
  litellm --config "$LITELLM_CONFIG" --port 4001 >> "$LITELLM_LOG" 2>&1 &
  echo $! > /tmp/litellm-codex.pid
  for i in $(seq 1 20); do
    sleep 0.5
    curl -sf http://localhost:4001/health >/dev/null 2>&1 && break
  done
  echo "[launcher] LiteLLM listo"
fi

if ! curl -sf http://localhost:4000/health >/dev/null 2>&1; then
  echo "[launcher] Iniciando proxy de filtro en puerto 4000..."
  python3 "$HOME/.codex/codex_proxy.py" >> "$PROXY_LOG" 2>&1 &
  echo $! > /tmp/codex-proxy.pid
  for i in $(seq 1 20); do
    sleep 0.5
    curl -sf http://localhost:4000/ >/dev/null 2>&1 && break
  done
  echo "[launcher] Proxy listo"
fi

exec ./node_modules/.bin/electron . --no-sandbox "$@"
