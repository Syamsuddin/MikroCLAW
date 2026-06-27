#!/usr/bin/env bash
#
# Bootstrap MikroCLAW untuk macOS & Linux — clone repo lalu jalankan installer.
# Instalasi satu baris tanpa clone manual:
#
#   curl -LsSf https://raw.githubusercontent.com/Syamsuddin/MikroCLAW/main/bootstrap.sh | bash
#
# Lokasi default: $HOME/MikroCLAW  (ubah dengan env MIKROCLAW_DIR).
#
set -o pipefail

DIR="${MIKROCLAW_DIR:-$HOME/MikroCLAW}"
REPO="${MIKROCLAW_REPO:-https://github.com/Syamsuddin/MikroCLAW.git}"

printf '\n  MikroCLAW Bootstrap (macOS / Linux)\n\n'

if ! command -v git >/dev/null 2>&1; then
    echo "git diperlukan tetapi tidak ditemukan." >&2
    echo "  macOS : jalankan 'xcode-select --install'" >&2
    echo "  Debian/Ubuntu: 'sudo apt install -y git'" >&2
    echo "  Fedora/RHEL  : 'sudo dnf install -y git'" >&2
    exit 1
fi

if [ -d "$DIR/.git" ]; then
    echo "==> Repo sudah ada di $DIR — git pull..."
    ( cd "$DIR" && git pull --ff-only ) || { echo "git pull gagal" >&2; exit 1; }
else
    echo "==> Clone $REPO -> $DIR"
    git clone "$REPO" "$DIR" || { echo "git clone gagal" >&2; exit 1; }
fi
echo "    [OK] Repo siap di $DIR"

echo "==> Menjalankan installer..."
exec bash "$DIR/install.sh" "$@"
