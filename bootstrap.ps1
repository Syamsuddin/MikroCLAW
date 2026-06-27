<#
  Bootstrap MikroCLAW untuk Windows — clone repo lalu jalankan installer.
  Dipakai untuk instalasi satu baris tanpa meng-clone manual:

    irm https://raw.githubusercontent.com/Syamsuddin/MikroCLAW/main/bootstrap.ps1 | iex

  Lokasi default: %USERPROFILE%\MikroCLAW (ubah dengan -Dir).
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Dir = (Join-Path $env:USERPROFILE "MikroCLAW"),
    [string]$RepoUrl = "https://github.com/Syamsuddin/MikroCLAW.git"
)

$ErrorActionPreference = "Stop"
function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "    [OK] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "    [!]  $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  MikroCLAW Bootstrap (Windows)" -ForegroundColor Magenta
Write-Host ""

# Pastikan git ada
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Step "git belum ada — memasang via winget..."
    winget install --id=Git.Git -e --accept-source-agreements --accept-package-agreements
    $gitCmd = Join-Path $env:ProgramFiles "Git\cmd"
    if (Test-Path $gitCmd) { $env:Path = "$gitCmd;$env:Path" }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git tidak ditemukan setelah instalasi. Tutup & buka ulang PowerShell, lalu coba lagi."
    }
}

# Clone atau update
if (Test-Path (Join-Path $Dir ".git")) {
    Write-Step "Repo sudah ada di $Dir — git pull..."
    Push-Location $Dir; try { git pull --ff-only } finally { Pop-Location }
}
else {
    Write-Step "Clone $RepoUrl -> $Dir"
    git clone $RepoUrl $Dir
}
Write-Ok "Repo siap di $Dir"

# Jalankan installer
Write-Step "Menjalankan installer..."
& (Join-Path $Dir "install.ps1")
