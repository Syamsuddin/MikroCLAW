#!/usr/bin/env bash
#
# Installer MikroCLAW untuk macOS & Linux.
# Memasang uv (jika perlu) + dependency, mengonfigurasi .env, dan mendaftarkan
# MCP server "mikroclaw" ke Claude Code.
#
# Contoh:
#   ./install.sh
#   ./install.sh --host 192.168.88.1 --user mikroclaw --non-interactive
#   ./install.sh --allow-write           # izinkan operasi write
#   ./install.sh --skip-mcp              # jangan daftarkan ke Claude Code
#
set -o pipefail

HOST=""
USER_RO="mikroclaw"
PASS=""
SCHEME="https"
ALLOW_WRITE="false"
NONINTERACTIVE="false"
SKIP_ENV="false"
SKIP_MCP="false"

usage() {
    grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed '1d'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --user) USER_RO="$2"; shift 2 ;;
        --password) PASS="$2"; shift 2 ;;
        --http) SCHEME="http"; shift ;;
        --allow-write) ALLOW_WRITE="true"; shift ;;
        --non-interactive) NONINTERACTIVE="true"; shift ;;
        --skip-env) SKIP_ENV="true"; shift ;;
        --skip-mcp) SKIP_MCP="true"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Argumen tak dikenal: $1" >&2; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

if [ -t 1 ]; then
    C_CY=$'\033[36m'; C_GR=$'\033[32m'; C_YL=$'\033[33m'; C_MA=$'\033[35m'; C_RS=$'\033[0m'
else
    C_CY=""; C_GR=""; C_YL=""; C_MA=""; C_RS=""
fi
step() { printf '%s==> %s%s\n' "$C_CY" "$1" "$C_RS"; }
ok()   { printf '    %s[OK]%s %s\n' "$C_GR" "$C_RS" "$1"; }
warn() { printf '    %s[!]%s  %s\n' "$C_YL" "$C_RS" "$1"; }
die()  { printf '    %s[X]%s  %s\n' "$C_YL" "$C_RS" "$1" >&2; exit 1; }

printf '\n%s  MikroCLAW Installer (macOS / Linux)%s\n' "$C_MA" "$C_RS"
printf '  Repo: %s\n\n' "$REPO_ROOT"

# --- 1) Pastikan uv terpasang -------------------------------------------------
ensure_uv() {
    if command -v uv >/dev/null 2>&1; then ok "uv sudah terpasang"; return; fi
    for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        if [ -x "$d/uv" ]; then export PATH="$d:$PATH"; ok "uv ditemukan di $d"; return; fi
    done
    step "uv belum ada — memasang via skrip resmi Astral..."
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        die "Gagal memasang uv. Pasang manual: https://docs.astral.sh/uv/ lalu ulangi."
    fi
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    command -v uv >/dev/null 2>&1 || die "uv belum di PATH. Buka ulang terminal lalu jalankan installer lagi."
    ok "uv terpasang"
}
step "Memeriksa uv (pengelola Python/dependency)..."
ensure_uv

# --- 2) Pasang dependency (uv mengunduh Python bila perlu) --------------------
step "Memasang dependency (uv sync)..."
( cd "$REPO_ROOT" && uv sync ) || die "uv sync gagal."
ok "Dependency siap"

# --- 3) Konfigurasi .env ------------------------------------------------------
configure_env() {
    [ "$SKIP_ENV" = "true" ] && { warn "Lewati konfigurasi .env (--skip-env)"; return; }
    ENV_PATH="$REPO_ROOT/.env"
    if [ -f "$ENV_PATH" ]; then
        if [ "$NONINTERACTIVE" = "true" ]; then warn ".env sudah ada — dibiarkan"; return; fi
        printf '.env sudah ada. Timpa? (y/N) '; read -r ans
        case "$ans" in [Yy]*) ;; *) warn ".env dibiarkan"; return ;; esac
    fi
    if [ "$NONINTERACTIVE" != "true" ]; then
        step "Konfigurasi koneksi RouterOS"
        [ -z "$HOST" ] && { printf '  Host/IP RouterOS (mis. 192.168.88.1): '; read -r HOST; }
        printf '  User RouterOS [%s]: ' "$USER_RO"; read -r u; [ -n "$u" ] && USER_RO="$u"
        if [ -z "$PASS" ]; then printf '  Password RouterOS: '; read -r -s PASS; echo; fi
        printf '  Pakai HTTPS? (Y/n): '; read -r s; case "$s" in [Nn]*) SCHEME="http" ;; esac
        if [ "$ALLOW_WRITE" != "true" ]; then
            printf '  Izinkan operasi WRITE (ubah konfigurasi)? (y/N): '; read -r w
            case "$w" in [Yy]*) ALLOW_WRITE="true" ;; esac
        fi
    fi
    [ -z "$HOST" ] && die "Host RouterOS wajib diisi (pakai --host atau jawab prompt)."
    if [ "$SCHEME" = "https" ]; then USE_TLS="true"; PORT="443"; else USE_TLS="false"; PORT="80"; fi

    ( umask 077; cat > "$ENV_PATH" <<EOF
MIKROTIK_HOST=$HOST
MIKROTIK_USER=$USER_RO
MIKROTIK_PASSWORD=$PASS
MIKROTIK_USE_TLS=$USE_TLS
MIKROTIK_PORT=$PORT
MIKROTIK_VERIFY_TLS=false
MIKROTIK_TIMEOUT=10
MIKROCLAW_ALLOW_WRITE=$ALLOW_WRITE
EOF
    )
    chmod 600 "$ENV_PATH" 2>/dev/null || true
    ok ".env ditulis (scheme=$SCHEME, write=$ALLOW_WRITE, mode 600)"
}
configure_env

# --- 4) Daftarkan MCP server ke Claude Code -----------------------------------
register_mcp() {
    [ "$SKIP_MCP" = "true" ] && { warn "Lewati registrasi MCP (--skip-mcp)"; return; }
    step "Menyiapkan registrasi MCP server 'mikroclaw'..."
    esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
    ESC_ROOT="$(esc "$REPO_ROOT")"
    cat > "$REPO_ROOT/.mcp.json" <<EOF
{
  "mcpServers": {
    "mikroclaw": {
      "command": "uv",
      "args": ["run", "--directory", "$ESC_ROOT", "mikroclaw"]
    }
  }
}
EOF
    ok ".mcp.json diperbarui (path: $REPO_ROOT)"

    if command -v claude >/dev/null 2>&1; then
        claude mcp remove mikroclaw --scope user >/dev/null 2>&1 || true
        if claude mcp add mikroclaw --scope user -- uv run --directory "$REPO_ROOT" mikroclaw >/dev/null 2>&1; then
            ok "Terdaftar di Claude Code (user scope) — aktif di semua proyek"
        else
            warn "Gagal 'claude mcp add' — .mcp.json tetap aktif untuk proyek ini"
        fi
    else
        warn "CLI 'claude' tidak ditemukan. Server aktif saat folder proyek dibuka di Claude Code."
    fi
}
register_mcp

# --- 5) Verifikasi ------------------------------------------------------------
step "Verifikasi: memuat MCP server & menghitung tool..."
COUNT="$( cd "$REPO_ROOT" && uv run python -c 'import asyncio; from mikroclaw.server import mcp; print(len(asyncio.run(mcp.list_tools())))' 2>/dev/null )"
if [ -n "$COUNT" ]; then ok "MCP server OK — $COUNT tool terdaftar"; else warn "Verifikasi tool gagal (cek deps/koneksi)"; fi

printf '\n%s  Selesai!%s\n' "$C_GR" "$C_RS"
printf '  Langkah berikutnya:\n'
printf '    1. Buka folder ini di Claude Code (atau restart Claude Code).\n'
printf '    2. Jalankan /mcp dan pastikan "mikroclaw" connected.\n'
printf '    3. Coba minta: "tampilkan resource & versi RouterOS".\n\n'
