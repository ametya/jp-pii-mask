# binaries/ — アプリに内蔵するサーバーの置き場

このディレクトリには、アプリに同梱する **サーバー本体（単一実行ファイル）** が入ります。
容量が大きく、OS ごとに別物なので、リポジトリには含めません（`.gitignore` 済み）。

## 自分で置く必要はありません

**このディレクトリの中身は自動で作られます。**

リポジトリにある「インストーラを作る.bat」（Windows）または
「インストーラを作る.command」（macOS）を実行すると、
`desktop/scripts/build-sidecar.py` が次のことを自動で行います。

1. サーバーを単一実行ファイルにまとめる（PyInstaller）
2. このディレクトリに、Tauri が探す名前で配置する

配置される名前（`<triple>` はお使いの環境によって変わります）:

- Windows: `jp-pii-mask-server-x86_64-pc-windows-msvc.exe`
- macOS (Apple Silicon): `jp-pii-mask-server-aarch64-apple-darwin`
- macOS (Intel): `jp-pii-mask-server-x86_64-apple-darwin`

> **なぜ `desktop/binaries/` ではないのか**
> Tauri の設定 `bundle.externalBin` は **`src-tauri/` からの相対パス**として
> 解決されます。そのため配置先は `desktop/src-tauri/binaries/` でなければなりません。
> 以前はここを間違えており、インストーラの作成が必ず失敗する状態でした。

## 手で確認したい場合

サーバー単体で起動できるか確かめると、切り分けが楽です。

```bash
# 例: macOS (Apple Silicon)
./desktop/src-tauri/binaries/jp-pii-mask-server-aarch64-apple-darwin serve --port 8787
# 別のターミナルで
curl -s http://127.0.0.1:8787/healthz
```

`{"status":"ok",...}` が返れば正常です。停止するときは、この実行ファイルだけでなく
**プロセスツリーごと**止めてください（単一実行ファイルは内部で別プロセスを起動するため、
親だけ止めるとポートを掴んだまま残ることがあります）。
