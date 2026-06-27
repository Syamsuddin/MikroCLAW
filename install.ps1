<#
  Installer MikroCLAW untuk Windows.
  Memasang uv (jika perlu) + dependency, mengonfigurasi .env, dan mendaftarkan
  MCP server "mikroclaw" ke Claude Code.

  Contoh:
    .\install.ps1
    .\install.ps1 -MikrotikHost 192.168.88.1 -MikrotikUser mikroclaw -NonInteractive
    .\install.ps1 -AllowWrite          # izinkan operasi write
    .\install.ps1 -SkipMcpRegister     # jangan daftarkan ke Claude Code
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$MikrotikHost,
    [string]$MikrotikUser = "mikroclaw",
    [string]$MikrotikPassword,
    [ValidateSet("https", "http")][string]$Scheme = "https",
    [switch]$AllowWrite,
    [switch]$NonInteractive,
    [switch]$SkipEnv,
    [switch]$SkipMcpRegister
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "    [!]  $m" -ForegroundColor Yellow }

# Tulis teks sebagai UTF-8 TANPA BOM (BOM merusak parsing JSON & baris pertama .env)
function Set-Utf8NoBom($Path, $Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

Write-Host ""
Write-Host "  +--------------------------------------+" -ForegroundColor Magenta
Write-Host "  |  MikroCLAW Installer (Windows)       |" -ForegroundColor Magenta
Write-Host "  +--------------------------------------+" -ForegroundColor Magenta
Write-Host "  Repo: $RepoRoot"
Write-Host ""

# --- 1) Pastikan uv terpasang -------------------------------------------------
function Resolve-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
            (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\uv.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe"))) {
        if (Test-Path $p) {
            $env:Path = "$(Split-Path $p);$env:Path"
            return $p
        }
    }
    return $null
}

Write-Step "Memeriksa uv (pengelola Python/dependency)..."
if (Resolve-Uv) {
    Write-Ok "uv sudah terpasang"
}
else {
    Write-Step "uv belum ada — memasang via skrip resmi Astral..."
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
    catch {
        Write-Warn "Skrip resmi gagal, mencoba winget..."
        winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    }
    if (-not (Resolve-Uv)) {
        throw "uv tidak ditemukan setelah instalasi. Tutup & buka ulang PowerShell, lalu jalankan installer lagi."
    }
    Write-Ok "uv terpasang"
}

# --- 2) Pasang dependency (uv mengunduh Python bila perlu) --------------------
Write-Step "Memasang dependency (uv sync)..."
Push-Location $RepoRoot
try { uv sync } finally { Pop-Location }
Write-Ok "Dependency siap"

# --- 3) Konfigurasi .env ------------------------------------------------------
if ($SkipEnv) {
    Write-Warn "Lewati konfigurasi .env (-SkipEnv)"
}
else {
    $envPath = Join-Path $RepoRoot ".env"
    $writeEnv = $true
    if (Test-Path $envPath) {
        if ($NonInteractive) {
            $writeEnv = $false; Write-Warn ".env sudah ada — dibiarkan"
        }
        else {
            $ans = Read-Host ".env sudah ada. Timpa? (y/N)"
            if ($ans -notmatch '^[Yy]') { $writeEnv = $false; Write-Warn ".env dibiarkan" }
        }
    }

    if ($writeEnv) {
        if (-not $NonInteractive) {
            Write-Host ""
            Write-Step "Konfigurasi koneksi RouterOS"
            if (-not $MikrotikHost) { $MikrotikHost = Read-Host "  Host/IP RouterOS (mis. 192.168.88.1)" }
            $u = Read-Host "  User RouterOS [$MikrotikUser]"; if ($u) { $MikrotikUser = $u }
            if (-not $MikrotikPassword) {
                $sec = Read-Host "  Password RouterOS" -AsSecureString
                $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
                $MikrotikPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
            $s = Read-Host "  Pakai HTTPS? (Y/n)"; if ($s -match '^[Nn]') { $Scheme = "http" }
            if (-not $AllowWrite) {
                $w = Read-Host "  Izinkan operasi WRITE (ubah konfigurasi)? (y/N)"
                if ($w -match '^[Yy]') { $AllowWrite = $true }
            }
        }
        if (-not $MikrotikHost) { throw "MikrotikHost wajib diisi (pakai -MikrotikHost atau jawab prompt)." }

        $useTls = if ($Scheme -eq "https") { "true" } else { "false" }
        $port   = if ($Scheme -eq "https") { "443" } else { "80" }
        $allow  = if ($AllowWrite) { "true" } else { "false" }

        $lines = @(
            "MIKROTIK_HOST=$MikrotikHost",
            "MIKROTIK_USER=$MikrotikUser",
            "MIKROTIK_PASSWORD=$MikrotikPassword",
            "MIKROTIK_USE_TLS=$useTls",
            "MIKROTIK_PORT=$port",
            "MIKROTIK_VERIFY_TLS=false",
            "MIKROTIK_TIMEOUT=10",
            "MIKROCLAW_ALLOW_WRITE=$allow"
        )
        Set-Utf8NoBom $envPath (($lines -join "`r`n") + "`r`n")
        Write-Ok ".env ditulis (scheme=$Scheme, write=$allow)"
    }
}

# --- 4) Daftarkan MCP server ke Claude Code -----------------------------------
if ($SkipMcpRegister) {
    Write-Warn "Lewati registrasi MCP (-SkipMcpRegister)"
}
else {
    Write-Step "Menyiapkan registrasi MCP server 'mikroclaw'..."

    # 4a. Selalu perbaiki .mcp.json proyek agar path-nya benar untuk mesin ini.
    $mcp = [ordered]@{
        mcpServers = [ordered]@{
            mikroclaw = [ordered]@{
                command = "uv"
                args    = @("run", "--directory", "$RepoRoot", "mikroclaw")
            }
        }
    }
    $json = $mcp | ConvertTo-Json -Depth 6
    Set-Utf8NoBom (Join-Path $RepoRoot ".mcp.json") $json
    Write-Ok ".mcp.json diperbarui (path: $RepoRoot)"

    # 4b. Bila CLI 'claude' ada, daftarkan juga di user-scope (tersedia global).
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if ($claude) {
        & claude mcp remove mikroclaw --scope user 2>$null | Out-Null
        & claude mcp add mikroclaw --scope user -- uv run --directory "$RepoRoot" mikroclaw 2>$null | Out-Null
        Write-Ok "Terdaftar di Claude Code (user scope) — aktif di semua proyek"
    }
    else {
        Write-Warn "CLI 'claude' tidak ditemukan. Server aktif saat folder proyek dibuka di Claude Code."
        Write-Warn "Untuk global: install Claude Code CLI, lalu jalankan installer lagi."
    }
}

# --- 5) Verifikasi ------------------------------------------------------------
Write-Step "Verifikasi: memuat MCP server & menghitung tool..."
Push-Location $RepoRoot
try {
    $count = uv run python -c "import asyncio; from mikroclaw.server import mcp; print(len(asyncio.run(mcp.list_tools())))"
    Write-Ok "MCP server OK — $count tool terdaftar"
}
catch {
    Write-Warn "Verifikasi tool gagal: $($_.Exception.Message)"
}
finally { Pop-Location }

Write-Host ""
Write-Host "  Selesai!" -ForegroundColor Green
Write-Host "  Langkah berikutnya:" -ForegroundColor Green
Write-Host "    1. Buka folder ini di Claude Code (atau restart Claude Code)."
Write-Host "    2. Jalankan /mcp dan pastikan 'mikroclaw' connected."
Write-Host "    3. Coba minta: 'tampilkan resource & versi RouterOS'."
Write-Host ""
Write-Host "  Uji cepat tanpa Claude:" -ForegroundColor DarkGray
Write-Host "    uv run python -c `"import asyncio;from mikroclaw.server import mcp;print(len(asyncio.run(mcp.list_tools())),'tools')`""
Write-Host ""
