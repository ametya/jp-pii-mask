// =============================================================================
// JP PII Mask — デスクトップシェル (Tauri 2)
// =============================================================================
//
// 役割:
//   1. Python サイドカー (jp-pii-mask-server) を `serve --port 8787` で起動する
//   2. システムトレイ / メニューバーに常駐し、ON/OFF・設定表示・終了を提供する
//   3. 終了時にサイドカーを確実に kill する
//
// ⚠️ 未検証:
//   このファイルは Rust ツールチェーンの無い環境で執筆されたため、
//   `cargo check` すら通していません。Tauri 2 の API 名・シグネチャには
//   不確実な箇所があり、該当箇所には `// NOTE:` を付けています。
//   詳細と検証手順は ../VERIFY.md を参照してください。
//
// 設計方針 (重要):
//   Rust 側は HTTP 通信を一切行いません。`reqwest` も `curl` も使いません。
//   管理 API (http://127.0.0.1:8787/admin/api/state) へのアクセスは
//   100% webview (src/main.js) の `fetch()` が担当し、トレイ操作は
//   イベント "tray-request-toggle" として webview に転送されます。
//   トレイのチェック状態は webview から `sync_tray_checked` コマンドで
//   書き戻されます (API 呼び出しの結果とトレイ表示を乖離させないため)。
//
//   この構成にした理由:
//     - 追加の HTTP クレート依存を増やさない
//     - API キーなどの機微情報を Rust 側のメモリ/ログに載せない
//     - 既に webview が状態をポーリングしているため、二重実装を避ける

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use tauri::{
    menu::{CheckMenuItemBuilder, MenuBuilder, MenuItemBuilder},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, RunEvent,
};

use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

/// サイドカーが待ち受けるポート。CLI の既定値と一致させること。
///
/// NOTE: 設定ファイル (%USERPROFILE%\.jp-pii-mask\config.json /
///       ~/.jp-pii-mask/config.json) の `port` からの読み込みは未実装。
///       変更する場合は、この定数と src/main.js の ADMIN_API を両方直すこと。
const DEFAULT_PORT: u16 = 8787;

/// サイドカーの externalBin 名 (ターゲットトリプル接尾辞は Tauri が解決する)。
const SIDECAR_NAME: &str = "jp-pii-mask-server";

/// トレイアイコンの識別子。
const TRAY_ID: &str = "main-tray";

/// メニュー項目の識別子。
const MENU_ID_TOGGLE: &str = "masking-toggle";
const MENU_ID_SETTINGS: &str = "open-settings";
const MENU_ID_QUIT: &str = "quit-app";

/// トレイ → webview へ「トグル要求」を運ぶイベント名。
///
/// payload は bool:
///   true  → マスキング保護を ON にしたい
///   false → OFF にしたい
const EVENT_TRAY_REQUEST_TOGGLE: &str = "tray-request-toggle";

/// 起動したサイドカーのハンドル。
///
/// アプリ終了時に kill するために保持する。`Option` なのは、起動失敗時や
/// 外部で既に `jp-pii-mask serve` が動いている運用を許容するため。
struct SidecarState(Mutex<Option<CommandChild>>);

impl SidecarState {
    fn new() -> Self {
        Self(Mutex::new(None))
    }

    /// 保持している子プロセスを kill する。二重呼び出し安全。
    fn kill_child(&self) {
        // NOTE: `Mutex::lock()` は Result を返す。poisoned でも中身を取り出せるよう
        //       unwrap_or_else で復旧する。
        let mut guard = self.0.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(child) = guard.take() {
            // NOTE: 正常に kill できたかを UI に通知する仕組みは未実装。
            //       失敗しても終了処理は続行する。
            if let Err(err) = child.kill() {
                eprintln!("[jp-pii-mask-desktop] サイドカーの終了に失敗しました: {err}");
            } else {
                println!("[jp-pii-mask-desktop] サイドカーを終了しました");
            }
        }
    }
}

// =============================================================================
// Tauri コマンド (webview → Rust)
// =============================================================================

/// トレイのチェック状態を webview から同期する。
///
/// webview が管理 API を叩いた「結果」を渡してくるので、
/// トレイ表示をその値に合わせる。API 呼び出し自体は webview 側の責務。
#[tauri::command]
fn sync_tray_checked(app: AppHandle, checked: bool) -> Result<(), String> {
    // NOTE: `Manager::tray_by_id` のシグネチャは Tauri 2 で
    //       `fn tray_by_id(&self, id: &str) -> Option<TrayIcon<R>>` と想定。
    let tray = app
        .tray_by_id(TRAY_ID)
        .ok_or_else(|| format!("トレイ '{TRAY_ID}' が見つかりません"))?;
    // NOTE: `TrayIcon::set_checked` の存在は未確認。無ければ
    //       メニュー項目ハンドルを別途 `app.manage` して
    //       `CheckMenuItem::set_checked` を呼ぶ実装に差し替えること。
    tray.set_checked(checked)
        .map_err(|e| format!("トレイのチェック状態を更新できません: {e}"))
}

/// webview から明示的にアプリを終了させる (設定ウィンドウの「終了」ボタン用)。
#[tauri::command]
fn quit_app(app: AppHandle) {
    // NOTE: サイドカーの kill は `RunEvent::ExitRequested` 側で行うため
    //       ここでは exit を要求するだけ。
    app.exit(0);
}

/// サイドカーが起動済みかどうかを webview に伝える (表示用)。
#[tauri::command]
fn sidecar_running(state: tauri::State<'_, SidecarState>) -> bool {
    let guard = state.0.lock().unwrap_or_else(|e| e.into_inner());
    guard.is_some()
}

// =============================================================================
// エントリポイント
// =============================================================================

fn main() {
    tauri::Builder::default()
        // サイドカー実行に必須。
        .plugin(tauri_plugin_shell::init())
        // ログイン時の自動起動。
        // NOTE: `tauri_plugin_autostart::init` の第 1 引数は macOS 用ランチャ種別
        //       (`MacosLauncher::LaunchAgent`)、第 2 引数は起動時引数。第 2 引数は
        //       バージョンによって `Option<Vec<&str>>` / `Vec<&str>` の差異がある
        //       可能性がある。`cargo check` で確認すること。
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .manage(SidecarState::new())
        .invoke_handler(tauri::generate_handler![
            sync_tray_checked,
            quit_app,
            sidecar_running
        ])
        .setup(|app| {
            // -----------------------------------------------------------------
            // 1) サイドカー (Python 本体) の起動
            // -----------------------------------------------------------------
            let result = app
                .shell()
                .sidecar(SIDECAR_NAME)
                .and_then(|cmd| {
                    cmd.args(["serve", "--port", &DEFAULT_PORT.to_string()])
                        .spawn()
                });

            match result {
                Ok((mut rx, child)) => {
                    println!(
                        "[jp-pii-mask-desktop] サイドカー {SIDECAR_NAME} を起動しました (port {DEFAULT_PORT})"
                    );
                    // 子プロセスのハンドルを保持 (終了時に kill する)。
                    {
                        let state = app.state::<SidecarState>();
                        let mut guard = state.0.lock().unwrap_or_else(|e| e.into_inner());
                        *guard = Some(child);
                    }
                    // 標準出力/エラーを親のログに転送する。
                    // NOTE: `CommandEvent` のバリアント名 (`Stdout` / `Stderr` /
                    //       `Terminated`) は tauri-plugin-shell v2 を前提としている。
                    tauri::async_runtime::spawn(async move {
                        use tauri_plugin_shell::process::CommandEvent;
                        while let Some(event) = rx.recv().await {
                            match event {
                                CommandEvent::Stdout(line) => {
                                    print!("[sidecar] {}", String::from_utf8_lossy(&line));
                                }
                                CommandEvent::Stderr(line) => {
                                    eprint!("[sidecar] {}", String::from_utf8_lossy(&line));
                                }
                                CommandEvent::Terminated(payload) => {
                                    eprintln!(
                                        "[jp-pii-mask-desktop] サイドカーが終了しました: code={:?}",
                                        payload.code
                                    );
                                }
                                _ => {}
                            }
                        }
                    });
                }
                Err(err) => {
                    // 起動失敗してもアプリ自体は常駐させる。
                    // (外部で `jp-pii-mask serve` を動かしている運用も想定内)
                    eprintln!(
                        "[jp-pii-mask-desktop] サイドカーを起動できませんでした: {err}\n\
                         binaries/ に jp-pii-mask-server-<target-triple>[.exe] を配置したか確認してください。"
                    );
                }
            }

            // -----------------------------------------------------------------
            // 2) トレイメニューの構築
            // -----------------------------------------------------------------
            let toggle_item = CheckMenuItemBuilder::with_id(MENU_ID_TOGGLE, "マスキング保護 ON/OFF")
                // 起動時は「未接続」を意味する OFF 表示から始める。
                // webview が /admin/api/state を取得した時点で sync_tray_checked により上書きされる。
                .checked(false)
                .build(app)?;

            let settings_item = MenuItemBuilder::with_id(MENU_ID_SETTINGS, "設定を開く").build(app)?;
            let quit_item = MenuItemBuilder::with_id(MENU_ID_QUIT, "終了").build(app)?;

            // NOTE: `MenuBuilder::items` は `&[&dyn IsMenuItem<R>]` を取る想定。
            //       ビルドが通らない場合は `MenuBuilder::new(app).item(&toggle_item).item(...)`
            //       の形に分解すること。
            let menu = MenuBuilder::new(app)
                .items(&[&toggle_item, &settings_item, &quit_item])
                .build()?;

            // -----------------------------------------------------------------
            // 3) トレイアイコンの構築
            // -----------------------------------------------------------------
            let mut builder = TrayIconBuilder::with_id(TRAY_ID)
                .menu(&menu)
                // 左クリックでもメニューを出す (macOS の慣習に合わせる)。
                .show_menu_on_left_click(true)
                .tooltip("JP PII Mask")
                .on_menu_event(move |app_handle, event| {
                    match event.id().as_ref() {
                        MENU_ID_TOGGLE => {
                            // メニュークリック時点で CheckMenuItem は Tauri 側が
                            // 自動トグル済み。よって「新しい状態」は、クリック直後の
                            // メニュー項目の checked 値を読むのが正しい。
                            // NOTE: `CheckMenuItem::is_checked` 相当の getter 名は未確認。
                            //       ここでは確実性を優先し、webview が
                            //       /admin/api/state を再取得して解決する設計にしている
                            //       (payload の bool は「反転要求」のヒント)。
                            let desired = app_handle
                                .tray_by_id(TRAY_ID)
                                .and_then(|t| t.menu())
                                .and_then(|m| m.get(MENU_ID_TOGGLE))
                                .and_then(|i| i.as_check_menuitem().cloned())
                                .map(|i| i.is_checked())
                                .unwrap_or(true);

                            // webview にトグル要求を投げる。
                            // NOTE: Tauri 2 では `Emitter` トレイトが必要。
                            if let Err(err) = app_handle.emit(EVENT_TRAY_REQUEST_TOGGLE, desired) {
                                eprintln!(
                                    "[jp-pii-mask-desktop] トグルイベントの送信に失敗しました: {err}"
                                );
                            }
                        }
                        MENU_ID_SETTINGS => {
                            if let Some(win) = app_handle.get_webview_window("main") {
                                let _ = win.show();
                                let _ = win.set_focus();
                            } else {
                                eprintln!("[jp-pii-mask-desktop] 設定ウィンドウ 'main' がありません");
                            }
                        }
                        MENU_ID_QUIT => {
                            // サイドカーの kill は RunEvent::ExitRequested で行う。
                            app_handle.exit(0);
                        }
                        _ => {}
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    // ダブルクリックで設定ウィンドウを開く。
                    if let TrayIconEvent::DoubleClick {
                        button: MouseButton::Left,
                        ..
                    } = event
                    {
                        let app_handle = tray.app_handle();
                        if let Some(win) = app_handle.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                    // NOTE: `TrayIconEvent::Click { button, button_state, .. }` を使った
                    //       「左クリック単発でウィンドウを開く」実装も可能だが、
                    //       show_menu_on_left_click(true) と衝突するため採用していない。
                    let _ = (MouseButtonState::Up, MouseButton::Left);
                });

            // アイコンは tauri.conf.json の bundle.icon から既定アイコンを使う。
            if let Some(icon) = app.default_window_icon() {
                builder = builder.icon(icon.clone());
            } else {
                eprintln!("[jp-pii-mask-desktop] 既定アイコンが取得できませんでした");
            }

            builder.build(app)?;

            Ok(())
        })
        // ウィンドウの × ボタンは「閉じる」ではなく「隠す」。
        // トレイ常駐アプリなので、設定ウィンドウを閉じても常駐は続ける。
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("Tauri アプリの初期化に失敗しました")
        .run(|app_handle, event| {
            // 終了要求時に必ずサイドカーを kill する。
            if let RunEvent::ExitRequested { .. } = event {
                let state = app_handle.state::<SidecarState>();
                state.kill_child();
            }
        });
}
