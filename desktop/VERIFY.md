# VERIFY.md — 未検証項目と検証手順

このドキュメントは「**何が検証済みで、何が未検証か**」を正直に列挙するものです。

---

## 0. 検証済みの事実（この環境で実際に実行したこと）

Rust ツールチェーンが無いため **Tauri 側は一切ビルド・実行していません**。
その代わり、テキスト成果物に対して機械的に確認できることだけを実施しました。

| 項目 | 結果 | 方法 |
| --- | --- | --- |
| `src-tauri/tauri.conf.json` が JSON として妥当 | **PASS** | `ConvertFrom-Json` |
| `src-tauri/capabilities/default.json` が JSON として妥当 | **PASS** | `ConvertFrom-Json` |
| 全テキストファイルが UTF-8 として妥当 | **PASS** | 厳格 UTF-8 デコード（不正バイトで例外） |
| `src/main.js` が JavaScript として構文エラー無し | **PASS** | `node --check` |
| `scripts/make-icons.py` が Python として構文エラー無し | **PASS** | `ast.parse` |
| アイコン 6 ファイルの生成 | **PASS** | `scripts/make-icons.py` 実行成功 |
| アイコンのファイル形式 | **PASS(簡易)** | マジックバイト確認 (PNG=`89504e47` / ICO=`00000100` / ICNS=`icns`) |
| 作業ディレクトリの逸脱が無いこと | **PASS** | `D:\個人情報マスキング\desktop\` 以外に `dist/` `build/` `__pycache__` を生成していない |

> 上記は **JSON の構文**と**ファイル形式**の確認であり、内容の正しさ・Tauri スキーマ適合・
> アプリの動作を保証するものでは **ありません**。

## 0.5 親エージェントによる追加検証（この節が上記を上書きします）

Python 側の実装・検証を担当した親エージェント（あなた）が、
この雛形に対して以下の修正と検証を行いました。
**この節の内容は「0. 検証済みの事実」より新しく、一部の未検証項目を解消しています。**

### 解消された未検証項目

| 旧 1.1 の項目 | 新しい状況 | 根拠 |
| --- | --- | --- |
| PyInstaller でサイドカーの単一実行ファイルが生成できること | **検証済み（PASS）** | `desktop/binaries/jp-pii-mask-server.exe` を実際に生成（34.6 MB） |
| サイドカーが起動して `/healthz` に応答すること | **検証済み（PASS）** | 生成した .exe を実プロセスで起動し、実 HTTP で 21/21 PASS |
| サイドカー探索規則・起動引数の整合 | **検証済み（PASS）** | `serve --host --port --upstream` が想定どおり動作 |
| `src/main.js` の構文 | **検証済み（PASS）** | `node --check` が通過 |

検証コマンド:

```bash
python scripts/e2e_live.py --exe desktop/binaries/jp-pii-mask-server.exe
# => 21/21 PASS
```

### 雛形に対して行った修正（重要）

1. **`scripts/build-sidecar.ps1` / `.sh` のエントリポイントを修正**
   - 旧: `jp_pii_mask/cli.py` を直接指定
   - 新: `scripts/sidecar_entry.py`
   - **理由:** PyInstaller はエントリを `__main__` として扱うため、
     `from .config import ...` のような相対 import が解決できず失敗します。
   - あわせて uvicorn の `--hidden-import` を 5 件追加しました。

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

## 1. 未検証項目（この環境では検証不能）

### 1.1 根本的な制約

- [ ] **`cargo check` が通ること** — Rust 未導入のため未実行。
      本スキャフォールド最大の不確実性はここに集約されています。
- [ ] **`cargo tauri dev` / `cargo tauri build` が成功すること**
- [ ] **アプリが実際に起動してトレイ常駐すること**
- [ ] **サイドカーが実際に起動して `/healthz` に応答すること**
- [ ] **PyInstaller でサイドカーの単一実行ファイルが生成できること**

### 1.2 Tauri 2 API の不確実性（`src-tauri/src/main.rs` の `// NOTE:` 箇所）

コード内で `// NOTE:` を付けた箇所を以下に一覧します。**いずれも未検証**です。

| # | 箇所 | 不確実な内容 | 動かない場合の代替 |
| --- | --- | --- | --- |
| 1 | `main.rs` 冒頭の `use tauri::{...}` | `tray`, `menu`, `Emitter`, `Manager` の各種アイテムが想定パスに存在するか | コンパイラの提案に従い `tauri::tray::*` / `tauri::menu::*` を追加 |
| 2 | `SidecarState::kill_child` | `CommandChild::kill(self)` が by-value か `&self` か | エラーが出たら `child.kill()` の呼び方（`&mut` など）を調整 |
| 3 | `sync_tray_checked` | `AppHandle::tray_by_id(&str) -> Option<TrayIcon<R>>` の存在と戻り値 | `app.tray_by_id()` が無ければ、`TrayIcon` ハンドルを `app.manage(...)` で保持して直接参照する |
| 4 | `sync_tray_checked` | `TrayIcon::set_checked(bool) -> tauri::Result<()>` の存在 | 無ければ `CheckMenuItem` のハンドルを `manage` して `CheckMenuItem::set_checked` を呼ぶ |
| 5 | `tauri_plugin_autostart::init` | 第 2 引数の型（`Option<Vec<&str>>` vs `Vec<&str>`） | コンパイラエラーに従い `Some(vec![])` / `vec![]` / `None` を切り替え |
| 6 | `MenuBuilder::items(&[&dyn IsMenuItem<R>])` | 配列で受け付けるか | `.item(&toggle_item).item(&settings_item).item(&quit_item)` のチェーンに分解 |
| 7 | `TrayIconBuilder::with_id(...).on_menu_event(...).on_tray_icon_event(...)` | ビルダメソッド名とクロージャ引数 | バージョン差異があれば `TrayIconBuilder::new()` + 各 setter に分解 |
| 8 | `TrayIconEvent::DoubleClick { button, .. }` | バリアント名・フィールド構成 | `TrayIconEvent::Click { button, button_state, .. }` に変更 |
| 9 | `CheckMenuItem::as_check_menuitem()` / `is_checked()` | getter 名 | 無ければ「反転要求」を送るだけにして、確定値は webview が `GET /admin/api/state` で解決（既にその設計） |
| 10 | `CommandEvent::{Stdout, Stderr, Terminated}` | バリアント名と `Terminated` の payload 形状 | `payload.code` が無ければ `payload` のデバッグ表示に変更 |
| 11 | `capabilities/default.json` の権限識別子 | `core:window:allow-show` / `core:event:default` / `shell:default` などの正確な名称 | `cargo tauri dev` 時に「permission not found」が出たら、そのメッセージが正しい名称を教えてくれる |
| 12 | `tauri.conf.json` の `$schema` / 各キー | Tauri 2 のスキーマに適合するか（`bundle.targets:"all"`, `app.withGlobalTauri` など） | ビルド時の検証エラーに従って修正 |

### 1.3 フロントエンドの不確実性（`src/main.js`）

- [ ] `window.__TAURI__` が `withGlobalTauri: true` で実際に生えるか
      （CSP と併用時に未定義にならないか）
- [ ] `window.__TAURI__.core.invoke` / `window.__TAURI__.event.listen` の名前空間が正しいか
      （Tauri 2 で `window.__TAURI__.tauri.invoke` から変わった可能性）
- [ ] Rust コマンド `sync_tray_checked` が webview から `syncTrayChecked`
      （camelCase）で呼べるか、snake_case のままか
- [ ] **CORS**: `http://tauri.localhost`（Windows）/ `tauri://localhost`（macOS）から
      `http://127.0.0.1:8787` への `fetch` が成功するか。
      **Python 側が `Access-Control-Allow-Origin` を返さない場合、確実に失敗します。**
      これは Python 側の設定で解決する必要があります（本シェルからは変更できません）。
- [ ] `connect-src` の CSP 指定が実際に `fetch` を許可するか
- [ ] `/admin/api/state` の `stats` の中身（キー名が `detected` / `replaced` か不明）。
      本実装は複数候補を試し、拾えなければ `—` を表示するフォールバックにしています。
- [ ] `POST /admin/api/state` が更新後の state を返すか（返さない前提で GET 再取得する実装済み）
- [ ] `api_key: ""` が「キー消去」として機能するか（「変更なし」と解釈される可能性あり）

### 1.4 サイドカー周り

- [ ] `PyInstaller` のコマンド（`--collect-all jp_pii_mask` 等）が実際に成功するか。
      特に `spacy` / `ja-ginza` を入れた構成では追加フックが必要になる可能性が高い
- [ ] サイドカーの実際のバイナリ名（`jp-pii-mask-server`）が
      `pyproject.toml` の想定と一致するか。今回は要件通り
      `externalBin: ["binaries/jp-pii-mask-server"]` としており、
      **CLI の `serve` サブコマンドがこの名前の実行ファイルに含まれる前提**です
- [ ] `--onefile` の初回起動遅延（数秒）が、webview の初回ポーリングに間に合うか
- [ ] macOS での Gatekeeper / Windows SmartScreen の挙動

### 1.5 その他

- [ ] アイコンの**見た目**（プレースホルダとして妥当か）。ファイル形式は検証済みだが
      意図した図柄になっているかは目視確認が必要です
- [ ] `tauri.conf.json` の `bundle.icon` に載せたファイル名が
      `cargo tauri build` の期待と一致するか（`icon.icns` / `icon.ico` の要否はプラットフォーム依存）
 補足: アイコンはプレースホルダです。`cargo tauri icon <png>` で再生成すれば確実です。
- [ ] `skipTaskbar: true` + `visible: false` の組み合わせが macOS/Windows 両方で意図通りか

---

## 2. 検証手順（あなたが実行するコマンド）

以下を **この順に** 実行し、どこで失敗するかを記録してください。
失敗した箇所がそのまま修正対象です。

### ステップ A — 前提ツールの確認

```bash
rustc --version
cargo --version
python3 --version     # Windows は py -3.12 --version
```

`rustc` / `cargo` が無ければ https://rustup.rs/ から導入してください。

### ステップ B — サイドカーを作る（Tauri より先にこちら）

```bash
# macOS / Linux
bash desktop/scripts/build-sidecar.sh

# Windows (PowerShell)
.\desktop\scripts\build-sidecar.ps1
```

確認: `desktop/binaries/` に `jp-pii-mask-server-<target-triple>[.exe]` ができていること。

```bash
ls -l desktop/binaries/
```

### ステップ C — サイドカー単体で動作確認

```bash
# macOS
./desktop/binaries/jp-pii-mask-server-aarch64-apple-darwin serve --port 8787
```

別ターミナルで:

```bash
curl -i http://127.0.0.1:8787/healthz
curl -s http://127.0.0.1:8787/admin/api/state | python3 -m json.tool
```

**期待値**: `/healthz` が `200`、`/admin/api/state` が
`{"enabled":...,"masking_enabled":...,"upstream":...,"port":...,"api_key_set":...,"stats":{...}}`
を返すこと。さらに **CORS ヘッダの有無** を必ず確認してください:

```bash
curl -is -H 'Origin: http://tauri.localhost' http://127.0.0.1:8787/admin/api/state | grep -i access-control
```

`Access-Control-Allow-Origin` が返らなければ、**Tauri アプリの `fetch` は失敗します**。
Python 側に CORS 設定を追加する必要があります。

確認できたら Ctrl-C で停止してください。

### ステップ D — Rust 側の型チェック（最初の関門）

```bash
cd desktop/src-tauri
cargo check 2>&1 | tee /tmp/check.log     # Windows: cargo check
```

**このステップがこのスキャフォールドの成否を決めます。**
`VERIFY.md` 1.2 の表と突き合わせて修正してください。

### ステップ E — 開発実行

```bash
# サイドカーはステップ B で binaries/ に配置済みであること
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

### ステップ F — バンドル

```bash
cargo tauri build
```

確認: `src-tauri/target/release/bundle/` にインストーラ（macOS は `.dmg` / `.app`、
Windows は `.msi` / `-setup.exe`）ができていること。
生成物の中にサイドカーの実体が同梱されているかも確認してください。

```bash
# macOS の例
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
