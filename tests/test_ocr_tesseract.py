"""Tesseract (任意) による縦書き OCR のテスト。

Windows 標準 OCR は縦書きを読めない (実測)。Tesseract の jpn_vert
モデルがあれば読めるため、その経路を検証する。

Tesseract が無い環境 (CI など) では全テストを skip する。
実行するには次のどちらか:

    JP_PII_MASK_TESSERACT=C:\\path\\to\\tesseract.exe python -m pytest tests/test_ocr_tesseract.py
    tesseract を PATH に追加する
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image, ImageDraw, ImageFont

from jp_pii_mask.detect import Detector
from jp_pii_mask.files import mask_file
from jp_pii_mask.ocr import (
    OcrLine,
    OcrWord,
    ocr_image_tesseract,
    tesseract_available,
)
from jp_pii_mask.vault import Vault

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]

PHONE = "09012345678"

requires_tesseract = pytest.mark.skipif(
    not tesseract_available(),
    reason="Tesseract (日本語データ入り) が使えない環境",
)


def _japanese_font(size: int = 40) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    pytest.skip("日本語フォントが見つかりません")


def _vertical_image() -> Image.Image:
    """縦書き (文字は正立、右から左へ) のテスト画像を作る。"""
    font = _japanese_font(40)
    image = Image.new("RGB", (700, 1000), "white")
    draw = ImageDraw.Draw(image)

    def column(text: str, x: int, top: int = 40) -> None:
        y = top
        for ch in text:
            draw.text((x, y), ch, fill="black", font=font)
            y += 48

    column("電話番号", 600)
    column(PHONE, 540)
    column("田中太郎", 420)
    return image


@requires_tesseract
class TestTesseractVertical:
    def test_reads_vertical_text(self):
        lines = ocr_image_tesseract(_vertical_image(), vertical=True)
        text = "".join(word.text for line in lines for word in line.words)
        assert PHONE in text
        # モデルの精度上、名前の最後の 1 文字は崩れることがある。
        assert "田中" in text

    def test_vertical_image_is_masked(self, tmp_path):
        src = tmp_path / "vertical.png"
        _vertical_image().save(src)

        result = mask_file(
            src,
            None,
            detector=Detector(min_confidence=0.5),
            vault=Vault(store_path=tmp_path / "out.masked.vault", encrypt=True),
            ocr="auto",
        )

        assert result.entities >= 1
        masked_plain = result.text_path.read_text(encoding="utf-8")
        assert PHONE not in masked_plain
        assert "[電話番号_1]" in masked_plain

    def test_falls_back_when_primary_ocr_reads_garbage(self, tmp_path, monkeypatch):
        # Windows OCR は縦書きを空文字ではなく文字化けで返すことがある。
        # 個人情報が 0 件なら Tesseract で読み直す経路を検証する。
        src = tmp_path / "vertical.png"
        _vertical_image().save(src)
        garbage = [
            OcrLine(
                words=[
                    OcrWord(
                        text="0C)01っ乁E一わC.C",
                        left=0.0,
                        top=0.0,
                        right=10.0,
                        bottom=10.0,
                    )
                ]
            )
        ]
        monkeypatch.setattr("jp_pii_mask.files.ocr_image", lambda image: garbage)

        result = mask_file(
            src,
            None,
            detector=Detector(min_confidence=0.5),
            vault=Vault(store_path=tmp_path / "out.masked.vault", encrypt=True),
            ocr="auto",
        )

        masked_plain = result.text_path.read_text(encoding="utf-8")
        assert result.entities >= 1
        assert PHONE not in masked_plain
        assert "[電話番号_1]" in masked_plain
