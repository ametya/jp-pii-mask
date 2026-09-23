<#
.SYNOPSIS
    Rust のセットアップから Tauri GUI のビルド、Python サイドカーの同梱、
    インストーラ (.msi) の作成までを全自動で行います。(Windows)

.DESCRIPTION
    これ 1 つで最後まで進みます。エンジニアでない方でも実行できます。

      1. Rust ツールチェーンの確認 (無ければ自動導入)
      2. Python の確認
      3. Python サイドカーのビルド (PyInstaller で単一 exe 化)
      4. Tauri プロジェクトの型検査 (cargo check) ← 最初の関門
      5. Tauri CLI の確認 (無ければ自動導入)
      6. GUI のビルド + インストーラ (.msi / setup.exe) の作成
      7. できあがったファイルの場所を表示

    途中で失敗した場合、どこで何をすればよいかを日本語で表示します。

.PARAMETER CheckOnly
    型検査 (手順 4) までで止めます。まず GUI がビルドできるかだけを
    確かめたいときに使います (インストーラは作りません)。

.PARAMETER SkipSidecar
    サイドカーのビルド (手順 3) を省略します。既に作ってある場合に。

.PARAMETER Python
    使用する Python。既定は 'python'。例: -Python 'py'

.EXAMPLE
    .\setup_and_build.ps1

.EXAMPLE
    # まず型検査だけ
    .\setup_and_build.ps1 -CheckOnly

.NOTES
    このファイルは UTF-8 (BOM 付き) で保存すること。
    Windows PowerShell 5.1 は BOM の無い UTF-8 を CP932 として読むため、
    日本語コメントが文字化けし、引用符が壊れて構文エラーになりうる。

    実処理の本体は desktop\scripts\ の Python スクリプトです。
    そちらは Windows / macOS / Linux で同じように動きます。
#>
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [switch]$CheckOnly,
    [switch]$SkipSidecar
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# パス解決: このファイルはリポジトリルートにある
# ---------------------------------------------------------------------------
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SetupRust     = Join-Path $RepoRoot 'desktop\scripts\setup-rust.py'
$BuildInstaller = Join-Path $RepoRoot 'desktop\scripts\build-installer.py'

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host ('=' * 62) -ForegroundColor DarkGray
    Write-Host $Text -ForegroundColor Cyan
    Write-Host ('=' * 62) -ForegroundColor DarkGray
}

function Fail([string]$Message, [string[]]$Hints) {
    Write-Host ''
    Write-Host "エラー: $Message" -ForegroundColor Red
    if ($Hints) {
        Write-Host ''
        foreach ($h in $Hints) { Write-Host "  $h" -ForegroundColor Yellow }
    }
    Write-Host ''
    exit 1
}

foreach ($f in @($SetupRust, $BuildInstaller)) {
    if (-not (Test-Path $f)) {
        Fail "必要なスクリプトが見つかりません: $f" @('リポジトリを正しく取得できているか確認してください。')
    }
}

# ---------------------------------------------------------------------------
# 1) Python の確認
# ---------------------------------------------------------------------------
Write-Step 'Python の確認'

try {
    $pyVersion = & $Python --version 2>&1
} catch {
    Fail "Python が見つかりません (コマンド: $Python)" @(
        'https://www.python.org/downloads/ から導入してください。',
        "別の名前で入っている場合は -Python で指定できます。例: -Python 'py'"
    )
}

if ($LASTEXITCODE -ne 0) {
    Fail "Python を実行できませんでした (コマンド: $Python)" @(
        'https://www.python.org/downloads/ から導入してください。'
    )
}
Write-Host "  $pyVersion"

# ---------------------------------------------------------------------------
# 2) Rust ツールチェーンのセットアップ (無ければ入れる)
# ---------------------------------------------------------------------------
Write-Step 'Rust ツールチェーンのセットアップ'

& $Python $SetupRust --yes
$rustCode = $LASTEXITCODE

if ($rustCode -eq 2) {
    # MSVC リンカが無い場合。cargo check は通るがインストーラは作れない。
    Fail 'Visual Studio Build Tools (MSVC リンカ) が必要です。' @(
        '上のメッセージに導入コマンドが表示されています。',
        '導入後、もう一度このスクリプトを実行してください。',
        '',
        '※ 型検査だけなら通ります: .\setup_and_build.ps1 -CheckOnly'
    )
}
if ($rustCode -ne 0) {
    Fail 'Rust のセットアップに失敗しました。' @(
        '上のメッセージを確認してください。',
        '手動導入: https://rustup.rs/'
    )
}

# ---------------------------------------------------------------------------
# 3) サイドカー → 型検査 → インストーラ (本体)
# ---------------------------------------------------------------------------
if ($CheckOnly) {
    Write-Step 'サイドカーのビルドと型検査'
} else {
    Write-Step 'サイドカーのビルド → 型検査 → インストーラ作成'
}

$buildArgs = @($BuildInstaller)
if ($CheckOnly)   { $buildArgs += '--check-only' }
if ($SkipSidecar) { $buildArgs += '--skip-sidecar' }

& $Python @buildArgs
$buildCode = $LASTEXITCODE

if ($buildCode -eq 0) {
    exit 0
}

# 失敗時は、よくある原因を日本語で案内する。
Fail 'ビルドに失敗しました。' @(
    '上のメッセージに、失敗した手順と対処方法が表示されています。',
    '',
    'よくある原因:',
    '  ・Visual Studio Build Tools (MSVC リンカ) が未導入',
    '    → winget install --id Microsoft.VisualStudio.2022.BuildTools --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools"',
    '  ・WebView2 が未導入 (通常は Windows 11 に同梱)',
    '  ・ネットワークが不安定で依存の取得に失敗した',
    '    → もう一度実行すると続きから進みます (取得済みは再利用されます)'
)
