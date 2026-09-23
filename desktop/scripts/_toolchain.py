#!/usr/bin/env python3
"""ツールチェーン (cargo / rustc など) を確実に呼び出すための共通処理。

== なぜ必要か (Windows で実際に踏んだ罠) ==

rustup を `--no-modify-path` で入れると、`~/.cargo/bin` は PATH に入りません。
そこで `subprocess.run(["cargo", "check"], env=env)` のように env で PATH を
足して渡しても、**Windows では実行ファイルが見つかりません**。

理由: Windows の `CreateProcess` は、`env` 引数で渡した PATH ではなく
「呼び出し元プロセスの PATH」で実行ファイルを探すためです
(Linux / macOS の execvp は渡した env の PATH を使うので、この差は
 Windows でだけ表面化します)。

実測 (このプロジェクトの Windows 環境):

    env=None (親 PATH に cargo なし)          -> FileNotFoundError
    env に PATH を前置して渡す                -> FileNotFoundError  ← 罠
    親 os.environ["PATH"] を書き換えて env=None -> OK
    絶対パス + env=PATH前置                   -> OK

したがって、**コマンドは事前に絶対パスへ解決してから渡す**必要があります。
`resolve()` がそれを行います。
"""

from __future__ import annotations

import os
import shutil

CARGO_BIN_DIR = __import__("pathlib").Path.home() / ".cargo" / "bin"


def augmented_path() -> str:
    """`~/.cargo/bin` を先頭に足した PATH 文字列を返す (既にあればそのまま)。"""
    current = os.environ.get("PATH", "")
    entry = str(CARGO_BIN_DIR)
    if entry.lower() in current.lower():
        return current
    return entry + os.pathsep + current


def child_env() -> dict[str, str]:
    """子プロセスに渡す環境変数。PATH に `~/.cargo/bin` を含める。"""
    env = dict(os.environ)
    env["PATH"] = augmented_path()
    return env


def resolve(cmd: list[str]) -> list[str]:
    """`cmd[0]` を絶対パスに解決する (見つからなければそのまま返す)。

    絶対パスで渡せば、Windows でも親 PATH に依存せず確実に起動できる。
    """
    if not cmd:
        return cmd
    exe = cmd[0]
    if os.path.isabs(exe) or os.sep in exe or (os.altsep and os.altsep in exe):
        return cmd
    found = shutil.which(exe, path=augmented_path())
    if found:
        return [found, *cmd[1:]]
    return cmd


def which(tool: str) -> str | None:
    """`~/.cargo/bin` も含めて実行ファイルを探す。"""
    return shutil.which(tool, path=augmented_path())
