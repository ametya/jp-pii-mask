# VERIFY.md — 未検証項目と検証手順

このドキュメントは「**何が検証済みで、何が未検証か**」を正直に列挙するものです。

---

## 0. 検証済みの事実

### 0.1 Rust ツールチェーン導入後に行った実検証（最新・最重要）

Rust ツールチェーンを導入し、**Tauri 側を実際にビルドしました**。
以前の「未検証」の記述のうち、以下は解消されています。

| 項目 | 結果 | 方法 |
| --- | --- | --- |
| `cargo check` が通ること | **PASS** | Rust 1.98.1 / tauri 2.11.6 で実行。エラー 0 |
| `cargo tauri build` が成功すること | **PASS** | リリースビルド完了（3分16秒） |
| アプリ本体が生成されること | **PASS** | `jp-pii-mask-desktop.exe` (4.34 MB) |
| インストーラ (`.msi`) が生成されること | **PASS** | `JP PII Mask_0.1.0_x64_en-US.msi` (36.5 MB) |
| インストーラ (`setup.exe`) が生成されること | **PASS** | `JP PII Mask_0.1.0_x64-setup.exe` (35.9 MB) |
| サイドカーが同梱されること (`externalBin`) | **PASS** | .msi を `msiexec /a` で実際に展開し、`jp-pii-mask-server.exe` (34.79 MB) が `jp-pii-mask-desktop.exe` と並んで入っていることを確認 |
| 同梱サイドカーが起動して応答すること | **PASS** | 展開後の .exe を実際に起動。`/healthz` が `200 {"status":"ok"}`、`/admin/api/state` が JSON を返す |
| 同梱サイドカーを孤児なく停止できること | **PASS** | `taskkill /PID <pid> /T /F` で停止。停止後にポート解放と孤児なしを確認 |
| セットアップスクリプトが最後まで通ること | **PASS** | `setup_and_build.ps1` を実実行し、インストーラ生成まで exit 0 |

**この節の結果は推測ではありません。実際に実行したコマンドの出力です。**

### 0.2 執筆時に不確実だった API のうち、実際に誤っていたもの

`cargo check` が以下の 2 箇所を実際に検出しました（コードは修正済みです）。

| 旧コード | 実際の API | 対応 |
| --- | --- | --- |
| `tray.set_checked(bool)` | `TrayIcon` に `set_checked` は **存在しない** | `CheckMenuItem::set_checked` に変更 |
| `tray.menu()` | `TrayIcon` に `menu()` は **存在しない**（`set_menu` のみ） | `CheckMenuItem` のハンドルを `SidecarState` に保持する方式に変更 |

> この 2 件は、当初 `// NOTE:` で「無ければ代替案 X に差し替えること」と
> 書いていたとおりの修正で解消しました。
> **推測で書いた API は実際に間違っていた**という事実は、
> 実際にコンパイルして初めて判明したものです。

### 0.3 テキスト成果物に対する機械的検証

| 項目 | 結果 | 方法 |
| --- | --- | --- |
| `src-tauri/tauri.conf.json` が JSON として妥当 | **PASS** | `json.load` |
| `src-tauri/capabilities/default.json` が JSON として妥当 | **PASS** | `json.load` |
| `.github/workflows/build-installers.yml` が YAML として妥当 | **PASS** | `yaml.safe_load` |
| `src/main.js` が JavaScript として構文エラー無し | **PASS** | `node --check` |
| ビルドスクリプト 3 本が Python として構文エラー無し | **PASS** | `ast.parse` |
| PowerShell スクリプトが BOM 付き UTF-8 | **PASS** | バイト先頭 3 バイト + UTF-8 復号 + 日本語文字の存在確認 |
| アイコン 6 ファイルの形式 | **PASS(簡易)** | マジックバイト確認 (PNG=`89504e47` / ICO=`00000100` / ICNS=`icns`) |
| 作業ディレクトリの逸脱が無いこと | **PASS** | `desktop/` 配下に `dist/` `build/` を隔離 |

## 0.4 親エージェントによる追加検証（この節が上記を上書きします）

Python 側の実装・検証を担当した親エージェント（あなた）が、
この雛形に対して以下の修正と検証を行いました。
**この節の内容は「0. 検証済みの事実」より新しく、一部の未検証項目を解消しています。**

### 解消された未検証項目

| 旧 1.1 の項目 | 新しい状況 | 根拠 |
| --- | --- | --- |
| PyInstaller でサイドカーの単一実行ファイルが生成できること | **検証済み（PASS）** | `desktop/src-tauri/binaries/jp-pii-mask-server-x86_64-pc-windows-msvc.exe` を実際に生成（34.79 MB） |
| サイドカーが起動して `/healthz` に応答すること | **検証済み（PASS）** | 生成した .exe を実プロセスで起動し、実 HTTP で 22/22 PASS |
| サイドカー探索規則・起動引数の整合 | **検証済み（PASS）** | `serve --host --port --upstream` が想定どおり動作 |
| `src/main.js` の構文 | **検証済み（PASS）** | `node --check` が通過 |

検証コマンド:

```bash
python scripts/e2e_live.py --exe desktop/src-tauri/binaries/jp-pii-mask-server-x86_64-pc-windows-msvc.exe
# => 22/22 PASS
```

### 雛形に対して行った修正（重要）

0. **サイドカーの配置先を修正（致命的なバグ）**
   - 旧: `desktop/binaries/` に配置していた
   - 新: `desktop/src-tauri/binaries/`
   - **理由:** Tauri の `bundle.externalBin: ["binaries/jp-pii-mask-server"]` は
     **`src-tauri/` からの相対パス**として解決されます。
     旧配置では `cargo tauri build` が必ず失敗する状態でした。
   - 旧スクリプト (`build-sidecar.ps1` / `.sh`) は削除し、
     Windows / macOS / Linux 共通の `build-sidecar.py` に統合しました。

1. **`scripts/build-sidecar.ps1` / `.sh` のエントリポイントを修正**
   - 旧: `jp_pii_mask/cli.py` を直接指定
   - 新: `scripts/sidecar_entry.py`
   - **理由:** PyInstaller はエントリを `__main__` として扱うため、
     `from .config import ...` のような相対 import が解決できず失敗します。
   - あわせて uvicorn の `--hidden-import` を 5 件追加しました。
   - 現在はこの 2 本を廃止し、共通の `build-sidecar.py` に統合しています。

2. **`build-sidecar.ps1` を UTF-8 (BOM 付き) で保存し直し**
   - **理由:** Windows PowerShell 5.1 は BOM の無い UTF-8 を CP932 として読みます。
     日本語コメントのバイト列が引用符として解釈され、構文エラーになり得ます。
   - 現在は `Parser::ParseFile` で構文検証済み。

3. **`src/main.js` の API 契約を実際の Python 実装に整合**
   - 統計キーを `entities_masked` / `placeholders_restored` に修正
     （旧実装は存在しないキーを探していました）
   - `POST /admin/api/state` が GET と同じ形を返すことを確認し、
     未確認コメントを削除
   - API キー消去（空文字 POST）が機能することを確認

4. **CORS を Python 側に実装（`proxy.py`）**
   - `Origin` が `localhost` / `127.0.0.1` / `tauri.localhost` のときだけ許可し、
     CORS ヘッダを返します。
   - それ以外の `Origin` 付き要求は **403** で拒否します。
     `/admin/api/mapping` は原文を返すため、任意のサイトから
     読めては困るためです。
   - `Origin` を送らない CLI / SDK は従来どおり通ります。

5. **cp932 でのクラッシュを修正（Python 側）**
   - サイドカーとして起動すると標準出力がパイプになり、
     ログの絵文字を CP932 が表現できず `UnicodeEncodeError` で
     プロセスごと終了していました。`cli._make_streams_robust()` で
     `errors="replace"` を設定して防止しています。
   - **この不具合は .exe を実際に起動して初めて発覚しました。**
     in-process のテストでは再現しない種類のものです。

### 引き続き未検証（Rust 環境が必要）

以下は**一切変わっていません**。Rust ツールチェーンのある環境で
`VERIFY.md` §2 の A→F を順に実行してください。

- `cargo check` / `cargo tauri dev` / `cargo tauri build`
- トレイ常駐、メニュー項目、`sync_tray_checked`、サイドカー kill
- `src-tauri/src/main.rs` の `// NOTE:` 12 箇所（§1.2）
- `window.__TAURI__` の名前空間（`core.invoke` / `event.listen`）
- `tauri.conf.json` の Tauri 2 スキーマ適合
- アイコンの見た目、macOS Gatekeeper / Windows SmartScreen

### ⚠️ サイドカー停止は「プロセスツリーごと」必要（実測で判明）

**PyInstaller の `--onefile` 実行ファイルは、ブートローダ（親）が
実際のアプリ（子）を spawn します。**

したがって `CommandChild::kill()` が親だけを終わらせた場合、
**子プロセスが孤児として残り、ポート 8787 を掴んだまま**になります。

これは実際に発生しました。検証スクリプトが `terminate()` で親だけを
止めたところ、`jp-pii-mask-server` が生存し続け、次のビルドが
`PermissionError`（exe がロックされている）で失敗しました。

**Rust 側（`main.rs` の `SidecarState::kill_child`）で必要な対応:**

1. **まず正常シャットダウンを要求する** — 子には `SIGTERM` 相当を送る。
   `CommandChild::kill()` は即時終了（Windows では TerminateProcess）なので、
   uvicorn がグレースフルに停止する余地がありません。
2. **ツリーごと確実に終わらせる** — 親だけを kill しても子が残ります。
   Windows なら `taskkill /PID <pid> /T /F` 相当（`/T` = ツリー）、
   Unix ならプロセスグループへのシグナル送信が必要です。
3. **ポート解放を確認する** — 停止後に 8787 が bind 可能か確かめる。
   掴んだままだと次回起動が失敗します。

検証スクリプト側は `terminate_tree()` で対応済みです
（Windows は `taskkill /T /F`、停止後にポート解放まで確認）。
この対応により `python scripts/e2e_live.py --exe …` は
**22/22 PASS** となり、実行後に孤児プロセスが残らないことを確認しました。

> この不具合は in-process のテストでは絶対に再現しません。
> 「実際にプロセスを起動して、終了後に確認する」ことで初めて見つかりました。

---

## 1. 未検証項目

### 1.1 根本的な制約 → 大部分が解消しました

以下はすべて **検証済み（PASS）** になりました（§0.1 参照）。

- [x] **`cargo check` が通ること** — Rust 1.98.1 で実行、エラー 0
- [x] **`cargo tauri build` が成功すること** — インストーラ生成まで確認
- [x] **サイドカーが実際に起動して `/healthz` に応答すること**
- [x] **PyInstaller でサイドカーの単一実行ファイルが生成できること**
- [x] **サイドカーがインストーラに同梱されること**

残っている未検証は **GUI を実際に操作しての確認** だけです。

- [ ] **アプリが実際に起動してトレイ常駐すること**（ビルドは成功したが、
      トレイアイコンが見えるか、メニューが押せるかは画面での目視確認が必要）
- [ ] **トレイの ON/OFF が実際にマスキングを切り替えること**
- [ ] **設定ウィンドウが意図どおりに描画されること**

### 1.2 Tauri 2 API の不確実性 → 実測で 2 件が誤りと判明

`cargo check` が検出した誤りは **修正済み**です（§0.2 参照）。
残りの `// NOTE:` は「コンパイルは通ったが、実行時の挙動は未確認」という
意味に変わりました。

**実測で確定したもの:**

| 箇所 | 結果 |
| --- | --- |
| `CommandChild::kill(self)` | by-value で正しい（コンパイル通過） |
| `CommandChild::pid() -> u32` | **実在する**。ツリー停止に使用（`taskkill /T`） |
| `tauri_plugin_autostart::init(MacosLauncher, Option<Vec<&str>>)` | この形で正しい（コンパイル通過） |
| `MenuBuilder::items(&[&dyn IsMenuItem<R>])` | この形で正しい（コンパイル通過） |
| `CommandEvent::{Stdout, Stderr, Terminated}` | この形で正しい（コンパイル通過） |
| `TrayIconBuilder::with_id(...).on_menu_event(...)` | この形で正しい（コンパイル通過） |

**依然として未確定（実行時のみ判明するもの）:**

| # | 箇所 | 不確実な内容 | どう確かめるか |
| --- | --- | --- | --- |
| 1 | `sync_tray_checked` | トレイのチェック表示が実際に変わるか | アプリを起動し、トグルを押してトレイのチェックを見る |
| 2 | `CheckMenuItem` 自動トグル | クリック時に Tauri が checked を反転させるか | 同上。反転しなければ `is_checked()` を自前で反転させる |
| 3 | `TrayIconEvent::DoubleClick` | ダブルクリックでウィンドウが開くか | トレイアイコンをダブルクリックする |
| 4 | `capabilities/default.json` の権限識別子 | 実行時に権限エラーが出ないか | 起動ログを見る。エラー時はメッセージが正しい名称を教える |
| 5 | `tauri.conf.json` の NSIS 日本語化 | インストーラが日本語で表示されるか | `.msi` / `setup.exe` を実行して見る |
| 6 | `skipTaskbar: true` + `visible: false` | 意図どおりかどうか | 起動してタスクバーを見る |

> **重要:** 以前のこの節には「12 個の NOTE がすべて未検証」と書いてありましたが、
> 実際にコンパイルした結果、**推測で書いた API 2 箇所が間違っており、
> 他は正しくコンパイルできました**。コンパイル検証は推測を実測に変えます。

### 1.3 フロントエンドの不確実性（`src/main.js`）

- [ ] `window.__TAURI__` が `withGlobalTauri: true` で実際に生えるか
      （CSP と併用時に未定義にならないか）
- [ ] `window.__TAURI__.core.invoke` / `window.__TAURI__.event.listen` の名前空間が正しいか
      （Tauri 2 で `window.__TAURI__.tauri.invoke` から変わった可能性）
- [ ] Rust コマンド `sync_tray_checked` が webview から `syncTrayChecked`
      （camelCase）で呼べるか、snake_case のままか
- [x] **CORS**: `http://tauri.localhost`（Windows）/ `tauri://localhost`（macOS）から
      `http://127.0.0.1:8787` への `fetch` が成功するか。
      → **Python 側に CORS を実装済み**（`proxy.py`）。`localhost` / `127.0.0.1` /
      `tauri.localhost` のときだけ許可し、それ以外の `Origin` 付き要求は 403 で拒否します。
      実測: `Origin: http://tauri.localhost` で `Access-Control-Allow-Origin` が返ることを確認済み。
- [ ] `connect-src` の CSP 指定が実際に `fetch` を許可するか
- [x] `/admin/api/state` の `stats` のキー名
      → **Python 側の実装を確認し、`entities_masked` / `placeholders_restored`
      に修正済み**（旧実装は存在しないキーを探していました）。
- [x] `POST /admin/api/state` が更新後の state を返すか
      → **返すことを確認済み**。GET と同じ形を返します。
- [x] `api_key: ""` が「キー消去」として機能するか
      → **機能することを確認済み**。

### 1.4 サイドカー周り

- [x] `PyInstaller` のコマンド（`--collect-all jp_pii_mask` 等）が実際に成功するか。
      → **成功を確認済み**（34.79 MB の単一 exe を生成）。
      `spacy` / `ja-ginza` を入れた構成での追加フックは**未検証**です
      （現在は NER 無しの劣化モードでビルドしています）。
- [x] サイドカーの実際のバイナリ名（`jp-pii-mask-server`）
      → **確認済み**。`externalBin: ["binaries/jp-pii-mask-server"]` に対し、
      `jp-pii-mask-server-x86_64-pc-windows-msvc.exe` が正しく解決されました。
- [ ] `--onefile` の初回起動遅延（数秒）が、webview の初回ポーリングに間に合うか
      （起動は実測したが、GUI のポーリング間隔との競合は未確認）
- [ ] macOS での Gatekeeper / Windows SmartScreen の挙動
      （.dmg は作っていないため未検証。Windows は未署名のため警告が出る想定）

### 1.5 その他

- [ ] アイコンの**見た目**（プレースホルダとして妥当か）。ファイル形式は検証済みだが
      意図した図柄になっているかは目視確認が必要です
- [x] `tauri.conf.json` の `bundle.icon` に載せたファイル名が
      `cargo tauri build` の期待と一致するか
      → **一致を確認済み**（Windows ビルドがエラーなく完了）。
 補足: アイコンはプレースホルダです。`cargo tauri icon <png>` で再生成すれば確実です。
- [ ] `skipTaskbar: true` + `visible: false` の組み合わせが macOS/Windows 両方で意図通りか
      （Windows で起動しての目視確認が必要）

---

## 2. 検証手順（GUI を実際に確かめる手順）

§0.1 でビルドとサイドカー同梱までは実測済みです。
以下は **残っている GUI の目視確認** の手順です。

### ステップ A — 前提ツールの確認

```bash
rustc --version
cargo --version
python3 --version     # Windows は py -3.12 --version
```

`rustc` / `cargo` が無ければ https://rustup.rs/ から導入してください。
（または `python desktop/scripts/setup-rust.py`）

### ステップ B — セットアップとビルド（1 コマンド）

```bash
# macOS / Linux
bash setup_and_build.sh
```

```powershell
# Windows（リポジトリのルートで実行）
.\setup_and_build.ps1
```

このスクリプトが、Rust の導入 → サイドカーのビルド → 型検査 →
インストーラ作成までを順に行います。

確認: サイドカーが Tauri の探す場所にできていること。

```bash
ls -l desktop/src-tauri/binaries/
# => jp-pii-mask-server-<target-triple>[.exe]
```

### ステップ C — サイドカー単体で動作確認

```bash
# macOS
./desktop/src-tauri/binaries/jp-pii-mask-server-aarch64-apple-darwin serve --port 8787
```

別ターミナルで:

```bash
curl -i http://127.0.0.1:8787/healthz
curl -s http://127.0.0.1:8787/admin/api/state | python3 -m json.tool
```

**期待値**: `/healthz` が `200`、`/admin/api/state` が
`{"enabled":...,"masking_enabled":...,"upstream":...,"port":...,"api_key_set":...,"stats":{...}}`
を返すこと。（**検証済み。実際に 200 と JSON を確認しました。**）

さらに **CORS ヘッダの有無** を確認してください:

```bash
curl -is -H 'Origin: http://tauri.localhost' http://127.0.0.1:8787/admin/api/state | grep -i access-control
```

`Access-Control-Allow-Origin` が返れば OK です（**検証済み**）。

確認できたら Ctrl-C で停止してください。

### ステップ D — Rust 側の型検査

```bash
cd desktop/src-tauri
cargo check
```

**検証済み（PASS）**。エラーが出る場合は `VERIFY.md` §1.2 を参照してください。

### ステップ E — 開発実行と GUI の目視確認 ← 残っている未検証はここだけ

```bash
# サイドカーはステップ B で binaries/ に配置済みであること
cd desktop/src-tauri
cargo tauri dev
```

確認項目:

1. メニューバー / システムトレイにアイコンが出るか
2. 起動直後に設定ウィンドウが **表示されない**か（`visible: false` の確認）
3. トレイの「設定を開く」でウィンドウが出るか
4. ウィンドウのステータス行が「接続中」になるか
5. 大きなトグルを押して `masking_enabled` が反転するか
   （`curl -s http://127.0.0.1:8787/admin/api/state` で裏取り）
6. トレイの「マスキング保護 ON/OFF」を押すと、ウィンドウ内の表示も追従するか
7. ウィンドウの × で **アプリが終了せず隠れるだけ**か
8. トレイの「終了」で **サイドカーのプロセスも消えているか**:

```bash
ps aux | grep jp-pii-mask-server | grep -v grep    # 何も出なければOK
```

Windows:

```powershell
Get-Process jp-pii-mask-server -ErrorAction SilentlyContinue
```

> **注意:** このアプリは `taskkill /T` でサイドカーをツリーごと停止します
> （実測に基づく対応）。トレイの「終了」で孤児が残らないか確かめてください。

### ステップ F — バンドル

```bash
cargo tauri build
# または
python desktop/scripts/build-installer.py
```

**検証済み（PASS）**。Windows では実際に以下が生成されました。

- `bundle/msi/JP PII Mask_0.1.0_x64_en-US.msi` (36.5 MB)
- `bundle/nsis/JP PII Mask_0.1.0_x64-setup.exe` (35.9 MB)

同梱確認（実測済みの方法）:

```powershell
# MSI を展開して中身を見る
msiexec /a "bundle\msi\JP PII Mask_0.1.0_x64_en-US.msi" /qn TARGETDIR=C:\temp\msi_extract
Get-ChildItem C:\temp\msi_extract -Recurse -File | Sort-Object Length -Descending
# => jp-pii-mask-server.exe (34.79 MB) と jp-pii-mask-desktop.exe が並んでいればOK
```

macOS の例:

```bash
ls -l "desktop/src-tauri/target/release/bundle/macos/JP PII Mask.app/Contents/MacOS/"
```

`jp-pii-mask-server` が並んでいれば `externalBin` が効いています。

---

## 3. 既知の制約（設計上の割り切り）

- **ポートは 8787 固定**。設定ファイルの `port` は読んでいません。
  変更するには `src-tauri/src/main.rs` の `DEFAULT_PORT` と
  `src/main.js` の `PORT`、および `tauri.conf.json` の `connect-src` の 3 箇所。
- **Rust 側は HTTP を話しません**。`curl` 呼び出しや `reqwest` 依存は意図的に避けています。
  管理 API へのアクセスは webview の `fetch()` のみです。
- **トレイのチェック状態は webview が正**。トレイ操作はイベントとして webview に渡り、
  確定値が `sync_tray_checked` で書き戻されます。サイドカーが停止している間は
  トレイのチェックを変えてもすぐ元に戻ります（これは仕様として意図した挙動です）。
- **API キーは Rust 側を通りません**。webview から直接プロキシの設定 API に送られ、
  Rust のメモリやログには残りません（ログに出るのはサイドカーの stdout のみ）。
- **自動起動プラグインを登録していますが、UI から有効化する導線は未実装**です
  （要件に無いため）。必要なら設定ウィンドウにチェックボックスを追加し、
  `window.__TAURI__.autostart.enable()` を呼ぶ実装を足してください。
