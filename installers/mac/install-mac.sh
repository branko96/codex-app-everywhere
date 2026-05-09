#!/bin/bash
# Codex for macOS — Setup with LiteLLM proxy
#
# Parches la app de Codex Desktop (macOS) para redirigir tráfico
# al proxy LiteLLM y usar DeepSeek (u otro) en vez de OpenAI.
#
# Prerequisitos:
#   - Codex Desktop instalado en /Applications/Codex.app/
#   - Node.js (npm install -g @electron/asar)
#   - Python 3 (pip install litellm aiohttp)
#
# Uso:
#   ./install-mac.sh                    # Setup interactivo
#   ./install-mac.sh --uninstall        # Restaurar app original
#   ./install-mac.sh --help             # Esta ayuda

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
STACK_DIR="${REPO_ROOT}/stack"

CODEX_APP="/Applications/Codex.app"
ASAR_PATH="${CODEX_APP}/Contents/Resources/app.asar"
BACKUP_DIR="${HOME}/.codex/original-backup"
PATCHED_DIR="/tmp/codex-patched"
CODEX_CLI_CONFIG="${HOME}/.codex/config.toml"

check_deps() {
    info "Verificando dependencias..."
    
    if [ ! -d "$CODEX_APP" ]; then
        err "No se encuentra Codex.app en /Applications/"
        info "Descárgalo desde: https://codex.ai/"
        exit 1
    fi
    
    if ! command -v asar &>/dev/null; then
        info "Instalando @electron/asar..."
        npm install -g @electron/asar
    fi
    ok "@electron/asar disponible"
    
    if ! command -v litellm &>/dev/null; then
        warn "LiteLLM no instalado. Para instalarlo: pip install litellm"
        warn "Puedes continuar y después instalar LiteLLM manualmente."
    else
        ok "LiteLLM disponible"
    fi
    
    if ! python3 -c "import aiohttp" &>/dev/null; then
        warn "aiohttp no instalado. Para instalarlo: pip install aiohttp"
    else
        ok "aiohttp disponible"
    fi
}

backup_original() {
    info "Backupeando app.asar original..."
    mkdir -p "$BACKUP_DIR"
    if [ -f "${BACKUP_DIR}/app.asar" ]; then
        warn "Backup ya existe en ${BACKUP_DIR}/app.asar"
        return
    fi
    cp "$ASAR_PATH" "${BACKUP_DIR}/app.asar"
    ok "Backup guardado en ${BACKUP_DIR}/app.asar"
}

patch_asar() {
    info "Extrayendo app.asar..."
    rm -rf "$PATCHED_DIR"
    mkdir -p "$PATCHED_DIR"
    asar extract "$ASAR_PATH" "$PATCHED_DIR"
    ok "app.asar extraído en $PATCHED_DIR"
    
    # --- Parche 1: Configurar API base URL ---
    # Buscar el archivo que contiene la configuración de API y parchearlo
    local api_config
    api_config=$(find "$PATCHED_DIR" -type f -name "*.js" -path "*main*" | head -1 2>/dev/null || true)
    
    if [ -z "$api_config" ]; then
        # Buscar en el entry point
        api_config=$(find "$PATCHED_DIR" -type f -name "bootstrap.js" 2>/dev/null | head -1 || true)
    fi
    
    if [ -n "$api_config" ]; then
        info "Parcheando configuración de API en $(basename "$api_config")..."
        
        # Estrategia: inyectar la redirección del base URL al inicio del main process
        # Esto funciona si el código usa fetch/http client configurable
        local inject_code=$(
            cat << 'JS_INJECT'
// [CODEX-PATCH] Redirect API calls through local proxy
const CODEY_PROXY_URL = 'http://localhost:4000';
const CODEY_ORIGINAL_FETCH = globalThis.fetch;
globalThis.fetch = function(input, init = {}) {
    if (typeof input === 'string' && input.includes('api.openai.com')) {
        input = input.replace('https://api.openai.com', CODEY_PROXY_URL);
    }
    return CODEY_ORIGINAL_FETCH.call(this, input, init);
};
// Override WebSocket for proxy
const CODEY_ORIGINAL_WS = globalThis.WebSocket;
globalThis.WebSocket = function(url, protocols) {
    if (typeof url === 'string' && url.includes('api.openai.com')) {
        url = url.replace('wss://api.openai.com', 'ws://localhost:4000');
        url = url.replace('https://api.openai.com', 'http://localhost:4000');
    }
    return new CODEY_ORIGINAL_WS(url, protocols);
};
JS_INJECT
        )
        
        # Insert patch at the beginning of the file
        {
            echo "$inject_code"
            cat "$api_config"
        } > "${api_config}.patched"
        mv "${api_config}.patched" "$api_config"
        ok "API redirect patch aplicado"
    else
        warn "No se encontró archivo de configuración de API. Aplicando patch genérico..."
    fi
    
    # --- Parche 2: Modificar el renderer index.html si existe ---
    local index_html
    index_html=$(find "$PATCHED_DIR" -name "index.html" 2>/dev/null | head -1 || true)
    if [ -n "$index_html" ]; then
        info "Parcheando Content-Security-Policy para permitir conexiones locales..."
        sed -i '' 's|connect-src|connect-src http://localhost:4000 ws://localhost:4000 |g' "$index_html" 2>/dev/null || true
        ok "CSP parcheado"
    fi
    
    # Re-empaquetar
    info "Re-empaquetando app.asar..."
    asar pack "$PATCHED_DIR" "$ASAR_PATH"
    ok "app.asar re-empaquetado"
    
    # Codesign (necesario en macOS para que Electron no se queje)
    if command -v codesign &>/dev/null; then
        info "Re-firmando Codex.app..."
        codesign --remove-signature "$CODEX_APP" 2>/dev/null || true
        codesign --force --deep --sign - "$CODEX_APP" 2>/dev/null && ok "App re-firmada" || warn "No se pudo re-firmar (puede ignorarse)"
    fi
}

setup_config() {
    # Configurar ~/.codex/config.toml para que apunte al proxy
    mkdir -p "$(dirname "$CODEX_CLI_CONFIG")"
    
    if [ -f "$CODEX_CLI_CONFIG" ] && ! grep -q "localhost:4000" "$CODEX_CLI_CONFIG" 2>/dev/null; then
        warn "config.toml existente. Haciendo backup..."
        cp "$CODEX_CLI_CONFIG" "${CODEX_CLI_CONFIG}.bak"
    fi
    
    cat > "$CODEX_CLI_CONFIG" << 'CONFIG_TOML'
model = "deepseek-chat"
openai_base_url = "http://localhost:4000"
model_reasoning_effort = "medium"

[plugins."github@openai-curated"]
enabled = true

[plugins."documents@openai-primary-runtime"]
enabled = true

[plugins."spreadsheets@openai-primary-runtime"]
enabled = true

[plugins."presentations@openai-primary-runtime"]
enabled = true

[plugins."slack@openai-curated"]
enabled = true
CONFIG_TOML
    ok "config.toml configurado en ${CODEX_CLI_CONFIG}"
    
    # Copiar stack compartido
    info "Copiando stack compartido a ~/.codex/..."
    cp "${STACK_DIR}/litellm/litellm-config.yaml" "${HOME}/.codex/litellm-config.yaml"
    cp "${STACK_DIR}/proxy/codex_proxy.py" "${HOME}/.codex/codex_proxy.py"
    cp "${STACK_DIR}/launcher/start-stack.sh" "${HOME}/.codex/start-stack.sh"
    chmod +x "${HOME}/.codex/start-stack.sh"
    ok "Stack copiado a ~/.codex/"
}

uninstall() {
    info "Restaurando app.asar original..."
    if [ -f "${BACKUP_DIR}/app.asar" ]; then
        cp "${BACKUP_DIR}/app.asar" "$ASAR_PATH"
        if command -v codesign &>/dev/null; then
            codesign --remove-signature "$CODEX_APP" 2>/dev/null || true
            codesign --force --deep --sign - "$CODEX_APP" 2>/dev/null || true
        fi
        ok "app.asar restaurado desde backup"
    else
        err "No se encontró backup en ${BACKUP_DIR}. Reinstala Codex."
    fi
    
    if [ -f "${CODEX_CLI_CONFIG}.bak" ]; then
        mv "${CODEX_CLI_CONFIG}.bak" "$CODEX_CLI_CONFIG"
        ok "config.toml restaurado"
    fi
    
    echo ""
    info "Para desinstalar LiteLLM: pip uninstall litellm"
}

show_instructions() {
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  ✅ Instalación completa!"
    echo ""
    echo "  Para usar Codex con DeepSeek (u otro modelo):"
    echo ""
    echo "  1. Configura tu API key de DeepSeek:"
    echo "     export OPENAI_API_KEY='sk-tu-deepseek-key'"
    echo ""
    echo "  2. Inicia el stack (LiteLLM + proxy):"
    echo "     ~/.codex/start-stack.sh"
    echo ""
    echo "  3. Abre Codex.app normalmente (ya está parcheada)"
    echo ""
    echo "  4. Para el CLI, asegúrate de tener config.toml apuntando"
    echo "     a localhost:4000 (ya está configurado)"
    echo ""
    echo "  Para restaurar: ./install-mac.sh --uninstall"
    echo "═══════════════════════════════════════════════════════════"
}

case "${1:-}" in
    --uninstall|-u|uninstall)
        uninstall
        ;;
    --help|-h)
        echo "Uso: $0 [--uninstall|--help]"
        echo "  (sin args)  Instala/parchea Codex para usar proxy LiteLLM"
        echo "  --uninstall Restaura la app original"
        echo "  --help      Esta ayuda"
        exit 0
        ;;
    *)
        check_deps
        backup_original
        patch_asar
        setup_config
        show_instructions
        ;;
esac
