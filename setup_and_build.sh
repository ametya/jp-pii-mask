#!/usr/bin/env bash
#
# setup_and_build.sh — これ 1 つで、Rust のセットアップから Tauri GUI のビルド、
# Python サーバーのサイドカー同梱、インストーラ (.dmg) の作成まで行います。(macOS / Linux)
#
# エンジニアでない方でも実行できます。途中で失敗した場合は、
# どこで何をすればよいかを日本語で表示します。
#
#   1. Rust ツールチェーンの確認 (無ければ自動導入)
#   2. Python の確認
#   3. Python サイドカーのビルド (PyInstaller で単一実行ファイル化)
#   4. Tauri プロジェクトの型検査 (cargo check) ← 最初の関門
#   5. Tauri CLI の確認 (無ければ自動導入)
#   6. GUI のビルド + インストーラ (.dmg / .app) の作成
#   7. できあがったファイルの場所を表示
#
# 使い方:
#   bash setup_and_build.sh
#   bash setup_and_build.sh --check-only    # 型検査までで止める
#   bash setup_and_build.sh --skip-sidecar  # サイドカーを作り直さない
#   PYTHON=python3.12 bash setup_and_build.sh
#
# 実処理の本体は desktop/scripts/ の Python スクリプトです。
# そちらは Windows / macOS / Linux で同じように動きます。

set -euo pipefail

CHECK_ONLY=0
SKIP_SIDECAR=0

for arg in "$@"; do
  case "$arg" in
    --check-only)   CHECK_ONLY=1 ;;
    --skip-sidecar) SKIP_SIDECAR=1 ;;
    -h|--help)
      sed -n '2,26p' "$0"
      exit 0
      ;;
    *)
      echo "不明な引数: $arg" >&2
      echo "使い方は --help で確認できます。" >&2
      exit 2
      ;;
  esac
done

# ---------------------------------------------------------------------------
# パス解決: このファイルはリポジトリルートにある
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_RUST="$SCRIPT_DIR/desktop/scripts/setup-rust.py"
BUILD_INSTALLER="$SCRIPT_DIR/desktop/scripts/build-installer.py"

PYTHON="${PYTHON:-python3}"

step() {
  echo
  echo "=============================================================="
  echo "$1"
  echo "=============================================================="
}

fail() {
  local message="$1"
  shift
  echo
  echo "エラー: $message" >&2
  if [ "$#" -gt 0 ]; then
    echo >&2
    for hint in "$@"; do
      echo "  $hint" >&2
    done
  fi
  echo >&2
  exit 1
}

for f in "$SETUP_RUST" "$BUILD_INSTALLER"; do
  [ -f "$f" ] || fail "必要なスクリプトが見つかりません: $f" \
    "リポジトリを正しく取得できているか確認してください。"
done

# ---------------------------------------------------------------------------
# 1) Python の確認
# ---------------------------------------------------------------------------
step "Python の確認"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  fail "Python が見つかりません (コマンド: $PYTHON)" \
    "https://www.python.org/downloads/ から導入してください。" \
    "別の名前で入っている場合は PYTHON=python3.12 のように指定できます。"
fi
echo "  $("$PYTHON" --version 2>&1)"

# ---------------------------------------------------------------------------
# 2) Rust ツールチェーンのセットアップ (無ければ入れる)
# ---------------------------------------------------------------------------
step "Rust ツールチェーンのセットアップ"

# setup-rust.py は PATH に cargo を足さない方針なので、
# ここで子プロセスとこのスクリプトの両方に通す。
if [ -d "$HOME/.cargo/bin" ]; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi

set +e
"$PYTHON" "$SETUP_RUST" --yes
RUST_CODE=$?
set -e

if [ "$RUST_CODE" -ne 0 ]; then
  fail "Rust のセットアップに失敗しました。" \
    "上のメッセージを確認してください。" \
    "手動導入: https://rustup.rs/"
fi

# setup-rust.py が入れた cargo をこのシェルでも使えるようにする。
if [ -d "$HOME/.cargo/bin" ]; then
  export PATH="$HOME/.cargo/bin:$PATH"
fi

if ! command -v cargo >/dev/null 2>&1; then
  fail "cargo が PATH に見つかりません。" \
    "新しいターミナルを開いてから、もう一度実行してみてください。"
fi

# ---------------------------------------------------------------------------
# 3) サイドカー → 型検査 → インストーラ (本体)
# ---------------------------------------------------------------------------
if [ "$CHECK_ONLY" -eq 1 ]; then
  step "サイドカーのビルドと型検査"
else
  step "サイドカーのビルド → 型検査 → インストーラ作成"
fi

build_args=()
[ "$CHECK_ONLY" -eq 1 ]   && build_args+=("--check-only")
[ "$SKIP_SIDECAR" -eq 1 ] && build_args+=("--skip-sidecar")

set +e
"$PYTHON" "$BUILD_INSTALLER" "${build_args[@]:-}"
BUILD_CODE=$?
set -e

if [ "$BUILD_CODE" -eq 0 ]; then
  exit 0
fi

fail "ビルドに失敗しました。" \
  "上のメッセージに、失敗した手順と対処方法が表示されています。" \
  "" \
  "よくある原因:" \
  "  ・macOS: Xcode Command Line Tools が未導入" \
  "    → xcode-select --install" \
  "  ・Linux: システムライブラリ不足 (WebKitGTK など)" \
  "    → sudo apt-get install -y libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf" \
  "  ・ネットワークが不安定で依存の取得に失敗した" \
  "    → もう一度実行すると続きから進みます (取得済みは再利用されます)"
