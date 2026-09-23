#!/usr/bin/env python3
"""jp_pii_mask を単一実行ファイル化し、Tauri が期待する場所と名前で配置する。

このスクリプトは Windows / macOS / Linux のどれでも同じように動きます。
CI (GitHub Actions) と、手元のビルドの両方から呼ばれます。

== なぜこのスクリプトがあるか ==

Tauri の `bundle.externalBin: ["binaries/jp-pii-mask-server"]` は
**`src-tauri/` からの相対パス**として解決されます。したがって配置先は

    desktop/src-tauri/binaries/jp-pii-mask-server-<target-triple>[.exe]

でなければなりません。`desktop/binaries/` ではありません。
(以前はここを間違えており、`cargo tauri build` が必ず失敗する状態でした。)

`<target-triple>` は `rustc -vV` の `host:` 行から取ります。

== 出力先の分離 ==

PyInstaller の作業ファイル (dist / build / spec) はすべて desktop/ 配下に置き、
リポジトリルートは読み取り専用の入力 (--paths) としてのみ参照します。

== 使い方 ==

    python desktop/scripts/build-sidecar.py
    python desktop/scripts/build-sidecar.py --skip-deps
    python desktop/scripts/build-sidecar.py --python python3.12
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
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

SIDECAR_NAME = "jp-pii-mask-server"

# PyInstaller に明示的に含めさせるモジュール。
# uvicorn は動的にモジュールを読み込むため、指定しないと実行時に落ちる。
HIDDEN_IMPORTS = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

# サイドカー本体が使う実行時依存 (pyproject.toml の dependencies と合わせること)。
RUNTIME_DEPS = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pydantic>=2.5",
    "cryptography>=42.0",
    "httpx>=0.27",
]


def container_of_copy() -> Path:
    """このスクリプトが置かれている desktop/ の親 (= リポジトリルート) を返す。"""
    # desktop/scripts/build-sidecar.py -> desktop -> repo root
    return Path(__file__).resolve().parent.parent.parent


def exe_suffix() -> str:
    return ".exe" if os.name == "nt" else ""


def detect_target_triple() -> tuple[str, bool]:
    """ターゲットトリプルを返す。第 2 要素は「rustc から取れたか」。

    rustc があればそれを正とする (これが唯一確実な方法)。
    無い場合はプラットフォームから推定し、呼び出し側で警告する。
    """
    rustc = which("rustc")
    if rustc:
        try:
            out = subprocess.run(
                [rustc, "-vV"], capture_output=True, text=True, check=False
            ).stdout
            for line in out.splitlines():
                if line.startswith("host:"):
                    return line.split(":", 1)[1].strip(), True
        except OSError:
            pass

    machine = platform.machine().lower()
    system = platform.system().lower()
    if system == "windows":
        return "x86_64-pc-windows-msvc", False
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "aarch64-apple-darwin", False
        return "x86_64-apple-darwin", False
    if machine in ("aarch64", "arm64"):
        return "aarch64-unknown-linux-gnu", False
    return "x86_64-unknown-linux-gnu", False


def run(cmd: list[str], **kwargs) -> None:
    """コマンドを実行し、失敗したら例外にする。出力はそのまま流す。

    実行ファイルは絶対パスに解決してから渡す。Windows の CreateProcess は
    親プロセスの PATH で実行ファイルを探すため、PATH を env で渡しても
    見つからないことがある。
    """
    cmd = resolve(cmd)
    print("  $ " + " ".join(cmd), flush=True)
    result = subprocess.run(cmd, check=False, **kwargs)
    if result.returncode != 0:
        raise SystemExit(f"コマンドが失敗しました (exit {result.returncode}): {' '.join(cmd)}")


def main() -> int:
    _make_streams_robust()
    parser = argparse.ArgumentParser(
        description="jp_pii_mask を単一実行ファイル化し、Tauri のサイドカーとして配置します。"
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="依存パッケージのインストールを省略します。",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="使用する Python (既定: このスクリプトを動かしている Python)。",
    )
    args = parser.parse_args()

    repo_root = container_of_copy()
    desktop_dir = repo_root / "desktop"
    entry_py = repo_root / "scripts" / "sidecar_entry.py"

    dist_dir = desktop_dir / "dist"
    build_dir = desktop_dir / "build"
    # Tauri は src-tauri からの相対でサイドカーを探す。
    bin_dir = desktop_dir / "src-tauri" / "binaries"

    print(f"リポジトリルート : {repo_root}")
    print(f"desktop         : {desktop_dir}")
    print(f"配置先          : {bin_dir}")
    print()

    if not entry_py.is_file():
        raise SystemExit(
            f"エントリポイントが見つかりません: {entry_py}\n"
            "  jp_pii_mask/cli.py を直接指定してはいけません "
            "(相対 import が解決できず失敗します)。"
        )

    # リポジトリ側に __pycache__ を作らせない。
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    # ------------------------------------------------------------------
    # 1) 依存パッケージ
    # ------------------------------------------------------------------
    if args.skip_deps:
        print("[1/4] 依存のインストールをスキップします (--skip-deps)")
    else:
        print("[1/4] 依存パッケージをインストールします...")
        run(
            [args.python, "-m", "pip", "install", "--upgrade", "pip", "pyinstaller", *RUNTIME_DEPS],
        )
    print()

    # ------------------------------------------------------------------
    # 2) PyInstaller で単一実行ファイル化
    # ------------------------------------------------------------------
    print("[2/4] PyInstaller で単一実行ファイルを作ります (数分かかります)...")
    pyi_cmd = [
        args.python,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        SIDECAR_NAME,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(build_dir),
        "--paths",
        str(repo_root),
        # パッケージ同梱のデータ (辞書・web/ など) を取りこぼさないため。
        "--collect-all",
        "jp_pii_mask",
    ]
    for hidden in HIDDEN_IMPORTS:
        pyi_cmd += ["--hidden-import", hidden]
    pyi_cmd += ["--console", str(entry_py)]
    run(pyi_cmd)

    built = dist_dir / f"{SIDECAR_NAME}{exe_suffix()}"
    if not built.is_file():
        raise SystemExit(f"生成物が見つかりません: {built}")
    built_mb = built.stat().st_size / (1024 * 1024)
    print(f"  生成: {built} ({built_mb:.1f} MB)")
    print()

    # ------------------------------------------------------------------
    # 3) ターゲットトリプルを決める
    # ------------------------------------------------------------------
    print("[3/4] ターゲットトリプルを確認します...")
    triple, from_rustc = detect_target_triple()
    if from_rustc:
        print(f"  rustc -vV から取得: {triple}")
    else:
        print(f"  警告: rustc が見つかりません。プラットフォームから {triple} と推定しました。")
        print("        異なる場合は rustup を導入して実行し直してください。")
    print()

    # ------------------------------------------------------------------
    # 4) Tauri が期待する名前でコピー
    # ------------------------------------------------------------------
    print("[4/4] Tauri が探す名前で配置します...")
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / f"{SIDECAR_NAME}-{triple}{exe_suffix()}"
    shutil.copy2(built, target)
    if os.name != "nt":
        os.chmod(target, 0o755)
    print(f"  配置: {target}")
    print()

    print("=" * 62)
    print("完了しました。")
    print(f"  サイドカー : {target}")
    print()
    print("次の手順:")
    print("  リポジトリにある「インストーラを作る.bat」(Windows) または")
    print("  「インストーラを作る.command」(macOS) を開いてください。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
