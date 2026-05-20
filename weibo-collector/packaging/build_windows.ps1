# CSL Sentinel Windows build script (run from weibo-collector)
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -SkipVendor

param(
    [switch]$SkipVendor,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== CSL Sentinel Windows Build ==" -ForegroundColor Cyan
Write-Host "Root: $Root"

Write-Host "`n[1/5] Installing build dependencies ..." -ForegroundColor Yellow
python -m pip install -r requirements.txt -q
python -m pip install 'pyinstaller>=6.0' -q

if (-not $SkipVendor) {
    Write-Host "`n[2/5] Downloading vendor models and Chromium (slow, needs network) ..." -ForegroundColor Yellow
    python packaging/prepare_vendor.py
} else {
    Write-Host "`n[2/5] Skip vendor download (-SkipVendor)" -ForegroundColor Yellow
}

if (-not (Test-Path (Join-Path $Root "vendor\models"))) {
    Write-Host "WARN: vendor\models missing; semantic/sentiment may need network." -ForegroundColor Red
}
if (-not (Test-Path (Join-Path $Root "vendor\browsers"))) {
    Write-Host "WARN: vendor\browsers missing; Weibo collect may fail." -ForegroundColor Red
}

Write-Host "`n[3/5] PyInstaller ..." -ForegroundColor Yellow
$buildDir = Join-Path $Root "build"
$distDir = Join-Path $Root "dist\CSLSentinel"
if (Test-Path $buildDir) { Remove-Item -LiteralPath $buildDir -Recurse -Force }
if (Test-Path $distDir) { Remove-Item -LiteralPath $distDir -Recurse -Force }
python -m PyInstaller packaging/CSLSentinel.spec --noconfirm

$Dist = Join-Path $Root "dist\CSLSentinel"
if (-not (Test-Path $Dist)) {
    throw "dist\CSLSentinel not found after PyInstaller"
}

Write-Host "`n[4/5] Copy vendor and empty dirs ..." -ForegroundColor Yellow
$vendorSrc = Join-Path $Root "vendor"
if (Test-Path $vendorSrc) {
    Copy-Item -LiteralPath $vendorSrc -Destination (Join-Path $Dist "vendor") -Recurse -Force
}
foreach ($dir in @("data", "reports", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Dist $dir) | Out-Null
}
Copy-Item -Force (Join-Path $Root ".env.example") (Join-Path $Dist ".env.example")
Copy-Item -Force (Join-Path $Root "packaging\USER_GUIDE.txt") (Join-Path $Dist "使用说明.txt")

Write-Host "`n[5/5] Inno Setup installer ..." -ForegroundColor Yellow
$iss = Join-Path $Root "packaging\CSLSentinel.iss"
$isccCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    "D:\Program Files\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($SkipInstaller) {
    Write-Host "Skip installer (-SkipInstaller)" -ForegroundColor Yellow
} elseif ($iscc) {
    & $iscc $iss
    Write-Host "Installer: $(Join-Path $Root 'dist\CSLSentinel_Setup.exe')" -ForegroundColor Green
} else {
    Write-Host "Inno Setup 6 not found; green build only at dist\CSLSentinel" -ForegroundColor Yellow
}

$mainExe = Join-Path $Dist "CSLSentinel.exe"
Write-Host "`nDone. Folder: $Dist" -ForegroundColor Green
Write-Host "Main exe: $mainExe" -ForegroundColor Green
