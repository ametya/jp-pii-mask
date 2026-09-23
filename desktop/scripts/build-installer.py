#!/usr/bin/env python3
"""デスクトップアプリのインストーラを作る (Windows / macOS / Linux 共通)。

非エンジニアでも実行できるように、順番に自動で進めます。

  1. Rust ツールチェーンの確認 (無ければ setup-rust.py を案内)
  2. Python サイドカーのビルド (build-sidecar.py)
  3. 型検査 (cargo check) ← ここが最初の関門。失敗したらここで止まる
  4. Tauri CLI の確認 (無ければ導入)
  5. インストーラの作成 (cargo tauri build)
  6. できたファイルの場所を表示

== 使い方 ==

    python desktop/scripts/build-installer.py

    # 型検査だけで止める (GUI のビルドが通るかの確認)
    python desktop/scripts/build-installer.py --check-only

    # サイドカーを作り直さない (既にある場合)
    python desktop/scripts/build-installer.py --skip-sidecar
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

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
    """
    env = dict(os.environ)
    cargo_bin = Path.home() / ".cargo" / "bin"
    if cargo_bin.is_dir() and str(cargo_bin).lower() not in env.get("PATH", "").lower():
        env["PATH"] = str(cargo_bin) + os.pathsep + env.get("PATH", "")
    return env


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
    print(f"  $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=False, env=env).returncode


def capture(cmd: list[str], env: dict[str, str]) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=False, env=env
        ).stdout.strip()
    except OSError:
        return ""


def find_bundles(src_tauri: Path) -> list[Path]:
    """生成されたインストーラ / アプリ本体を探す。"""
    bundle_root = src_tauri / "target" / "release" / "bundle"
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


def main() -> int:
    _make_streams_robust()
    parser = argparse.ArgumentParser(description="デスクトップアプリのインストーラを作ります。")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="サイドカーと型検査までで止めます (インストーラは作りません)。",
    )
    parser.add_argument(
        "--skip-sidecar",
        action="store_true",
        help="サイドカーのビルドを省略します (既にある場合)。",
    )
    args = parser.parse_args()

    root = repo_root()
    desktop = root / "desktop"
    src_tauri = desktop / "src-tauri"
    env = cargo_env()

    print("=" * 62)
    print("JP PII Mask — デスクトップアプリのビルド")
    print("=" * 62)
    print()

    # ------------------------------------------------------------------
    # 1) Rust ツールチェーン
    # ------------------------------------------------------------------
    print("[1/5] Rust ツールチェーンを確認します...")
    if not shutil.which("cargo", path=env.get("PATH")) and not shutil.which("cargo"):
        print("  Rust が見つかりません。先に次を実行してください:")
        print("    python desktop/scripts/setup-rust.py")
        return 1
    print(f"  {capture(['rustc', '--version'], env) or 'rustc: 不明'}")
    print()

    # ------------------------------------------------------------------
    # 2) サイドカー
    # ------------------------------------------------------------------
    if args.skip_sidecar:
        print("[2/5] サイドカーのビルドをスキップします (--skip-sidecar)")
    else:
        print("[2/5] Python サイドカーをビルドします...")
        sidecar_cmd = [
            sys.executable,
            str(desktop / "scripts" / "build-sidecar.py"),
            "--skip-deps",
        ]
        rc = run(sidecar_cmd, env=env)
        if rc != 0:
            print("  エラー: サイドカーのビルドに失敗しました。")
            return rc
    print()

    # ------------------------------------------------------------------
    # 3) 型検査 (最初の関門)
    # ------------------------------------------------------------------
    print("[3/5] 型検査 (cargo check) を実行します...")
    print("      初回は依存の取得に数分かかります。")
    if run(["cargo", "check"], cwd=src_tauri, env=env) != 0:
        print()
        print("  エラー: cargo check が失敗しました。")
        print("  表示されたエラーの箇所が修正対象です。")
        print("  desktop/VERIFY.md の §1.2 に、API 差異が疑わしい箇所の一覧があります。")
        return 1
    print("  OK: 型検査を通過しました。")
    print()

    if args.check_only:
        print("=" * 62)
        print("--check-only のため、ここで終了します。")
        print("GUI のビルドが通ることは確認できました。")
        print("=" * 62)
        return 0

    # ------------------------------------------------------------------
    # 4) Tauri CLI
    # ------------------------------------------------------------------
    print("[4/5] Tauri CLI を確認します...")
    if "tauri" not in capture(["cargo", "tauri", "--version"], env):
        print("  Tauri CLI が無いため導入します (数分かかります)...")
        if run(["cargo", "install", "tauri-cli", "--version", "^2", "--locked"], env=env) != 0:
            print("  エラー: Tauri CLI の導入に失敗しました。")
            return 1
    print(f"  {capture(['cargo', 'tauri', '--version'], env) or 'tauri: 不明'}")
    print()

    # ------------------------------------------------------------------
    # 5) インストーラ作成
    # ------------------------------------------------------------------
    print("[5/5] インストーラを作成します (数分〜十数分かかります)...")
    if run(["cargo", "tauri", "build"], cwd=src_tauri, env=env) != 0:
        print()
        print("  エラー: インストーラの作成に失敗しました。")
        if os.name == "nt":
            print("  Windows でリンカ関連のエラーが出る場合は、Visual Studio Build Tools が")
            print("  未導入の可能性があります。次で導入できます (数 GB):")
            print()
            print("    winget install --id Microsoft.VisualStudio.2022.BuildTools \\")
            print('      --override "--quiet --wait --add Microsoft.VisualStudio.Workload.VCTools"')
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
        print(f"  出力先: {src_tauri / 'target' / 'release' / 'bundle'}")
        print("  (上記ディレクトリにインストーラが作られています)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
