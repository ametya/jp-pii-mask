#!/usr/bin/env python3
"""Rust ツールチェーンを導入する (未導入なら)。Windows / macOS / Linux 共通。

非エンジニアでも迷わないように、以下を自動で行います。

  1. rustc / cargo があるか確認 (あれば何もしない)
  2. 無ければ rustup を取得して stable を導入
  3. Windows では MSVC リンカ (Visual Studio Build Tools) の有無を確認し、
     無ければ winget で入れるかどうかを尋ねる

== なぜ MSVC Build Tools が要るか ==

`cargo check` はリンカ不要ですが、`cargo tauri build` は
**ネイティブの実行ファイルをリンクする**ため MSVC のリンカが必要です。
Windows では Visual Studio Build Tools の「C++ によるデスクトップ開発」
ワークロードがそれに当たります (数 GB あります)。

== 使い方 ==

    python desktop/scripts/setup-rust.py
    python desktop/scripts/setup-rust.py --yes    # 確認なしで進める (CI 用)
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

# このスクリプトと同じディレクトリの共通ヘルパを読む。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _toolchain import resolve, which


def _make_streams_robust() -> None:
    """標準出力・標準エラーを「落ちない」設定にする。

    Windows の既定コードページ (cp932) は em ダッシュや絵文字を表現できず、
    それを print しようとすると UnicodeEncodeError でプロセスごと終了する。
    エンコードできない文字は例外ではなく置換文字にする。
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]

RUSTUP_URL_WIN = "https://win.rustup.rs/x86_64"
RUSTUP_URL_UNIX = "https://sh.rustup.rs"

CARGO_BIN_DIR = Path.home() / ".cargo" / "bin"


def cargo_home_in_path() -> str:
    """cargo の bin を含む PATH を返す (子プロセスに渡す用)。"""
    current = os.environ.get("PATH", "")
    entry = str(CARGO_BIN_DIR)
    if entry.lower() in current.lower():
        return current
    return entry + os.pathsep + current


def have(tool: str) -> bool:
    """PATH (および ~/.cargo/bin) に実行ファイルがあるか。"""
    return which(tool) is not None


def rustc_version() -> str:
    """`rustc --version` の出力を返す (見つからなければ空文字)。

    絶対パスに解決してから呼ぶ。Windows では env の PATH を見ないため。
    """
    rustc = which("rustc")
    if not rustc:
        return ""
    try:
        return subprocess.run(
            [rustc, "--version"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return ""


def run(cmd: list[str], env: dict[str, str] | None = None) -> int:
    cmd = resolve(cmd)
    print("  $ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=False, env=env).returncode


def msvc_linker_present() -> bool:
    """Windows で MSVC リンカ (link.exe) が見つかるか。

    完全な判定は難しいので、Build Tools の代表的な配置を探す。
    見つからなくても `cargo check` は動くため、警告に留める。
    """
    if os.name != "nt":
        return True

    program_files = os.environ.get("ProgramFiles(x86)") or os.environ.get("ProgramFiles") or ""
    vswhere = Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        try:
            out = subprocess.run(
                [
                    str(vswhere),
                    "-latest",
                    "-products",
                    "*",
                    "-requires",
                    "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                    "-property",
                    "installationPath",
                ],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if out:
                return True
        except OSError:
            pass

    # フォールバック: リンカらしきものが PATH にあるか
    return shutil.which("link.exe") is not None


def install_rustup(yes: bool) -> int:
    """rustup を取得して stable を入れる。"""
    if os.name == "nt":
        import urllib.request

        tmp = Path(os.environ.get("TEMP", ".")) / "rustup-init.exe"
        print(f"  rustup-init.exe をダウンロードしています... ({RUSTUP_URL_WIN})")
        urllib.request.urlretrieve(RUSTUP_URL_WIN, tmp)
        print("  Rust をインストールしています (数分かかります)...")
        # --no-modify-path: PATH はこのスクリプトが子プロセスに渡すので不要。
        # グローバルな環境変数を勝手に書き換えないための配慮。
        return run(
            [
                str(tmp),
                "-y",
                "--default-toolchain",
                "stable",
                "--profile",
                "minimal",
                "--no-modify-path",
            ]
        )

    print("  rustup をダウンロードして実行します...")
    import urllib.request

    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "rustup-init.sh"
    urllib.request.urlretrieve(RUSTUP_URL_UNIX, tmp)
    tmp.chmod(0o755)
    return run(["/bin/sh", str(tmp), "-y", "--default-toolchain", "stable", "--profile", "minimal"])


def main() -> int:
    _make_streams_robust()
    parser = argparse.ArgumentParser(description="Rust ツールチェーンを導入します。")
    parser.add_argument("--yes", action="store_true", help="確認なしで進めます (CI 用)。")
    args = parser.parse_args()

    print("=" * 62)
    print("Rust ツールチェーンの確認")
    print("=" * 62)

    # ------------------------------------------------------------------
    # 1) 既に入っているか
    # ------------------------------------------------------------------
    if have("rustc") and have("cargo"):
        print(f"  すでに導入済みです: {rustc_version()}")
        already = True
    else:
        print("  Rust は未導入です。自動で導入します。")
        if not args.yes:
            try:
                answer = input("  続けてよいですか? [Y/n]: ").strip().lower()
            except EOFError:
                answer = "y"
            if answer not in ("", "y", "yes"):
                print("  中止しました。https://rustup.rs/ から手動で導入できます。")
                return 1
        if install_rustup(args.yes) != 0:
            print("  エラー: Rust の導入に失敗しました。")
            return 1
        already = False

    ver = rustc_version()
    print(f"  rustc: {ver or '(取得できませんでした)'}")
    print()

    # ------------------------------------------------------------------
    # 2) Windows: MSVC リンカの確認
    # ------------------------------------------------------------------
    if os.name == "nt" and not msvc_linker_present():
        print("=" * 62)
        print("  ⚠ Visual Studio Build Tools (MSVC リンカ) が見つかりません")
        print("=" * 62)
        print()
        print("  `cargo check` (ソースの型検査) は動きますが、")
        print("  `cargo tauri build` (インストーラ作成) にはリンカが必要です。")
        print()
        print("  次のコマンドで導入できます (数 GB のダウンロード):")
        print()
        print("    winget install --id Microsoft.VisualStudio.2022.BuildTools \\")
        print("      --override \"--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools\"")
        print()
        print("  導入後、このスクリプトを実行し直してください。")
        print()
        return 2

    print("=" * 62)
    if already:
        print("Rust ツールチェーンの準備は完了しています。")
    else:
        print("Rust ツールチェーンの導入が完了しました。")
    print("=" * 62)
    print()
    print("次の手順:")
    print("  リポジトリにある「インストーラを作る.bat」(Windows) または")
    print("  「インストーラを作る.command」(macOS) を開いてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
