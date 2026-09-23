#Requires -Version 5.1
<#
.SYNOPSIS
    Python 側 (jp_pii_mask) を PyInstaller で単一実行ファイル化し、
    Tauri が期待する名前で desktop\binaries\ に配置する。

.DESCRIPTION
    - 出力先 (dist / build / spec) はすべて desktop\ 配下に固定する。
      リポジトリルートをビルド生成物で汚さないため。
    - リポジトリルートは読み取り専用の入力 (--paths) としてのみ参照する。
    - __pycache__ を作らせないため PYTHONDONTWRITEBYTECODE=1 を設定する。
    - エントリは scripts\sidecar_entry.py を使う。
      jp_pii_mask\cli.py を直接指定すると相対 import が解決できず失敗する。
    - このファイルは UTF-8 (BOM 付き) で保存すること。
      Windows PowerShell 5.1 は BOM の無い UTF-8 を CP932 として読むため、
      日本語コメントが文字化けし、引用符が壊れて構文エラーになりうる。

.NOTES
    検証状況: このスクリプトの PyInstaller 実行部分は Windows 上で実際に
    成功を確認済み (jp-pii-mask-server.exe を生成)。Rust 側 (cargo) は未検証。

.EXAMPLE
    .\desktop\scripts\build-sidecar.ps1
    .\desktop\scripts\build-sidecar.ps1 -SkipDeps
    .\desktop\scripts\build-sidecar.ps1 -Python python
#>
[CmdletBinding()]
param(
    # 使用する Python。例: 'python' / 'py' に '-3.12' を続けて渡す。
    [string[]]$Python = @('python'),

    # 依存パッケージのインストールを省略する。
    [switch]$SkipDeps
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# パス解決: このファイルは desktop\scripts\ にある
# ---------------------------------------------------------------------------
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$DesktopDir = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$RepoRoot   = (Resolve-Path (Join-Path $DesktopDir '..')).Path

$BinDir   = Join-Path $DesktopDir 'binaries'
$DistDir  = Join-Path $DesktopDir 'dist'
$BuildDir = Join-Path $DesktopDir 'build'
$EntryPy  = Join-Path $RepoRoot   'scripts\sidecar_entry.py'

Write-Host "リポジトリルート: $RepoRoot"
Write-Host "desktop         : $DesktopDir"

if (-not (Test-Path $EntryPy)) {
    throw "エントリポイントが見つかりません: $EntryPy"
}

# ---------------------------------------------------------------------------
# リポジトリ側を汚さないための設定
# ---------------------------------------------------------------------------
$env:PYTHONDONTWRITEBYTECODE = '1'

# ---------------------------------------------------------------------------
# 1) 依存パッケージのインストール
# ---------------------------------------------------------------------------
$PyExe  = $Python[0]
$PyArgs = @()
if ($Python.Count -gt 1) { $PyArgs = $Python[1..($Python.Count - 1)] }

if (-not $SkipDeps) {
    Write-Host "依存パッケージをインストールします..." -ForegroundColor Cyan
    & $PyExe @PyArgs -m pip install --upgrade pip pyinstaller fastapi uvicorn pydantic cryptography httpx
    if ($LASTEXITCODE -ne 0) { throw "pip install が失敗しました (exit $LASTEXITCODE)" }
} else {
    Write-Host "依存のインストールをスキップします (-SkipDeps)" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 2) PyInstaller 実行 (出力は desktop\ 配下のみ)
# ---------------------------------------------------------------------------
Write-Host "PyInstaller を実行します..." -ForegroundColor Cyan

$pyiArgs = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--name', 'jp-pii-mask-server',
    '--distpath', $DistDir,
    '--workpath', $BuildDir,
    '--specpath', $BuildDir,
    '--paths', $RepoRoot,
    '--collect-all', 'jp_pii_mask',
    # uvicorn は動的にモジュールを読み込むため、明示的に含める必要がある。
    '--hidden-import', 'uvicorn.logging',
    '--hidden-import', 'uvicorn.loops.auto',
    '--hidden-import', 'uvicorn.protocols.http.auto',
    '--hidden-import', 'uvicorn.protocols.websockets.auto',
    '--hidden-import', 'uvicorn.lifespan.on',
    '--console',
    $EntryPy
)

& $PyExe @PyArgs @pyiArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller が失敗しました (exit $LASTEXITCODE)" }

$built = Join-Path $DistDir 'jp-pii-mask-server.exe'
if (-not (Test-Path $built)) { throw "生成物が見つかりません: $built" }

# ---------------------------------------------------------------------------
# 3) Tauri が期待するターゲットトリプル名でコピー
# ---------------------------------------------------------------------------
$triple = $null
try {
    $rustcOut = & rustc -vV 2>$null
    foreach ($line in $rustcOut) {
        if ($line -match '^host:\s*(\S+)') { $triple = $Matches[1]; break }
    }
} catch {
    # rustc 未導入
}

if ([string]::IsNullOrWhiteSpace($triple)) {
    $triple = 'x86_64-pc-windows-msvc'
    Write-Warning "rustc が見つからないため、ターゲットトリプルを $triple と仮定します。"
    Write-Warning "異なる場合は手動でリネームしてください (rustup 導入後に rustc -vV で確認)。"
}

if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir | Out-Null }

$target = Join-Path $BinDir "jp-pii-mask-server-$triple.exe"
Copy-Item -Path $built -Destination $target -Force

Write-Host ""
Write-Host "完了しました。" -ForegroundColor Green
Write-Host "  生成物 : $built"
Write-Host "  配置先 : $target"
Write-Host ""
Write-Host "次の手順:" -ForegroundColor Cyan
Write-Host "  cd `"$DesktopDir\src-tauri`" ; cargo tauri dev"
