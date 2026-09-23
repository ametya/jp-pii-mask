"""PyInstaller 用のエントリポイント。

`jp_pii_mask/cli.py` を直接指定すると相対 import が解決できないため、
パッケージとして import する薄い入口を用意する。

PyInstaller の呼び出し例:
    python -m PyInstaller --onefile --name jp-pii-mask-server \
        --paths . --hidden-import uvicorn.logging \
        --hidden-import uvicorn.loops.auto \
        --hidden-import uvicorn.protocols.http.auto \
        --hidden-import uvicorn.protocols.websockets.auto \
        --hidden-import uvicorn.lifespan.on \
        scripts/sidecar_entry.py
"""

from __future__ import annotations

import multiprocessing
import sys

from jp_pii_mask.cli import main

if __name__ == "__main__":
    # 単一ファイル化した実行ファイルでは、子プロセス起動時に
    # 再帰的な起動を防ぐための宣言が必要。
    multiprocessing.freeze_support()
    sys.exit(main())
