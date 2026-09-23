# JP PII Mask — デスクトップシェル (Tauri 2)

日本語 PII マスキングプロキシ `jp_pii_mask` を、常駐メニューバー / システムトレイ常駐アプリとして
操作するための **Tauri 2 デスクトップシェル**です。ワンクリックで「マスキング保護 ON/OFF」を
切り替えられます。

> **⚠️ 未検証 (UNVERIFIED)**
> このスキャフォールドは **Rust ツールチェーンが無い環境で執筆されたもので、一度もコンパイル・実行・
> バンドルされていません**。したがって「ビルドが通る」「トレイアイコンが表示される」「サイドカーが
> 起動する」「管理 API と通信できる」ことは **一切確認されていません**。検証すべき項目と、あなたが
> 実行すべき検証コマンドは [`VERIFY.md`](./VERIFY.md) に列挙しています。

---

## 1. 前提条件

### 全プラットフォーム共通

- **Rust ツールチェーン**（`rustup` 経由）
  - https://rustup.rs/ から導入し、`rustc --version` / `cargo --version` が通ることを確認
  - 本スキャフォールドは **Rust 未導入環境で書かれた**ため、`rustup` の導入自体も未検証です
- **Node.js は不要**です。フロントエンドは `src/` の素の HTML/JS で、ビルドステップも CDN も
  使いません（オフラインで動作します）。
- Tauri CLI（どちらか）
  - `cargo install tauri-cli --version "^2"` → 以降 `cargo tauri ...`
  - もしくは `npm i -D @tauri-apps/cli@^2` → 以降 `npx tauri ...`

### Windows

- **Microsoft C++ Build Tools**（「C++ によるデスクトップ開発」ワークロード、
  MSVC v143 + Windows 10/11 SDK）
- **WebView2 Runtime**（Windows 11 は標準搭載。Windows 10 は Evergreen Runtime を導入）
- バンドル（インストーラ）を作る場合は WebView2 のブートストラッパ設定も検討してください

### macOS

- **Xcode Command Line Tools**: `xcode-select --install`
- 対応バージョン: **macOS 11.0 (Big Sur) 以上**
  （`src-tauri/tauri.conf.json` の `bundle.macOS.minimumSystemVersion` で指定）
- トレイ常駐アプリのため、`LSUIElement` 相当の設定は Rust 側で行っていません。
  必要なら `src-tauri/Info.plist` を追加し `LSUIElement=true` を検討してください（未実装）

---

## 2. サイドカー（Python 本体）の探索規則

このシェルは **Python 側をサイドカープロセスとして起動するだけ**で、マスキング処理そのものは
持ちません。起動コマンドは次の 1 形態です。

```
jp-pii-mask-server serve --port 8787
```

サイドカーの実体は `src-tauri/tauri.conf.json` の `bundle.externalBin` で宣言しています。

```json
"externalBin": ["binaries/jp-pii-mask-server"]
```

Tauri 2 の規則により、実際に配置するファイル名には **ターゲットトリプル**が付きます。

| プラットフォーム | 配置するファイル名 |
| --- | --- |
| Windows (x64) | `desktop/binaries/jp-pii-mask-server-x86_64-pc-windows-msvc.exe` |
| macOS (Apple Silicon) | `desktop/binaries/jp-pii-mask-server-aarch64-apple-darwin` |
| macOS (Intel) | `desktop/binaries/jp-pii-mask-server-x86_64-apple-darwin` |

自分のトリプルは次で確認できます。

```bash
rustc -vV | grep host
```

- **開発中 (`tauri dev`)**: 上記パスに実体を置けばそのまま起動されます。
  `binaries/` の実体は `.gitignore` 済みです（リポジトリには入れません）。
- **配布物**: バンドル時に Tauri がサイドカーを同梱します。
- サイドカーの生成は `scripts/build-sidecar.ps1`（Windows）/ `scripts/build-sidecar.sh`（macOS・Linux）
  を使います。詳細は [`binaries/README.md`](./binaries/README.md) を参照。
- **サイドカーが無い場合**: 起動は失敗し、設定ウィンドウの状態行に何も表示されません
  （サイドカーが無くてもプロキシのポート 8787 が既に誰かによって起動済みなら動作します）。
  既に `jp-pii-mask serve --port 8787` を自分で起動している運用も想定内です。

### ポート番号について

現時点では **8787 固定**です（`src-tauri/src/main.rs` の `DEFAULT_PORT`）。
設定ファイル（`%USERPROFILE%\.jp-pii-mask\config.json` /
`~/.jp-pii-mask/config.json`）側の `port` を変えた場合は、`DEFAULT_PORT` と
`src/main.js` の `ADMIN_API` を揃えて変更してください。
（`// NOTE:` コメントで印を付けています。設定ファイルからの読み込みは未実装。）

---

## 3. ビルド / 実行

```bash
cd desktop/src-tauri

# 依存の取得と型チェック（ここまでが最初の関門）
cargo check

# 開発実行（設定ウィンドウは起動時に非表示。トレイアイコンから開きます）
cargo tauri dev

# リリースビルド + インストーラ生成
cargo tauri build
```

`cargo tauri` が使えない場合は `npx tauri dev` / `npx tauri build` に読み替えてください。

成果物の目安:

- Windows: `desktop/src-tauri/target/release/bundle/msi/*.msi`, `bundle/nsis/*-setup.exe`
- macOS: `desktop/src-tauri/target/release/bundle/dmg/*.dmg`, `bundle/macos/JP PII Mask.app`

### アイコンについて

`src-tauri/icons/` の各ファイルは **プレースホルダ**（単色の仮アイコン）です。
`scripts/make-icons.py` で再生成できます。

```bash
python desktop/scripts/make-icons.py
```

本番用のアイコンを用意したら、標準コマンドで全形式を生成し直してください。

```bash
cargo tauri icon path/to/icon.png   # 1024x1024 の PNG を推奨
```

---

## 4. 仕組み（データフロー）

```
┌──────────────────────── Tauri アプリ ────────────────────────┐
│                                                              │
│  トレイメニュー                      設定ウィンドウ (380x520)  │
│  ├ マスキング保護 ON/OFF (チェック)   ┌──────────────────────┐ │
│  ├ 設定を開く                         │ 大きな ON/OFF トグル │ │
│  └ 終了                               │ 上流 API / APIキー   │ │
│         │                             │ ポート・稼働状況表示 │ │
│         │ emit("tray-request-toggle") │  ↑ 3秒ごとにポーリング│ │
│         └───────────────► (webview) ──┘  fetch /admin/api/state
│                              │                               │
└──────────────────────────────┼───────────────────────────────┘
                               │ HTTP (127.0.0.1:8787)
┌──────────────────────────────▼───────────────────────────────┐
│  jp-pii-mask-server (PyInstaller サイドカー)                  │
│  control panel / admin API / masking proxy                   │
└──────────────────────────────────────────────────────────────┘
```

Rust 側は **HTTP 通信を行いません**。`curl` や外部コマンドの呼び出しも一切していません。
設計方針として、管理 API へのアクセスは 100% **webview 内の `fetch()`** が担当し、
トレイ操作は **イベント** として webview に転送されます。

- トレイの「マスキング保護 ON/OFF」クリック
  → `app.emit("tray-request-toggle", <新しい真偽値>)`
  → webview が `POST /admin/api/state {"masking_enabled": ...}` を実行
  → webview が結果を `invoke("sync_tray_checked", {"checked": <実際の値>})` でトレイに書き戻す
- webview 側の大きなトグルも同じ経路を使います（`invoke` は状態同期のみ）
- 「終了」→ サイドカーを kill してから `app.exit(0)`

### 既知の注意点（未検証）

- **CORS**: webview のオリジン（Windows は `http://tauri.localhost`、macOS は `tauri://localhost`）は
  `http://127.0.0.1:8787` とは **クロスオリジン**です。Python 側が
  `Access-Control-Allow-Origin` を返さない場合、`fetch` は失敗します。
  Python 側の CORS 設定を確認してください（本シェルはヘッダを追加できません）。
- **CSP**: `tauri.conf.json` の `app.security.csp` で `connect-src` に
  `http://127.0.0.1:8787` を許可しています。ポートを変えるならここも変更が必要です。
- `withGlobalTauri: true` を使っているため、フロントエンドは `window.__TAURI__` 経由で
  IPC を呼びます（npm パッケージ不要）。CSP と併用した場合に `__TAURI__` が
  未定義になる可能性は未検証です（`VERIFY.md` 参照）。

---

## 5. ファイル構成

```
desktop/
├── README.md                       このファイル
├── VERIFY.md                       未検証項目と検証手順
├── .gitignore
├── binaries/
│   └── README.md                   サイドカーの配置場所と生成コマンド
├── scripts/
│   ├── build-sidecar.ps1           PyInstaller 実行 (Windows)
│   ├── build-sidecar.sh            PyInstaller 実行 (macOS/Linux)
│   └── make-icons.py               プレースホルダアイコン生成
└── src-tauri/
    ├── build.rs                    tauri-build
    ├── Cargo.toml
    ├── tauri.conf.json
    ├── capabilities/default.json
    ├── icons/                      プレースホルダ (tauri icon で置換推奨)
    └── src/main.rs                 トレイ + サイドカー起動 + コマンド
└── src/
    ├── index.html                  設定ウィンドウ (日本語 UI / インライン CSS)
    └── main.js                     状態ポーリング + トグル処理
```

---

## 6. トラブルシューティング（予想されるもの）

| 症状 | 確認すること |
| --- | --- |
| ビルドが失敗する | まず `VERIFY.md` の「検証手順」を上から順に実行。API 差異はここに集約されています |
| トレイアイコンが出ない | `Cargo.toml` の `tauri` に `features = ["tray-icon"]` があるか。`icons/icon.png` が存在するか |
| ウィンドウが出ない | 仕様です（`visible: false`）。トレイの「設定を開く」で表示します |
| 状態行が「接続できません」のまま | `jp-pii-mask serve --port 8787` が起動しているか、CORS ヘッダが返っているか |
| マスキングが効かない | Python 側の `masking_enabled` が `true` か（`GET /admin/api/state` で確認） |
| 終了してもプロセスが残る | 「終了」以外の方法（タスクマネージャ等）で殺した場合はサイドカーが残る可能性があります |

---

## 7. ライセンス

リポジトリ本体と同じ MIT を想定しています（本ディレクトリに個別ライセンスファイルはありません）。
