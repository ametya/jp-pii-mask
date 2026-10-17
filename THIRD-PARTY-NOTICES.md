# THIRD-PARTY NOTICES

本プロジェクト (jp-pii-mask) は以下のオープンソースソフトウェアを利用しています。
各ソフトウェアの著作権は、それぞれの著作者に帰属します。

本プロジェクト自体のライセンスは [MIT License](LICENSE) です。

---

## 実行時依存

### FastAPI
- License: MIT
- Source: https://github.com/fastapi/fastapi
- Copyright (c) 2018 Sebastián Ramírez

### Starlette
- License: BSD-3-Clause
- Source: https://github.com/encode/starlette
- Copyright (c) 2018, Encode OSS Ltd.

### Uvicorn
- License: BSD-3-Clause
- Source: https://github.com/encode/uvicorn
- Copyright (c) 2017, Encode OSS Ltd.

### pydantic
- License: MIT
- Source: https://github.com/pydantic/pydantic
- Copyright (c) 2017 to present, Pydantic Services Inc. and individual contributors

### cryptography
- License: Apache-2.0 OR BSD-3-Clause
- Source: https://github.com/pyca/cryptography
- Copyright (c) Individual contributors

### HTTPX
- License: BSD-3-Clause
- Source: https://github.com/encode/httpx
- Copyright (c) 2019, Encode OSS Ltd.

### uvicorn[standard] が追加する依存
- httptools — MIT — https://github.com/MagicStack/httptools
- uvloop — MIT / Apache-2.0 — https://github.com/MagicStack/uvloop
- watchfiles — MIT — https://github.com/samuelcolvin/watchfiles
- websockets — BSD-3-Clause — https://github.com/python-websockets/websockets

---

## 任意依存（精度向上）

### spaCy
- License: MIT
- Source: https://github.com/explosion/spaCy
- Copyright (c) 2016 ExplosionAI GmbH

### GiNZA
- License: MIT
- Source: https://github.com/megagonlabs/ginza
- Copyright (c) 2019 Megagon Labs, Inc.

> **注意:** GiNZA が利用する学習済みモデルには、別途ライセンス条件が
> 設定されている場合があります。再配布する場合は各モデルのライセンスを
> 個別に確認してください。

### SudachiPy / SudachiDict
- License: Apache-2.0
- Source: https://github.com/WorksApplications/SudachiPy
- Copyright (c) 2018 Works Applications Co., Ltd.

---

## 任意依存（PDF・画像ファイルのマスク）

### Pillow
- License: MIT-CMU (HPND)
- Source: https://github.com/python-pillow/Pillow
- Copyright (c) 2010 by Jeffrey A. Clark and contributors

### pypdfium2
- License: BSD-3-Clause / Apache-2.0（同梱の PDFium 本体は BSD-3-Clause）
- Source: https://github.com/pypdfium2-team/pypdfium2
- Copyright (c) pypdfium2 team, PDFium authors

### winsdk（Windows 標準 OCR の Python バインディング）
- License: MIT
- Source: https://github.com/pywinrt/pywinrt
- Copyright (c) PyWinRT contributors

> `winsdk` は OS 内蔵の Windows.Media.Ocr を呼び出すだけで、
> 認識処理を外部サービスへ送信しません。

---

## デスクトップ GUI（`desktop/`）

### Tauri
- License: MIT OR Apache-2.0
- Source: https://github.com/tauri-apps/tauri
- Copyright (c) 2019 - present, Tauri Programme within The Commons Conservancy

### tauri-plugin-shell
- License: MIT OR Apache-2.0
- Source: https://github.com/tauri-apps/plugins-workspace

### tauri-plugin-autostart
- License: MIT OR Apache-2.0
- Source: https://github.com/tauri-apps/plugins-workspace

---

## 開発時のみ

### pytest
- License: MIT — https://github.com/pytest-dev/pytest

### Ruff
- License: MIT — https://github.com/astral-sh/ruff

### PyInstaller
- License: GPL-2.0-or-later **WITH Bootloader Exception**
- Source: https://github.com/pyinstaller/pyinstaller
- Copyright (c) 2010-2024, PyInstaller Development Team

> **PyInstaller のライセンスに関する重要事項:**
> PyInstaller 本体は GPL-2.0 ですが、**Bootloader Exception** により、
> PyInstaller を用いて生成した実行ファイルには GPL の条件は及びません。
> したがって、本プロジェクトを PyInstaller で単一実行ファイル化して
> 商用配布することは可能です。
>
> ただし、PyInstaller 自体を改変して配布する場合は、
> 改変物に GPL-2.0 が適用されます。

---

## 参考にしたが、コードを含めていないもの

以下は設計の参考にしたのみで、いかなるコードも含んでいません。

- **Microsoft Presidio** (MIT) — 検出と置換の設計思想を参考
- **LiteLLM** (MIT) — プロバイダ抽象化の設計を参考

以下は**検討の結果、採用を見送りました**（理由は
[`docs/LICENSING.md`](docs/LICENSING.md) を参照）。

- **Jan** — AGPL-3.0（コピーレフトのため商用クローズド製品に不適）
- **Lobe Chat** — Apache-2.0 だが追加条項の確認が必要
- **Electron** — ライセンスは問題なし。常駐アプリとしてのメモリ消費が大きいため Tauri を採用

---

配布物に本ファイルと各ライセンス全文を含めてください。
ライセンス全文は各プロジェクトのリポジトリから取得できます。
