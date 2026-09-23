# binaries/ — PyInstaller サイドカーの配置場所

このディレクトリには **Python 本体を単一実行ファイル化したサイドカー**を置きます。
実体は容量が大きくプラットフォーム依存なので、リポジトリには含めません（`.gitignore` 済み）。

## 1. 生成コマンド (リポジトリルートで実行)

`scripts/build-sidecar.ps1`（Windows）/ `scripts/build-sidecar.sh`（macOS・Linux）が
以下のコマンドをラップしています。

### Windows (PowerShell)

```powershell
cd D:\個人情報マスキング
py -3.12 -m pip install --upgrade pip pyinstaller fastapi "uvicorn[standard]" pydantic cryptography httpx
py -3.12 -m PyInstaller `
  --noconfirm --clean --onefile `
  --name jp-pii-mask-server `
  --collect-all jp_pii_mask `
  jp_pii_mask\cli.py
```

### macOS / Linux

```bash
cd /path/to/個人情報マスキング
python3 -m pip install --upgrade pip pyinstaller fastapi 'uvicorn[standard]' pydantic cryptography httpx
python3 -m PyInstaller \
  --noconfirm --clean --onefile \
  --name jp-pii-mask-server \
  --collect-all jp_pii_mask \
  jp_pii_mask/cli.py
```

`--collect-all jp_pii_mask` は、パッケージ同梱の辞書・設定・`web/` などの
データファイルを取りこぼさないために付けています。

## 2. Tauri が期待するファイル名にリネームする

Tauri 2 の `bundle.externalBin` は **ターゲットトリプル付きのファイル名**を要求します。
PyInstaller が `dist/jp-pii-mask-server.exe` を吐いたら、次のようにコピーしてください。

自分のターゲットトリプルは `rustc -vV` の `host:` 行で確認できます。

```bash
rustc -vV | grep host
```

### Windows x64

```powershell
copy dist\jp-pii-mask-server.exe binaries\jp-pii-mask-server-x86_64-pc-windows-msvc.exe
```

### macOS Apple Silicon

```bash
cp dist/jp-pii-mask-server binaries/jp-pii-mask-server-aarch64-apple-darwin
chmod +x binaries/jp-pii-mask-server-aarch64-apple-darwin
```

### macOS Intel

```bash
cp dist/jp-pii-mask-server binaries/jp-pii-mask-server-x86_64-apple-darwin
chmod +x binaries/jp-pii-mask-server-x86_64-apple-darwin
```

### Linux x64 (参考)

```bash
cp dist/jp-pii-mask-server binaries/jp-pii-mask-server-x86_64-unknown-linux-gnu
chmod +x binaries/jp-pii-mask-server-x86_64-unknown-linux-gnu
```

## 3. 動作確認

サイドカー単体で起動できるか先に確認すると、切り分けが楽です。

```bash
./binaries/jp-pii-mask-server-x86_64-apple-darwin serve --port 8787
# 別ターミナルで
curl -s http://127.0.0.1:8787/healthz -o /dev/null -w '%{http_code}\n'
curl -s http://127.0.0.1:8787/admin/api/state
```

`healthz` が 200 を返し、`state` が JSON を返せばサイドカーは正常です。
その状態で `cargo tauri dev` を実行すると、Tauri 側は既に起動済みのポートに接続します
（Tauri 側からの起動は「Address already in use」で失敗しますが、アプリは常駐を続けます）。

## 4. 注意事項

- **プラットフォームごとに別々にビルドが必要です。** PyInstaller はクロスコンパイルしません。
- **署名なしの実行ファイルは Gatekeeper / SmartScreen に止められます。**
  配布するならコード署名を検討してください。
- `--onefile` は起動時に一時ディレクトリへ展開するため、初回起動が数秒遅れます。
  起動速度を優先するなら `--onedir` に切り替え、その出力ディレクトリ全体を
  サイドカーとして扱う構成に変更してください（その場合 `externalBin` は使えません）。

> **未検証**: 上記のコマンドは一度も実行されていません。Python 3.12 環境での
> PyInstaller ビルドは、`spacy` / `ja-ginza` などの任意依存を入れた構成で
> しばしばフックが必要になります。詳細は `../VERIFY.md` を参照してください。
