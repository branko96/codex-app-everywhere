#!/bin/bash
# Codex for macOS — Wrapper Launcher
# 1. Starts LiteLLM + proxy stack
# 2. Opens Codex.app (already patched)
#
# Usage:
#   ./codex-mac-launcher.sh               # Start stack + open Codex
#   ./codex-mac-launcher.sh --cli         # Just start stack (use CLI manually)
#   ./codex-mac-launcher.sh --stack-only  # Only start stack, don't open app
#   ./codex-mac-launcher.sh --stop        # Stop stack
#   ./codex-mac-launcher.sh --status      # Check status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
START_STACK="${REPO_ROOT}/stack/launcher/start-stack.sh"

# Colors
CYAN='\033[0;36m'
GREEN='\033[0;32m'
NC='\033[0m'

case "${1:-}" in
    --cli)
        # Just start the stack, user will use CLI manually
        bash "$START_STACK"
        echo -e "${GREEN}[→]${NC} Ahora usa 'codex' en la terminal (o abre una nueva terminal)"
        ;;
    --stack-only)
        bash "$START_STACK"
        ;;
    --stop)
        bash "$START_STACK" --stop
        ;;
    --status)
        bash "$START_STACK" --status
        ;;
    --help|-h)
        echo "Uso: $0 [--cli|--stack-only|--stop|--status|--help]"
        echo "  (sin args)  Inicia stack + abre Codex.app"
        echo "  --cli       Solo inicia el stack (usa CLI manualmente)"
        echo "  --stop      Detiene servicios"
        echo "  --status    Muestra estado"
        exit 0
        ;;
    *)
        bash "$START_STACK"
        echo -e "${CYAN}[*]${NC} Abriendo Codex.app..."
        open -a "Codex" 2>/dev/null || open /Applications/Codex.app 2>/dev/null || \
            echo -e "${YELLOW}[!]${NC} No se pudo abrir Codex.app. Ábrela manualmente."
        ;;
esac
