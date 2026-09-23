// Tauri のビルドスクリプト。
// NOTE: このファイルは要件リストには無かったが、Tauri 2 では
//       `tauri_build::build()` を呼ぶ build.rs が必須のため追加している。
fn main() {
    tauri_build::build()
}
