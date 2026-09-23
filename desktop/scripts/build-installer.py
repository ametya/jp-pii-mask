#!/usr/bin/env python3
"""アプリとインストーラを準備する (Windows / macOS / Linux 共通)。

むずかしい言葉を使わずに、順番に自動で進めます。
ダブルクリック用の入口から呼ばれるのが基本です。

  1. 組み立てに必要な道具 (Rust) の確認 — 無ければ自動で導入
  2. アプリに内蔵するサーバーの準備 (PyInstaller)
  3. プログラムの確認 (cargo check) ← ここで止まると原因が分かります
  4. 仕上げの道具 (Tauri CLI) の確認 — 無ければ自動で導入
  5. インストーラの作成 (cargo tauri build)

== 使い方 ==

    python desktop/scripts/build-installer.py

    # 3 までで止める (アプリを組み立てられるかだけ確認)
    python desktop/scripts/build-installer.py --check-only

    # 内蔵サーバーを作り直さない (既にある場合)
    python desktop/scripts/build-installer.py --skip-sidecar
"""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
from pathlib import Path

# このスクリプトと同じディレクトリの共通ヘルパを読む。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _toolchain import child_env, resolve, which

SIDECAR_NAME = "jp-pii-mask-server"


def _make_streams_robust() -> None:
    """標準出力・標準エラーを「落ちない」設定にする。

    Windows の既定コードページ (cp932) は em ダッシュや絵文字を表現できず、
    それを print しようとすると UnicodeEncodeError でプロセスごと終了する。
    エンコードできない文字は例外ではなく置換文字にする。
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def cargo_env() -> dict[str, str]:
    """cargo の bin を PATH に足した環境変数を返す。

    rustup は既定で PATH を書き換えないため (このリポジトリでは
    グローバル環境を汚さない方針)、子プロセスに明示的に渡す。

    注意: これだけでは Windows で cargo は起動しない (親 PATH で
    実行ファイルを探すため)。実際の起動は run() が絶対パスに
    解決してから行う。
    """
    return child_env()


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
    # 実行ファイルを絶対パスに解決する。Windows の CreateProcess は
    # 渡された env の PATH ではなく親の PATH で探すため、これが必須。
    cmd = resolve(cmd)
    print(f"  $ {' '.join(cmd)}", flush=True)
    try:
        return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=False, env=env).returncode
    except FileNotFoundError:
        print(f"  エラー: 実行ファイルが見つかりません: {cmd[0]}")
        print("  組み立てに必要な道具が入っていない可能性があります。")
        return 127


def capture(cmd: list[str], env: dict[str, str]) -> str:
    cmd = resolve(cmd)
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=False, env=env
        ).stdout.strip()
    except OSError:
        return ""


def target_dir(src_tauri: Path) -> Path:
    """Cargo の出力先。CARGO_TARGET_DIR が指定されていればそれを使う。"""
    override = os.environ.get("CARGO_TARGET_DIR")
    if override:
        return Path(override)
    return src_tauri / "target"


def find_bundles(src_tauri: Path) -> list[Path]:
    """できあがったインストーラ / アプリ本体を探す。"""
    bundle_root = target_dir(src_tauri) / "release" / "bundle"
    if not bundle_root.is_dir():
        return []
    wanted = (".msi", ".exe", ".dmg", ".app", ".deb", ".rpm", ".AppImage")
    found: list[Path] = []
    for path in sorted(bundle_root.rglob("*")):
        if path.suffix in wanted:
            # NSIS の *-setup.exe と MSI、.app バンドルを拾う。
            # bundle 直下の .exe (アプリ本体) も含める。
            found.append(path)
    return found


def app_exe_path(src_tauri: Path) -> Path:
    """デスクトップアプリ本体の出力先。"""
    return target_dir(src_tauri) / "release" / "jp-pii-mask-desktop.exe"


def is_locked(path: Path) -> bool:
    """起動中のアプリにつかまれているファイルかどうか (Windows)。"""
    if not path.exists():
        return False
    try:
        with open(path, "r+b"):
            return False
    except OSError:
        return True


def warn_if_app_running(src_tauri: Path) -> bool:
    """アプリが起動中なら知らせる。

    起動中のアプリは自分の実行ファイルをつかんでいるため、
    新しいものに置き換えられません (Windows では access denied)。
    数分待たされた上げくに失敗するので、先に伝えます。
    """
    if os.name != "nt":
        return False
    if not is_locked(app_exe_path(src_tauri)):
        return False
    print()
    print("  ！アプリが起動したままです。")
    print("  画面右下（通知領域）のアイコンを右クリックして「終了」を選び、")
    print("  アプリを閉じてから、もう一度お試しください。")
    print("  （起動したままだと、できあがったファイルを置き換えられません）")
    print()
    return True


def ensure_rust(env: dict[str, str]) -> int:
    """組み立てに必要な道具 (Rust) を用意する。

    入っていなければ setup-rust.py が自動で導入します。
    Windows でリンカ (MSVC) が無い場合は 2 を返します
    (ソースの確認までは通りますが、インストーラは作れません)。
    """
    print("[1/5] アプリを組み立てる道具を確認します...")
    setup_rust = repo_root() / "desktop" / "scripts" / "setup-rust.py"
    code = run([sys.executable, str(setup_rust), "--yes"], env=env)
    if code == 2:
        print()
        print("  このまま進めると、インストーラの作成で失敗します。")
        print("  上のメッセージのコマンドを実行してから、もう一度お試しください。")
        return 2
    if code != 0 or not which("cargo"):
        print()
        print("  エラー: 組み立ての道具 (Rust) を用意できませんでした。")
        print("  上のメッセージを確認してください。手動導入: https://rustup.rs/")
        return 1
    print()
    return 0


def main() -> int:
    _make_streams_robust()
    parser = argparse.ArgumentParser(description="アプリとインストーラを準備します。")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="プログラムの確認までで止めます (インストーラは作りません)。",
    )
    parser.add_argument(
        "--skip-sidecar",
        action="store_true",
        help="内蔵サーバーの準備を省略します (既にある場合)。",
    )
    parser.add_argument(
        "--skip-rust-check",
        action="store_true",
        help="道具の確認を省略します (CI などで既に入っている場合)。",
    )
    args = parser.parse_args()

    root = repo_root()
    desktop = root / "desktop"
    src_tauri = desktop / "src-tauri"
    env = cargo_env()

    print("=" * 62)
    print("JP PII Mask — アプリとインストーラを準備します")
    print("=" * 62)
    print()

    # ------------------------------------------------------------------
    # 1) 組み立てに必要な道具 (Rust)
    # ------------------------------------------------------------------
    if args.skip_rust_check:
        print("[1/5] 道具の確認をスキップします (--skip-rust-check)")
        if not which("cargo"):
            print("  エラー: cargo が見つかりません。")
            return 1
        print()
    else:
        code = ensure_rust(env)
        if code != 0:
            return code

    # ------------------------------------------------------------------
    # 2) アプリに内蔵するサーバー
    # ------------------------------------------------------------------
    if args.skip_sidecar:
        print("[2/5] 内蔵サーバーの準備をスキップします (--skip-sidecar)")
    else:
        print("[2/5] アプリに内蔵するサーバーを準備します (数分かかります)...")
        sidecar_cmd = [
            sys.executable,
            str(desktop / "scripts" / "build-sidecar.py"),
            "--skip-deps",
        ]
        rc = run(sidecar_cmd, env=env)
        if rc != 0:
            print("  エラー: 内蔵サーバーの準備に失敗しました。")
            return rc
    print()

    # ------------------------------------------------------------------
    # 3) プログラムの確認
    # ------------------------------------------------------------------
    print("[3/5] プログラムを確認します (初回は数分かかります)...")
    if run(["cargo", "check"], cwd=src_tauri, env=env) != 0:
        print()
        print("  エラー: プログラムの確認で問題が見つかりました。")
        print("  上の赤字の箇所が直すべき場所です。")
        print("  desktop/VERIFY.md の §1.2 に、直し方が載っています。")
        return 1
    print("  OK: 問題ありませんでした。")
    print()

    if args.check_only:
        print("=" * 62)
        print("--check-only のため、ここで終了します。")
        print("アプリを組み立てられることは確認できました。")
        print("=" * 62)
        return 0

    # ------------------------------------------------------------------
    # 4) 仕上げの道具 (Tauri CLI)
    # ------------------------------------------------------------------
    print("[4/5] 仕上げの道具を確認します...")
    if "tauri" not in capture(["cargo", "tauri", "--version"], env):
        print("  入っていないため導入します (数分かかります)...")
        if run(["cargo", "install", "tauri-cli", "--version", "^2", "--locked"], env=env) != 0:
            print("  エラー: 仕上げの道具を導入できませんでした。")
            return 1
    print(f"  {capture(['cargo', 'tauri', '--version'], env) or 'tauri: 不明'}")
    print()

    # アプリが起動中だと、できあがったファイルを置き換えられず失敗する。
    # 数分待たされた上げくに失敗するので、先に知らせる。
    if warn_if_app_running(src_tauri):
        return 1

    # ------------------------------------------------------------------
    # 5) インストーラの作成
    # ------------------------------------------------------------------
    print("[5/5] インストーラを作ります (数分〜十数分かかります)...")
    if run(["cargo", "tauri", "build"], cwd=src_tauri, env=env) != 0:
        print()
        print("  エラー: インストーラを作れませんでした。")
        if os.name == "nt":
            if is_locked(app_exe_path(src_tauri)):
                print("  アプリが起動したままのようです。")
                print("  画面右下（通知領域）のアイコンを右クリックして「終了」を選び、")
                print("  アプリを閉じてから、もう一度お試しください。")
            else:
                print("  リンカ関連のエラーが出ている場合は、次の道具が")
                print("  入っていない可能性があります (数 GB あります):")
                print()
                print("    winget install --id Microsoft.VisualStudio.2022.BuildTools --override "
                      "\"--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools\"")
        return 1

    # ------------------------------------------------------------------
    # 結果
    # ------------------------------------------------------------------
    print()
    print("=" * 62)
    print("完了しました。")
    print("=" * 62)
    bundles = find_bundles(src_tauri)
    if bundles:
        print()
        print("できあがったファイル:")
        for path in bundles:
            try:
                mb = path.stat().st_size / (1024 * 1024)
                print(f"  {path}  ({mb:.1f} MB)")
            except OSError:
                print(f"  {path}")
        print()
        print("このファイルを配布すれば、他の人はインストールするだけで使えます。")
    else:
        print()
        print(f"  出力先: {target_dir(src_tauri) / 'release' / 'bundle'}")
        print("  (上記ディレクトリにインストーラが作られています)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
