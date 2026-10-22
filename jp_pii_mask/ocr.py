"""OCR エンジンの薄いラッパー。

主経路: Windows 標準 OCR (Windows.Media.Ocr)
補助経路: Tesseract (任意。縦書き対応)

なぜ自前 OCR を同梱しないか
---------------------------
画像やスキャン PDF の文字をマスクするには OCR が必須だが、
OCR エンジンを同梱すると依存とモデルのサイズが激増し、
「コア依存 5 つ・監査容易」という本プロジェクトの方針を壊す。

そこで **OS 内蔵の OCR** を使う:

  - Windows: `winsdk` (Microsoft 公式の WinRT 投影) 経由で
    `Windows.Media.Ocr` を呼ぶ。追加バイナリ不要。日本語は
    言語パックに含まれる OCR をそのまま利用する。
  - macOS / Linux: Windows OCR は無い。Tesseract があれば使う。

**縦書き**は Windows 標準 OCR が読めない (実測)。Tesseract の
`jpn_vert` モデルは読めるため、インストールされていれば
縦書きを含むページの補助として使う:

  - `JP_PII_MASK_TESSERACT` に tesseract の実行ファイルを指定するか、
    PATH / 標準のインストール先にあれば検出する。
  - `JP_PII_MASK_TESSDATA` で tessdata の場所を指定できる (任意)。
  - 日本語データ (`jpn_vert` / `jpn`) が必要。無ければ使わない。

`winsdk` は任意依存 (`pip install "jp-pii-mask[ocr-win]"`)。
未インストールでも PDF のテキスト層がある文書はマスクできる。

設計上の注意:
  - 認識結果は「行 > 単語」の構造で返す。日本語の認識器は
    漢字を 1 文字ずつ単語として返すことが多いため、呼び出し側は
    文字単位の座標として扱える。
  - 巨大画像は OCR エンジンが扱いやすいサイズに縮小し、
    座標を元のスケールに戻して返す。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("jp_pii_mask.ocr")

__all__ = [
    "OcrLine",
    "OcrUnavailable",
    "OcrWord",
    "ocr_available",
    "ocr_image",
    "ocr_image_tesseract",
    "tesseract_available",
    "unavailable_reason",
]

# OCR エンジンに渡す画像の最大辺 (px)。これを超える場合は縮小する。
MAX_OCR_DIMENSION = 6000

# 認識言語。日本語の言語パックが無い場合は OCR 不可として扱う。
# 英語エンジンへのフォールバックはしない (日本語文書を誤読したまま
# 「OCR 成功」になり、黒塗り漏れを招くため)。
PREFERRED_TAGS: tuple[str, ...] = ("ja", "ja-JP")

OCR_MISSING_HINT = (
    "Windows 標準 OCR を利用できません。次を確認してください:\n"
    "  1. pip install \"jp-pii-mask[ocr-win]\" (winsdk の導入)\n"
    "  2. Windows の設定 > 時刻と言語 > 言語と地域 で日本語を追加し、\n"
    "     「オプションの言語機能」から OCR を有効にする\n"
    "  Windows 以外の OS では、Tesseract (日本語データ入り) があれば\n"
    "  そちらを使います (JP_PII_MASK_TESSERACT に実行ファイルを指定)。\n"
    "  縦書きは Tesseract の jpn_vert モデルでのみ読めます。\n"
    "  テキスト層のある PDF は --ocr off でもマスクできます。"
)


class OcrUnavailable(RuntimeError):
    """OCR エンジンが利用できないときの例外。"""


@dataclass(slots=True)
class OcrWord:
    """認識された 1 単語 (日本語では 1 文字のことが多い)。"""

    text: str
    left: float
    top: float
    right: float
    bottom: float


@dataclass(slots=True)
class OcrLine:
    """認識された 1 行。"""

    words: list[OcrWord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 利用可否
# ---------------------------------------------------------------------------
def _import_winsdk() -> Any:
    """winsdk を遅延 import する。未導入なら None。"""
    try:
        import winsdk  # noqa: F401
        from winsdk.windows.globalization import Language
        from winsdk.windows.graphics.imaging import (
            BitmapAlphaMode,
            BitmapPixelFormat,
            SoftwareBitmap,
        )
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.security.cryptography import CryptographicBuffer
    except Exception as exc:  # 未導入・想定外の壊れた環境の両方
        log.debug("winsdk を読み込めません: %s", exc)
        return None
    return {
        "Language": Language,
        "BitmapAlphaMode": BitmapAlphaMode,
        "BitmapPixelFormat": BitmapPixelFormat,
        "SoftwareBitmap": SoftwareBitmap,
        "OcrEngine": OcrEngine,
        "CryptographicBuffer": CryptographicBuffer,
    }


def _create_engine(mod: dict[str, Any]) -> Any:
    """日本語 OCR エンジンを作る。無ければ None。

    日本語の言語パックが無い環境でユーザーのプロファイル言語
    (英語など) にフォールバックすると、日本語文書を誤読したまま
    「OCR 成功」になる。日本語専用ツールのため厳密に日本語を要求する。
    """
    engine_cls = mod["OcrEngine"]
    language_cls = mod["Language"]
    for tag in PREFERRED_TAGS:
        with contextlib.suppress(Exception):
            engine = engine_cls.try_create_from_language(language_cls(tag))
            if engine is not None:
                return engine
    return None


def unavailable_reason() -> str:
    """使えない理由を返す (使える場合は空文字)。"""
    if sys.platform != "win32":
        return f"この OS ({sys.platform}) では OCR 未対応です。"
    mod = _import_winsdk()
    if mod is None:
        return "winsdk がインストールされていません。"
    try:
        if _create_engine(mod) is None:
            return "利用可能な OCR 言語が見つかりません (日本語の OCR を有効にしてください)。"
    except Exception as exc:
        return f"OCR エンジンの初期化に失敗しました: {exc}"
    return ""


def ocr_available() -> bool:
    """現在の環境で OCR を使えるか。"""
    return unavailable_reason() == ""


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def ocr_image(image: Any) -> list[OcrLine]:
    """PIL 画像を OCR にかけ、行と単語の座標を返す。

    座標は入力画像と同じピクセル座標系 (左上原点) で返す。
    利用できない場合は OcrUnavailable を送出する。
    """
    reason = unavailable_reason()
    if reason:
        raise OcrUnavailable(reason)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_ocr_async(image))

    # すでにイベントループが動いている呼び出し元 (将来の組み込み用) からは
    # 専用スレッドで実行する。asyncio.run の入れ子はできないため。
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(_ocr_async(image))
        except BaseException as exc:  # スレッドを越えて再送出する
            box["error"] = exc

    thread = threading.Thread(target=runner, name="jp-pii-mask-ocr", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


async def _ocr_async(image: Any) -> list[OcrLine]:
    mod = _import_winsdk()
    if mod is None:
        raise OcrUnavailable("winsdk がインストールされていません。")
    engine = _create_engine(mod)
    if engine is None:
        raise OcrUnavailable("利用可能な OCR 言語が見つかりません。")

    fitted, scale = _fit_for_ocr(image)
    bitmap = _to_software_bitmap(fitted, mod)
    result = await engine.recognize_async(bitmap)

    inv = 1.0 / scale
    lines: list[OcrLine] = []
    for line in result.lines:
        words: list[OcrWord] = []
        for word in line.words:
            rect = word.bounding_rect
            words.append(
                OcrWord(
                    text=word.text,
                    left=rect.x * inv,
                    top=rect.y * inv,
                    right=(rect.x + rect.width) * inv,
                    bottom=(rect.y + rect.height) * inv,
                )
            )
        if words:
            lines.append(OcrLine(words=words))
    return lines


def _fit_for_ocr(image: Any) -> tuple[Any, float]:
    """OCR エンジンに渡す画像を必要に応じて縮小する。

    戻り値: (画像, 元スケールへ戻すための倍率)
    """
    width, height = image.size
    longest = max(width, height)
    if longest <= MAX_OCR_DIMENSION:
        return image, 1.0
    scale = MAX_OCR_DIMENSION / float(longest)
    resized = image.resize(
        (max(1, int(width * scale)), max(1, int(height * scale))),
        resample=_lanczos(),
    )
    return resized, scale


def _lanczos() -> Any:
    from PIL import Image

    return Image.Resampling.LANCZOS


def _to_software_bitmap(image: Any, mod: dict[str, Any]) -> Any:
    """PIL 画像 (RGB/RGBA) を WinRT の SoftwareBitmap (BGRA8) に変換する。"""
    from PIL import Image

    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    red, green, blue, alpha = rgba.split()
    # PIL は RGBA の並びでメモリに書くため、(B, G, R, A) を
    # RGBA として結合すると BGRA のバイト列になる。
    bgra = Image.merge("RGBA", (blue, green, red, alpha))

    bitmap = mod["SoftwareBitmap"](
        mod["BitmapPixelFormat"].BGRA8,
        bgra.width,
        bgra.height,
        mod["BitmapAlphaMode"].PREMULTIPLIED,
    )
    buffer = mod["CryptographicBuffer"].create_from_byte_array(bgra.tobytes())
    bitmap.copy_from_buffer(buffer)
    return bitmap


# ---------------------------------------------------------------------------
# Tesseract (任意。縦書き対応)
# ---------------------------------------------------------------------------
def _no_window() -> int:
    """子プロセスのコンソールを出さない (Windows)。"""
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _tesseract_candidates() -> tuple[str, ...]:
    local = os.environ.get("LOCALAPPDATA")
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
    ]
    if local:
        candidates.insert(
            2, os.path.join(local, "Programs", "Tesseract-OCR", "tesseract.exe")
        )
    return tuple(candidates)


def tesseract_path() -> str | None:
    """Tesseract の実行ファイルを探す (env > PATH > 標準の場所)。"""
    env = os.environ.get("JP_PII_MASK_TESSERACT", "").strip()
    if env:
        path = Path(env)
        if path.is_dir():
            path = path / ("tesseract.exe" if os.name == "nt" else "tesseract")
        if path.is_file():
            return str(path)
        log.debug("JP_PII_MASK_TESSERACT が指すファイルがありません: %s", env)
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in _tesseract_candidates():
        if Path(candidate).is_file():
            return candidate
    return None


_LANG_CACHE: dict[tuple[str, str], frozenset[str]] = {}


def _tessdata_dir() -> str:
    return os.environ.get("JP_PII_MASK_TESSDATA", "").strip()


def _tesseract_langs(exe: str) -> frozenset[str]:
    """利用可能な言語データ名を返す (結果はプロセス内でキャッシュ)。"""
    key = (exe, _tessdata_dir())
    if key in _LANG_CACHE:
        return _LANG_CACHE[key]

    cmd = [exe, "--list-langs"]
    if key[1]:
        cmd += ["--tessdata-dir", key[1]]
    langs: frozenset[str] = frozenset()
    try:
        proc = subprocess.run(  # noqa: S603 - 実行パスは候補列挙で検証済み
            cmd,
            capture_output=True,
            timeout=30,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("tesseract --list-langs に失敗: %s", exc)
    else:
        if proc.returncode == 0:
            # Tesseract の出力は UTF-8。ロケール (cp932) で解釈しない。
            stdout = proc.stdout.decode("utf-8", "replace")
            names = []
            for line in stdout.splitlines():
                name = line.strip()
                if not name or name.lower().startswith("list of available"):
                    continue
                names.append(name)
            langs = frozenset(names)
        else:
            log.debug(
                "tesseract --list-langs rc=%s: %s",
                proc.returncode,
                proc.stderr.decode("utf-8", "replace"),
            )
    _LANG_CACHE[key] = langs
    return langs


def tesseract_unavailable_reason() -> str:
    """Tesseract が使えない理由 (使える場合は空文字)。"""
    exe = tesseract_path()
    if exe is None:
        return "Tesseract が見つかりません (JP_PII_MASK_TESSERACT で指定できます)。"
    if not (_tesseract_langs(exe) & {"jpn", "jpn_vert"}):
        return "Tesseract に日本語データ (jpn / jpn_vert) がありません。"
    return ""


def tesseract_available() -> bool:
    """Tesseract + 日本語データで OCR できるか。"""
    return tesseract_unavailable_reason() == ""


def _parse_tsv(tsv: str, scale: float) -> list[OcrLine]:
    """Tesseract の TSV 出力を OcrLine のリストにする。"""
    header: list[str] | None = None
    grouped: dict[tuple[str, str, str], list[OcrWord]] = {}
    order: list[tuple[str, str, str]] = []
    inv = 1.0 / scale if scale else 1.0

    for line in tsv.splitlines():
        if header is None:
            header = line.split("\t")
            continue
        cols = line.split("\t")
        if len(cols) != len(header):
            continue
        row = dict(zip(header, cols, strict=True))
        if row.get("level") != "5":
            continue
        text = row.get("text", "").strip()
        if not text:
            continue
        try:
            left = float(row["left"])
            top = float(row["top"])
            width = float(row["width"])
            height = float(row["height"])
        except (KeyError, ValueError):
            continue
        key = (row["block_num"], row["par_num"], row["line_num"])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(
            OcrWord(
                text=text,
                left=left * inv,
                top=top * inv,
                right=(left + width) * inv,
                bottom=(top + height) * inv,
            )
        )
    return [OcrLine(words=grouped[key]) for key in order]


def ocr_image_tesseract(
    image: Any, *, vertical: bool | None = None
) -> list[OcrLine]:
    """PIL 画像を Tesseract で OCR する。

    vertical:
        True  -> jpn_vert (縦書き) のみ
        False -> jpn (横書き) のみ
        None  -> 縦書きを優先し、空なら横書きも試す

    文字が認識できなければ空リストを返す。
    """
    reason = tesseract_unavailable_reason()
    if reason:
        raise OcrUnavailable(reason)

    exe = tesseract_path()
    assert exe is not None
    langs = _tesseract_langs(exe)
    attempts: list[tuple[str, str]] = []
    if vertical is not False and "jpn_vert" in langs:
        attempts.append(("jpn_vert", "5"))
    if vertical is not True and "jpn" in langs:
        attempts.append(("jpn", "6"))
    if not attempts:
        attempts.append(("jpn_vert" if "jpn_vert" in langs else "jpn", "5"))

    fitted, scale = _fit_for_ocr(image)
    fd, tmp_name = tempfile.mkstemp(suffix=".png", prefix="jp-pii-mask-ocr-")
    os.close(fd)
    try:
        fitted.save(tmp_name)
        for lang, psm in attempts:
            cmd = [exe, tmp_name, "stdout"]
            if _tessdata_dir():
                cmd += ["--tessdata-dir", _tessdata_dir()]
            cmd += ["-l", lang, "--psm", psm, "--dpi", "300", "tsv"]
            try:
                proc = subprocess.run(  # noqa: S603 - 実行パスは検証済み
                    cmd,
                    capture_output=True,
                    timeout=180,
                    creationflags=_no_window(),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise OcrUnavailable(f"Tesseract の実行に失敗しました: {exc}") from exc
            if proc.returncode != 0:
                log.debug(
                    "tesseract (%s) rc=%s: %s",
                    lang,
                    proc.returncode,
                    proc.stderr.decode("utf-8", "replace"),
                )
                continue
            stdout = proc.stdout.decode("utf-8", "replace")
            lines = _parse_tsv(stdout, scale)
            if lines:
                return lines
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
    return []
