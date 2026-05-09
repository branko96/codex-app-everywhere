#!/bin/bash
# Start the Codex proxy stack (LiteLLM + filter proxy)
# Works on both Linux and macOS
#
# Usage:
#   ./start-stack.sh              # Start both services
#   ./start-stack.sh --stop       # Stop all services
#   ./start-stack.sh --status     # Check if services are running

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LITELLM_CONFIG="${SCRIPT_DIR}/../litellm/litellm-config.yaml"
PROXY_SCRIPT="${SCRIPT_DIR}/../proxy/codex_proxy.py"

LITELLM_LOG="$HOME/.codex/litellm.log"
PROXY_LOG="$HOME/.codex/proxy.log"
LITELLM_PIDFILE="/tmp/codex-litellm.pid"
PROXY_PIDFILE="/tmp/codex-proxy.pid"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; }

cleanup() {
    for pidfile in "$LITELLM_PIDFILE" "$PROXY_PIDFILE"; do
        if [ -f "$pidfile" ]; then
            local pid
            pid=$(cat "$pidfile" 2>/dev/null || true)
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
                ok "Detenido PID $pid"
            fi
            rm -f "$pidfile"
        fi
    done
}

check_deps() {
    if ! command -v litellm &>/dev/null; then
        err "LiteLLM no está instalado. Instálalo con: pip install litellm"
        exit 1
    fi
    if ! python3 -c "import aiohttp" &>/dev/null; then
        err "aiohttp no está instalado. Instálalo con: pip install aiohttp"
        exit 1
    fi
}

stop_stack() {
    info "Deteniendo servicios..."
    cleanup
    ok "Stack detenido"
}

status_stack() {
    echo ""
    echo "=== Estado del stack ==="
    for svc in "LiteLLM:4001:$LITELLM_PIDFILE" "Proxy:4000:$PROXY_PIDFILE"; do
        local name port pidfile
        name=$(echo "$svc" | cut -d: -f1)
        port=$(echo "$svc" | cut -d: -f2)
        pidfile=$(echo "$svc" | cut -d: -f3)
        
        if [ -f "$pidfile" ]; then
            local pid
            pid=$(cat "$pidfile" 2>/dev/null || true)
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                if curl -sf "http://localhost:$port/health" >/dev/null 2>&1; then
                    ok "$name corriendo en puerto $port (PID $pid)"
                else
                    warn "$name (PID $pid) en puerto $port — no responde health check"
                fi
            else
                err "$name pidfile existe pero proceso muerto (puerto $port)"
            fi
        else
            if curl -sf "http://localhost:$port/" >/dev/null 2>&1 || curl -sf "http://localhost:$port/health" >/dev/null 2>&1; then
                ok "$name respondiendo en puerto $port (sin pidfile)"
            else
                err "$name no está corriendo (puerto $port)"
            fi
        fi
    done
    echo ""
}

start_stack() {
    info "Verificando dependencias..."
    check_deps

    # Ensure ~/.codex/ exists
    mkdir -p "$HOME/.codex"

    # --- LiteLLM ---
    if curl -sf http://localhost:4001/health >/dev/null 2>&1; then
        ok "LiteLLM ya está corriendo en puerto 4001"
    else
        info "Iniciando LiteLLM en puerto 4001..."
        litellm --config "$LITELLM_CONFIG" --port 4001 >> "$LITELLM_LOG" 2>&1 &
        echo $! > "$LITELLM_PIDFILE"
        for i in $(seq 1 20); do
            sleep 0.5
            if curl -sf http://localhost:4001/health >/dev/null 2>&1; then
                ok "LiteLLM listo (PID $(cat "$LITELLM_PIDFILE"))"
                break
            fi
        done
        if ! curl -sf http://localhost:4001/health >/dev/null 2>&1; then
            err "LiteLLM no arrancó. Revisa $LITELLM_LOG"
            exit 1
        fi
    fi

    # --- Proxy ---
    if curl -sf http://localhost:4000/ >/dev/null 2>&1; then
        ok "Proxy ya está corriendo en puerto 4000"
    else
        info "Iniciando proxy de filtro en puerto 4000..."
        python3 "$PROXY_SCRIPT" >> "$PROXY_LOG" 2>&1 &
        echo $! > "$PROXY_PIDFILE"
        for i in $(seq 1 20); do
            sleep 0.5
            if curl -sf http://localhost:4000/ >/dev/null 2>&1; then
                ok "Proxy listo (PID $(cat "$PROXY_PIDFILE"))"
                break
            fi
        done
        if ! curl -sf http://localhost:4000/ >/dev/null 2>&1; then
            err "Proxy no arrancó. Revisa $PROXY_LOG"
            exit 1
        fi
    fi

    echo ""
    echo "═══════════════════════════════════════════"
    echo "  Stack listo:"
    echo "    Codex → localhost:4000 (proxy)"
    echo "    proxy → localhost:4001 (LiteLLM)"
    echo "    LiteLLM → $(grep 'model_name:' "$LITELLM_CONFIG" | head -1 | awk '{print $2}')"
    echo ""
    echo "  OPENAI_API_KEY debe estar configurada"
    echo "═══════════════════════════════════════════"
}

case "${1:-}" in
    --stop|-s|stop)
        stop_stack
        ;;
    --status|status)
        status_stack
        ;;
    --help|-h)
        echo "Uso: $0 [--stop|--status|--help]"
        echo "  (sin args)  Inicia el stack completo"
        echo "  --stop      Detiene todos los servicios"
        echo "  --status    Muestra estado de los servicios"
        echo "  --help      Esta ayuda"
        ;;
    *)
        start_stack
        ;;
esac
