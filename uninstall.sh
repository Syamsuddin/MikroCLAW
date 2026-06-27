#!/usr/bin/env bash
#
# Uninstaller MikroCLAW untuk macOS & Linux.
# Melepas registrasi MCP dari Claude Code. Opsional menghapus .env dan .venv.
#
# Contoh:
#   ./uninstall.sh                 # lepas registrasi MCP saja
#   ./uninstall.sh --remove-env    # + hapus .env (kredensial)
#   ./uninstall.sh --remove-venv   # + hapus .venv (dependency)
#
set -o pipefail

REMOVE_ENV="false"
REMOVE_VENV="false"
for a in "$@"; do
    case "$a" in
        --remove-env) REMOVE_ENV="true" ;;
        --remove-venv) REMOVE_VENV="true" ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed '1d'; exit 0 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Melepas registrasi MCP 'mikroclaw' dari Claude Code..."
if command -v claude >/dev/null 2>&1; then
    claude mcp remove mikroclaw --scope user >/dev/null 2>&1 || true
    claude mcp remove mikroclaw --scope project >/dev/null 2>&1 || true
    echo "    [OK] registrasi MCP dilepas (user & project scope bila ada)"
else
    echo "    [!]  CLI 'claude' tidak ada — hapus entri 'mikroclaw' dari .mcp.json secara manual bila perlu."
fi

if [ "$REMOVE_ENV" = "true" ]; then
    rm -f "$SCRIPT_DIR/.env" && echo "    [OK] .env dihapus"
fi
if [ "$REMOVE_VENV" = "true" ]; then
    rm -rf "$SCRIPT_DIR/.venv" && echo "    [OK] .venv dihapus"
fi

echo ""
echo "  MikroCLAW dilepas dari Claude Code. Folder proyek masih ada."
echo ""
