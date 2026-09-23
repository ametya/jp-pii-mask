<#
.SYNOPSIS
    JP PII Mask のデスクトップアプリ (インストーラ) を、これ 1 つで作ります。

.DESCRIPTION
    非エンジニアの方でも迷わないように、順番に自動で進めます。

      1. Rust ツールチェーンの確認 (無ければ案内)
      2. Python サイドカーのビルド
      3. 型検査 (cargo check) ← 最初の関門
      4. Tauri CLI の確認 (無ければ導入)
      5. インストーラの作成 (cargo tauri build)
      6. できたファイルの場所を表示

    実体は Python の build-installer.py です。PowerShell から呼びやすいように
    このラッパーを置いています (ダブルクリックに近い感覚で使えるように)。

.EXAMPLE
    .\desktop\build-installer.ps1

.EXAMPLE
    # 型検査だけで止める (GUI がビルドできるかの確認)
    .\desktop\build-installer.ps1 -CheckOnly

.NOTES
    このファイルは UTF-8 (BOM 付き) で保存すること。
    Windows PowerShell 5.1 は BOM の無い UTF-8 を CP932 として読むため、
    日本語コメントが文字化けし、引用符が壊れて構文エラーになりうる。
#>
[CmdletBinding()]
param(
    # 使用する Python
    [string]$Python = 'python',

    # 型検査だけで止める
    [switch]$CheckOnly,

    # サイドカーを作り直さない
    [switch]$SkipSidecar
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Builder   = Join-Path $ScriptDir 'scripts\build-installer.py'

if (-not (Test-Path $Builder)) {
    throw "ビルドスクリプトが見つかりません: $Builder"
}

$pyArgs = @($Builder)
if ($CheckOnly)   { $pyArgs += '--check-only' }
if ($SkipSidecar) { $pyArgs += '--skip-sidecar' }

Write-Host "デスクトップアプリのビルドを開始します..." -ForegroundColor Cyan
& $Python @pyArgs
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ""
    Write-Host "ビルドに失敗しました (exit $code)。" -ForegroundColor Red
    Write-Host "上のメッセージに対処方法が書かれています。" -ForegroundColor Yellow
    exit $code
}

Write-Host ""
Write-Host "すべて完了しました。" -ForegroundColor Green
