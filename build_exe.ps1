# Nexus Downloader - Windows 用 exe ビルドスクリプト
#
# 使い方:
#   pwsh -ExecutionPolicy Bypass -File .\build_exe.ps1
#   pwsh -ExecutionPolicy Bypass -File .\build_exe.ps1 -Addons yt_dlp_addon.py -ZipPath .\NexusDL.zip
#
# 出力:
#   dist\NexusDownloader\NexusDownloader.exe
#   （設定・アドオンは exe と同じフォルダに置かれる = 後から追加・更新できる）
#
# 方針:
#   onefile ではなく onedir を使う。onefile は起動のたびに全ファイルを一時
#   フォルダへ展開するため、起動が数秒遅くなる。onedir はそのまま実行できる。

param(
    # 同梱するアドオンのファイル名。未指定なら addons\*.py をすべて同梱する。
    [string[]]$Addons = @(),
    # 指定すると dist フォルダを zip にまとめる（GitHub Release 用）。
    [string]$ZipPath = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "[1/3] PyInstaller でビルド中..." -ForegroundColor Cyan
python -m PyInstaller NexusDownloader.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller のビルドに失敗しました (exit $LASTEXITCODE)"
}

$dist = Join-Path $PSScriptRoot "dist\NexusDownloader"
if (-not (Test-Path $dist)) {
    throw "出力フォルダが見つかりません: $dist"
}

Write-Host "[2/3] 設定・アドオンを exe の隣へコピー中..." -ForegroundColor Cyan

foreach ($file in @("config.json", "version.txt", "manifest.json", "README.md", "LICENSE")) {
    $src = Join-Path $PSScriptRoot $file
    if (Test-Path $src) {
        Copy-Item $src -Destination $dist -Force
    }
}

# 外部アドオン（.py）だけをコピーする（__pycache__ は含めない）
$addonsDst = Join-Path $dist "addons"
New-Item -ItemType Directory -Force -Path $addonsDst | Out-Null
$allAddons = @(Get-ChildItem (Join-Path $PSScriptRoot "addons") -Filter *.py -File)
if ($Addons.Count -gt 0) {
    $selected = @($allAddons | Where-Object { $Addons -contains $_.Name })
    $missing = @($Addons | Where-Object { $n = $_; -not ($allAddons | Where-Object { $_.Name -eq $n }) })
    if ($missing.Count -gt 0) {
        throw "指定されたアドオンが見つかりません: $($missing -join ', ')"
    }
} else {
    $selected = $allAddons
}
$selected | ForEach-Object { Copy-Item $_.FullName -Destination $addonsDst -Force }

# プラグイン用フォルダ（空でも作成）
New-Item -ItemType Directory -Force -Path (Join-Path $dist "plugins") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $dist "downloads") | Out-Null

# 任意: cookies.txt があれば同梱（yt-dlp アドオンのログイン用）
$cookies = Join-Path $PSScriptRoot "cookies.txt"
if (Test-Path $cookies) {
    Copy-Item $cookies -Destination $dist -Force
}

# 任意: GitHub Release 用の zip を作成（.NET の ZipFile を使用 = 高速）
if ($ZipPath) {
    $zipFull = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $ZipPath))
    if (Test-Path $zipFull) { Remove-Item $zipFull -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $dist, $zipFull,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $true)
    Write-Host "  zip: $zipFull" -ForegroundColor Cyan
}

$bundled = ($selected | ForEach-Object { $_.Name }) -join ", "
Write-Host "[3/3] 完了" -ForegroundColor Green
Write-Host "  $dist\NexusDownloader.exe"
Write-Host "  同梱アドオン: $bundled"
Write-Host "  （フォルダごと配布してください。設定は config.json、アドオンは addons\ にあります）"

