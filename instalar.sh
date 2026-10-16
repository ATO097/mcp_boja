#!/usr/bin/env sh
# Instalador de MCP-BOJA (macOS / Linux)
set -e
cd "$(dirname "$0")"
command -v uv >/dev/null 2>&1 || { echo "Instalando uv..."; curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
uv tool install --force --reinstall .
uv tool update-shell >/dev/null || true
"$(uv tool dir --bin)/mcp-boja-configurar" "$@"
echo "Listo. Reinicia Claude Desktop."
