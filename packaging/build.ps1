<#
.SYNOPSIS
  LPortMan を Windows 向けに onedir ビルドする。

.DESCRIPTION
  dist/LPortMan/ に lportman.exe (CLI) / lportmanw.exe (窓なし・画面) と _internal/ を作る。
  台帳などのデータは実行時に exe の隣の data/ に作られるので、フォルダごと配布・移動できる。
  手元の data/ (台帳・調査結果) は配布物に入れない。
#>
[CmdletBinding()]
param(
    # PyInstaller のキャッシュを消さない。spec や依存を変えたときは付けないこと
    [switch]$Fast
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 動いている LPortMan (dist のもの) が _internal を掴んでいるとビルドが失敗する
$running = Get-Process lportman, lportmanw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith((Join-Path $root 'dist'), [StringComparison]::OrdinalIgnoreCase) }
if ($running) {
    Write-Warning "dist の LPortMan が動いています。閉じてからビルドしてください: $($running.Path -join ', ')"
    exit 1
}

uv sync --locked --group build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$pyi = @('packaging/lportman.spec', '--noconfirm', '--distpath', 'dist', '--workpath', 'build')
if (-not $Fast) { $pyi += '--clean' }
uv run pyinstaller @pyi
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$out = Join-Path $root 'dist/LPortMan'
# 手元で試した data/ が dist に残っていたら配布物に混ざるので消す
$data = Join-Path $out 'data'
if (Test-Path $data) {
    Remove-Item $data -Recurse -Force
    Write-Host "外した: data/ (台帳・調査結果は配布物に入れない)"
}

Write-Host ""
Write-Host "ビルド完了: $out"
Write-Host "  画面: .\dist\LPortMan\lportmanw.exe"
Write-Host "  CLI : .\dist\LPortMan\lportman.exe suggest 5173"
