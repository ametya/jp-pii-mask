"""Windows 標準 OCR (Windows.Media.Ocr) の薄いラッパー。

なぜ自前 OCR を同梱しないか
---------------------------
画像やスキャン PDF の文字をマスクするには OCR が必須だが、
OCR エンジンを同梱すると依存とモデルのサイズが激増し、
「コア依存 5 つ・監査容易」という本プロジェクトの方針を壊す。

そこで **OS 内蔵の OCR** を使う:

  - Windows: `winsdk` (Microsoft 公式の WinRT 投影) 経由で
    `Windows.Media.Ocr` を呼ぶ。追加バイナリ不要。日本語は
    言語パックに含まれる OCR をそのまま利用する。
  - macOS / Linux: 現状未対応 (呼び出し側が明確なエラーを出す)。

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
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("jp_pii_mask.ocr")

__all__ = [
    "OcrLine",
    "OcrUnavailable",
    "OcrWord",
    "ocr_available",
    "ocr_image",
    "unavailable_reason",
]

# OCR エンジンに渡す画像の最大辺 (px)。これを超える場合は縮小する。
MAX_OCR_DIMENSION = 6000

# 優先する認識言語。日本語の言語パックが無い場合は
# ユーザーのプロファイル言語へフォールバックする。
PREFERRED_TAGS: tuple[str, ...] = ("ja", "ja-JP")

OCR_MISSING_HINT = (
    "Windows 標準 OCR を利用できません。次を確認してください:\n"
    "  1. pip install \"jp-pii-mask[ocr-win]\" (winsdk の導入)\n"
    "  2. Windows の設定 > 時刻と言語 > 言語と地域 で日本語を追加し、\n"
    "     「オプションの言語機能」から OCR を有効にする\n"
    "  Windows 以外の OS では現在 OCR 未対応です。\n"
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
    """日本語優先で OCR エンジンを作る。無ければ None。"""
    engine_cls = mod["OcrEngine"]
    language_cls = mod["Language"]
    for tag in PREFERRED_TAGS:
        with contextlib.suppress(Exception):
            engine = engine_cls.try_create_from_language(language_cls(tag))
            if engine is not None:
                return engine
    with contextlib.suppress(Exception):
        return engine_cls.try_create_from_user_profile_languages()
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
