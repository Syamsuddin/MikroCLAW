<#
  Uninstaller MikroCLAW untuk Windows.
  Melepas registrasi MCP dari Claude Code. Opsional menghapus .env dan .venv.

  Contoh:
    .\uninstall.ps1                 # lepas registrasi MCP saja
    .\uninstall.ps1 -RemoveEnv      # + hapus .env (kredensial)
    .\uninstall.ps1 -RemoveVenv     # + hapus .venv (dependency)
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$RemoveEnv,
    [switch]$RemoveVenv
)

$RepoRoot = $PSScriptRoot
function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "    [!]  $m" -ForegroundColor Yellow }

Write-Step "Melepas registrasi MCP 'mikroclaw' dari Claude Code..."
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    & claude mcp remove mikroclaw --scope user 2>$null | Out-Null
    & claude mcp remove mikroclaw --scope project 2>$null | Out-Null
    Write-Ok "Registrasi MCP dilepas (user & project scope bila ada)"
}
else {
    Write-Warn "CLI 'claude' tidak ditemukan — lewati. Hapus entri 'mikroclaw' dari .mcp.json secara manual bila perlu."
}

if ($RemoveEnv) {
    $envPath = Join-Path $RepoRoot ".env"
    if (Test-Path $envPath) { Remove-Item $envPath -Force; Write-Ok ".env dihapus" }
    else { Write-Warn ".env tidak ada" }
}

if ($RemoveVenv) {
    $venv = Join-Path $RepoRoot ".venv"
    if (Test-Path $venv) { Remove-Item $venv -Recurse -Force; Write-Ok ".venv dihapus" }
    else { Write-Warn ".venv tidak ada" }
}

Write-Host ""
Write-Host "  MikroCLAW dilepas dari Claude Code." -ForegroundColor Green
Write-Host "  Folder proyek masih ada; hapus manual bila tidak diperlukan lagi."
Write-Host ""
