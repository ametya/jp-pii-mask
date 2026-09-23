#!/bin/bash
# ===================================================================
#  JP PII Mask — アプリとインストーラを準備します。
#
#  このファイルをダブルクリックすると、自動で最後まで進みます。
#  コマンドを打つ必要はありません。
# ===================================================================

cd "$(dirname "$0")" || exit 1

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo
    echo "Python 3 が見つかりませんでした。"
    echo "先に Python をインストールしてから、もう一度このファイルを開いてください。"
    echo "  https://www.python.org/downloads/"
    echo
    read -r -p "Enter キーを押すと閉じます..."
    exit 1
fi

"$PY" desktop/scripts/build-installer.py "$@"
code=$?

echo
if [ "$code" -eq 0 ]; then
    echo "完了しました。上の『できあがったファイル』を確認してください。"
else
    echo "準備に失敗しました。上のメッセージを確認してください。"
fi
echo
read -r -p "Enter キーを押すと閉じます..."
exit "$code"
