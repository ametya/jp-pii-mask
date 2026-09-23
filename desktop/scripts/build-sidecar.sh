#!/usr/bin/env bash
#
# jp_pii_mask を PyInstaller で単一実行ファイル化し、Tauri が期待する名前で
# desktop/binaries/ に配置する。(macOS / Linux 用)
#
# このスクリプトはリポジトリの他のディレクトリを変更しません。
# PyInstaller の出力 (dist / build / spec) はすべて desktop/ 配下に固定し、
# リポジトリルートは読み取り専用の入力 (--paths) としてのみ参照します。
# __pycache__ を作らせないため PYTHONDONTWRITEBYTECODE=1 を設定します。
#
# ⚠️ 未検証: このスクリプトは一度も実行されていません。VERIFY.md を参照。
#
# 使い方:
#   bash desktop/scripts/build-sidecar.sh
#   bash desktop/scripts/build-sidecar.sh --skip-deps
#   PYTHON=python3.12 bash desktop/scripts/build-sidecar.sh

set -euo pipefail

SKIP_DEPS=0
for arg in "$@"; do
  case "$arg" in
    --skip-deps) SKIP_DEPS=1 ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "不明な引数: $arg" >&2
      exit 2
      ;;
  esac
done

# ---------------------------------------------------------------------------
# パス解決: このファイルは desktop/scripts/ にある
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_DIR/.." && pwd)"

BIN_DIR="$DESKTOP_DIR/binaries"
DIST_DIR="$DESKTOP_DIR/dist"
BUILD_DIR="$DESKTOP_DIR/build"
ENTRY_PY="$REPO_ROOT/scripts/sidecar_entry.py"

PYTHON="${PYTHON:-python3}"

echo "リポジトリルート : $REPO_ROOT"
echo "desktop         : $DESKTOP_DIR"
echo "python          : $PYTHON ($($PYTHON --version 2>&1))"

if [ ! -f "$ENTRY_PY" ]; then
  echo "エラー: エントリポイントが見つかりません: $ENTRY_PY" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# リポジトリ側を汚さないための設定
# ---------------------------------------------------------------------------
export PYTHONDONTWRITEBYTECODE=1

# ---------------------------------------------------------------------------
# 1) 依存のインストール
# ---------------------------------------------------------------------------
if [ "$SKIP_DEPS" -eq 0 ]; then
  echo "依存パッケージをインストールします..."
  "$PYTHON" -m pip install --upgrade pip pyinstaller fastapi 'uvicorn[standard]' pydantic cryptography httpx
else
  echo "依存のインストールをスキップします (--skip-deps)"
fi

# ---------------------------------------------------------------------------
# 2) PyInstaller 実行 (出力は desktop/ 配下のみ)
# ---------------------------------------------------------------------------
echo "PyInstaller を実行します..."
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name jp-pii-mask-server \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR" \
  --specpath "$BUILD_DIR" \
  --paths "$REPO_ROOT" \
  --collect-all jp_pii_mask \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  --console \
  "$ENTRY_PY"

BUILT="$DIST_DIR/jp-pii-mask-server"
if [ ! -f "$BUILT" ]; then
  echo "エラー: 生成物が見つかりません: $BUILT" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 3) Tauri が期待するターゲットトリプル名でコピー
# ---------------------------------------------------------------------------
TRIPLE=""
if command -v rustc >/dev/null 2>&1; then
  TRIPLE="$(rustc -vV | sed -n 's/^host: //p' | head -n1)"
fi

if [ -z "$TRIPLE" ]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)  TRIPLE="aarch64-apple-darwin" ;;
    Darwin-x86_64) TRIPLE="x86_64-apple-darwin" ;;
    Linux-x86_64)  TRIPLE="x86_64-unknown-linux-gnu" ;;
    *)
      echo "エラー: ターゲットトリプルを判定できません。rustup を導入するか、手動でリネームしてください。" >&2
      exit 1
      ;;
  esac
  echo "警告: rustc が見つからないため、uname から $TRIPLE と推定しました。" >&2
fi

mkdir -p "$BIN_DIR"
TARGET="$BIN_DIR/jp-pii-mask-server-$TRIPLE"
cp -f "$BUILT" "$TARGET"
chmod +x "$TARGET"

echo
echo "完了しました。"
echo "  生成物 : $BUILT"
echo "  配置先 : $TARGET"
echo
echo "次の手順:"
echo "  (cd \"$DESKTOP_DIR/src-tauri\" && cargo tauri dev)"
