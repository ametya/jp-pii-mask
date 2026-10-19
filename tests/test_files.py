"""PDF・画像ファイルのマスク (`file` 機能) のテスト。

方針:
  - PDF のテキスト層ルートは合成 PDF (ASCII/Helvetica) で検証する。
    日本語の座標検証にはフォント埋め込みが必要で、環境依存を避けるため
    日本語側は OCR ルートのテストでカバーする。
  - OCR が必要なテストは、Windows + winsdk + 日本語 OCR が
    利用できる環境でのみ実行する (それ以外は skip)。
  - 「マスクしたのに元の個人情報が取り出せる」が一番まずいので、
    出力 PDF のテキスト層に PII が残っていないことを必ず確かめる。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")
pytest.importorskip("PIL")

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont, ImageStat

from jp_pii_mask.cli import main
from jp_pii_mask.detect import Detector, Entity
from jp_pii_mask.files import (
    Box,
    FileMaskError,
    Span,
    entity_rects,
    mask_file,
    merge_boxes,
    output_path_for,
    restore_file,
)
from jp_pii_mask.ocr import ocr_available, ocr_image
from jp_pii_mask.vault import Vault

PHONE = "090-1234-5678"
EMAIL = "taro.tanaka@example.co.jp"
CARD = "4242 4242 4242 4242"

JAPANESE_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Supplemental/ヒラギノ角ゴシック W3.ttc",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _japanese_font(size: int = 36) -> ImageFont.FreeTypeFont:
    for path in JAPANESE_FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    pytest.skip("日本語フォントが見つかりません")


def _build_pdf(pages: list[list[str]], rotation: int = 0) -> bytes:
    """最小構成の PDF (ASCII / Helvetica) を組み立てる。

    rotation: /Rotate の値 (0/90/180/270)。回転つきページの座標変換検証用。
    """
    n = len(pages)
    font_id = 3 + 2 * n
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
            + f"] /Count {n} >>"
        ).encode(),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, lines in enumerate(pages):
        page_id = 3 + 2 * i
        content_id = page_id + 1
        stream = (
            "BT /F1 24 Tf 72 720 Td (" + ") Tj T* (".join(lines) + ") Tj ET"
        ).encode("latin-1")
        rotate = f" /Rotate {rotation}" if rotation else ""
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]{rotate} "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode()
        objects[content_id] = (
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for oid in sorted(objects):
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n".encode() + objects[oid] + b"\nendobj\n"
    xref = len(out)
    size = font_id + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for oid in range(1, size):
        out += f"{offsets.get(oid, 0):010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _write_pii_pdf(path: Path) -> None:
    path.write_bytes(
        _build_pdf(
            [
                [
                    f"Phone: {PHONE}",
                    f"Mail: {EMAIL}",
                    "Name: Taro Tanaka",
                    f"Card: {CARD}",
                ]
            ]
        )
    )


def _page_text(path: Path, index: int = 0) -> str:
    document = pdfium.PdfDocument(str(path))
    try:
        page = document[index]
        text_page = page.get_textpage()
        try:
            return text_page.get_text_range()
        finally:
            text_page.close()
    finally:
        document.close()


def _dark_pixels(path: Path, index: int = 0) -> int:
    """描画したページの暗い画素の数 (黒塗りの有無を雑に確認する)。"""
    document = pdfium.PdfDocument(str(path))
    try:
        bitmap = document[index].render(scale=1.0)
        try:
            image = bitmap.to_pil().convert("L")
        finally:
            bitmap.close()
    finally:
        document.close()
    return sum(image.histogram()[:60])


def _render_page_image(path: Path, index: int = 0, scale: float = 2.0) -> Image.Image:
    """ページを指定倍率で描画してグレースケール画像にする。"""
    document = pdfium.PdfDocument(str(path))
    try:
        bitmap = document[index].render(scale=scale)
        try:
            return bitmap.to_pil().convert("L")
        finally:
            bitmap.close()
    finally:
        document.close()


def _make_detector() -> Detector:
    return Detector(min_confidence=0.5)


def _make_vault(tmp_path: Path, name: str = "out") -> Vault:
    return Vault(store_path=tmp_path / f"{name}.masked.vault", encrypt=True)


requires_ocr = pytest.mark.skipif(
    not ocr_available(), reason="Windows 標準 OCR (winsdk) が使えない環境"
)


class TestBoxMath:
    def test_merge_same_line(self):
        boxes = [Box(0, 0, 10, 10), Box(12, 1, 22, 11), Box(0, 30, 10, 40)]
        merged = merge_boxes(boxes)
        assert len(merged) == 2
        same_line = min(merged, key=lambda b: b.top)
        assert (same_line.left, same_line.right) == (0, 22)

    def test_merge_wrapped_entity_keeps_two_lines(self):
        boxes = [Box(0, 0, 100, 10), Box(0, 12, 80, 22)]
        assert len(merge_boxes(boxes)) == 2

    def test_entity_rects_uses_overlapping_spans_only(self):
        spans = [
            Span(0, 4, Box(0, 0, 40, 10)),
            Span(4, 8, Box(40, 0, 80, 10)),
            Span(8, 12, Box(200, 50, 240, 60)),
        ]
        entity = Entity("PERSON", 4, 8, "abcd", 0.9)
        rects = entity_rects(entity, spans, pad=0)
        assert len(rects) == 1
        assert (rects[0].left, rects[0].right) == (40, 80)

    def test_output_path_for_adds_extension(self, tmp_path):
        src = tmp_path / "doc.pdf"
        assert output_path_for(src, None).name == "doc.masked.pdf"
        assert output_path_for(src, tmp_path / "custom").name == "custom.pdf"


class TestPdfTextLayer:
    def test_masks_pii_and_flattens_masked_page(self, tmp_path):
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        original_text = _page_text(src)

        vault = _make_vault(tmp_path)
        result = mask_file(
            src,
            None,
            detector=_make_detector(),
            vault=vault,
            ocr="off",
        )

        assert result.entities >= 3
        assert result.pages_masked == 1
        assert result.masked_path.exists()
        assert result.text_path is not None and result.text_path.exists()
        assert result.vault_path is not None and result.vault_path.exists()

        # 出力 PDF のテキスト層から PII が取り出せないこと (画像化されている)
        masked_text = _page_text(result.masked_path)
        assert PHONE not in masked_text
        assert EMAIL not in masked_text
        assert "4242" not in masked_text

        # 黒塗りが実際に描かれていること
        assert _dark_pixels(result.masked_path) > _dark_pixels(src)

        # テキスト出力はプレースホルダ入りで、復元すると原文に戻ること
        masked_plain = result.text_path.read_text(encoding="utf-8")
        assert PHONE not in masked_plain
        assert "[電話番号_1]" in masked_plain

        # 対応表ファイルに平文の PII が無いこと
        blob = result.vault_path.read_bytes()
        assert PHONE.encode("utf-8") not in blob
        assert EMAIL.encode("utf-8") not in blob

        restored = restore_file(result.text_path, result.vault_path)
        assert restored.restored >= 3
        assert len(restored.missing) == 0
        assert PHONE in restored.text
        assert EMAIL in restored.text
        assert original_text.split() == restored.text.split()

    def test_clean_pages_keep_text_layer(self, tmp_path):
        src = tmp_path / "mixed.pdf"
        src.write_bytes(
            _build_pdf(
                [
                    [f"Phone: {PHONE}"],
                    ["Meeting notes for project alpha"],
                ]
            )
        )

        result = mask_file(
            src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="off"
        )

        assert result.pages == 2
        assert result.pages_masked == 1
        # 1 ページ目はマスクされ画像化 -> テキスト層なし
        assert PHONE not in _page_text(result.masked_path, 0)
        # 2 ページ目は無傷のベクタページ -> テキスト層が残る
        assert "Meeting notes" in _page_text(result.masked_path, 1)

    def test_image_only_pdf_with_ocr_off_warns_but_copies(self, tmp_path):
        image = Image.new("RGB", (600, 200), "white")
        draw = ImageDraw.Draw(image)
        draw.text((20, 80), "090-1234-5678", fill="black", font=_japanese_font(24))
        src = tmp_path / "scan.pdf"
        image.save(src, format="PDF", resolution=150)

        result = mask_file(
            src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="off"
        )

        assert result.entities == 0
        assert result.warnings
        assert result.pages == 1
        assert _page_text(result.masked_path, 0).strip() == ""


class TestRotatedPages:
    """`/Rotate` 付きページで黒塗りが正しい位置に描かれること。

    実測で、回転ページに単純な (left, page_height - top) 変換を使うと
    黒塗りが別の場所に描かれるバグがあった (tests で回帰を防ぐ)。
    """

    @pytest.mark.parametrize("rotation", [90, 180, 270])
    def test_rotated_pdf_masks_and_empties_text_layer(self, tmp_path, rotation):
        src = tmp_path / f"rot{rotation}.pdf"
        src.write_bytes(_build_pdf([[f"Phone: {PHONE}"]], rotation=rotation))

        result = mask_file(
            src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="off"
        )

        assert result.entities >= 1
        assert result.pages_masked == 1
        assert PHONE not in _page_text(result.masked_path, 0)
        masked_plain = result.text_path.read_text(encoding="utf-8")
        assert "[電話番号_1]" in masked_plain
        restored = restore_file(result.text_path, result.vault_path)
        assert PHONE in restored.text


@requires_ocr
class TestRotatedPagesOcr:
    @pytest.mark.parametrize("rotation", [90, 180, 270])
    def test_black_box_actually_covers_digits(self, tmp_path, rotation):
        src = tmp_path / f"rot{rotation}.pdf"
        src.write_bytes(_build_pdf([[f"Phone: {PHONE}"]], rotation=rotation))

        original = _render_page_image(src)
        digit_words = [
            word
            for line in ocr_image(original)
            for word in line.words
            if re.fullmatch(r"[0-9\-]+", word.text) and len(word.text) >= 3
        ]
        assert digit_words, "検証用に OCR で電話番号を特定できませんでした"

        result = mask_file(
            src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="off"
        )
        masked = _render_page_image(result.masked_path)

        for word in digit_words:
            box = (
                int(word.left),
                int(word.top),
                int(word.right) + 1,
                int(word.bottom) + 1,
            )
            dark_ratio = sum(masked.crop(box).histogram()[:60]) / (
                (box[2] - box[0]) * (box[3] - box[1])
            )
            assert dark_ratio > 0.9, (
                f"回転 {rotation} で数字 {word.text!r} が黒塗りされていない "
                f"(dark_ratio={dark_ratio:.2f})"
            )


class TestErrors:
    def test_missing_file(self, tmp_path):
        with pytest.raises(FileMaskError, match="見つかりません"):
            mask_file(
                tmp_path / "nope.pdf",
                None,
                detector=_make_detector(),
                vault=_make_vault(tmp_path),
            )

    def test_unsupported_extension(self, tmp_path):
        src = tmp_path / "data.xyz"
        src.write_text("hello", encoding="utf-8")
        with pytest.raises(FileMaskError, match="対応していない"):
            mask_file(src, None, detector=_make_detector(), vault=_make_vault(tmp_path))

    def test_same_input_and_output(self, tmp_path):
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        with pytest.raises(FileMaskError, match="同じファイル"):
            mask_file(
                src,
                src,
                detector=_make_detector(),
                vault=_make_vault(tmp_path),
                ocr="off",
            )

    def test_image_with_ocr_off(self, tmp_path):
        src = tmp_path / "photo.png"
        Image.new("RGB", (100, 100), "white").save(src)
        with pytest.raises(FileMaskError, match="OCR"):
            mask_file(
                src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="off"
            )

    def test_restore_with_missing_vault(self, tmp_path):
        text = tmp_path / "a.masked.txt"
        text.write_text("[電話番号_1]", encoding="utf-8")
        with pytest.raises(FileMaskError, match="対応表が見つかりません"):
            restore_file(text, tmp_path / "nope.vault")


@requires_ocr
class TestOcrPaths:
    def test_image_masks_japanese_pii(self, tmp_path):
        font = _japanese_font(36)
        image = Image.new("RGB", (1100, 360), "white")
        draw = ImageDraw.Draw(image)
        draw.text((30, 30), "田中太郎です。", fill="black", font=font)
        draw.text((30, 110), "携帯は090-1234-5678です。", fill="black", font=font)
        draw.text(
            (30, 190), "メールは taro@example.co.jp です。", fill="black", font=font
        )
        src = tmp_path / "scan.png"
        image.save(src)

        # 検証用に、原文画像での電話番号の位置を OCR から得ておく
        phone_boxes = [
            word
            for line in ocr_image(image)
            for word in line.words
            if "090" in word.text
        ]

        result = mask_file(
            src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="auto"
        )

        assert result.entities >= 2
        assert result.ocr_pages == 1
        masked_plain = result.text_path.read_text(encoding="utf-8")
        assert "[電話番号_1]" in masked_plain
        assert PHONE not in masked_plain

        # 電話番号の位置が黒く塗られていること
        assert phone_boxes
        original = image.convert("L")
        masked = Image.open(result.masked_path).convert("L")
        for word in phone_boxes:
            box = (int(word.left), int(word.top), int(word.right) + 1, int(word.bottom) + 1)
            assert ImageStat.Stat(original.crop(box)).mean[0] > 100
            assert ImageStat.Stat(masked.crop(box)).mean[0] < 60

        restored = restore_file(result.text_path, result.vault_path)
        assert PHONE in restored.text
        assert len(restored.missing) == 0

    def test_scanned_pdf_uses_ocr(self, tmp_path):
        font = _japanese_font(32)
        image = Image.new("RGB", (1000, 240), "white")
        draw = ImageDraw.Draw(image)
        draw.text((30, 40), "お客様番号は 090-1234-5678 です。", fill="black", font=font)
        draw.text((30, 130), "株式会社サンプル商事", fill="black", font=font)
        src = tmp_path / "scanned.pdf"
        image.save(src, format="PDF", resolution=150)

        result = mask_file(
            src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="auto"
        )

        assert result.ocr_pages == 1
        assert result.entities >= 1
        masked_plain = result.text_path.read_text(encoding="utf-8")
        assert "[電話番号_1]" in masked_plain
        # スキャン PDF なので元々テキスト層は無いが、念のため
        assert PHONE not in _page_text(result.masked_path, 0)

    def test_image_without_text_is_copied_with_warning(self, tmp_path):
        src = tmp_path / "blank.png"
        Image.new("RGB", (400, 200), "white").save(src)
        result = mask_file(
            src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="auto"
        )
        assert result.entities == 0
        assert result.warnings
        assert src.read_bytes() == result.masked_path.read_bytes()


class TestCli:
    @pytest.fixture(autouse=True)
    def _isolate_home(self, tmp_path, monkeypatch):
        # 鍵はユーザー領域 (JP_PII_MASK_HOME) に作られるため、
        # テストが実ユーザーの設定ディレクトリを汚さないようにする。
        monkeypatch.setenv("JP_PII_MASK_HOME", str(tmp_path / "home"))

    def test_file_mask_and_restore_via_cli(self, tmp_path, capsys):
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        config = str(tmp_path / "config.json")

        rc = main(
            ["--config", config, "file", "mask", str(src), "--ocr", "off", "--json"]
        )
        out = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(out)
        assert payload["entities"] >= 3
        masked = Path(payload["masked"])
        assert masked.exists()

        rc = main(
            ["--config", config, "file", "restore", payload["text"], "--vault", payload["vault"]]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert PHONE in out
        assert EMAIL in out

    def test_file_restore_to_output_file(self, tmp_path, capsys):
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        config = str(tmp_path / "config.json")
        main(["--config", config, "file", "mask", str(src), "--ocr", "off"])

        out_file = tmp_path / "restored.txt"
        rc = main(
            [
                "--config",
                config,
                "file",
                "restore",
                str(tmp_path / "invoice.masked.txt"),
                "--vault",
                str(tmp_path / "invoice.masked.vault"),
                "-o",
                str(out_file),
            ]
        )
        assert rc == 0
        assert PHONE in out_file.read_text(encoding="utf-8")

    def test_file_mask_reports_error_for_bad_input(self, tmp_path, capsys):
        config = str(tmp_path / "config.json")
        rc = main(["--config", config, "file", "mask", str(tmp_path / "none.pdf")])
        assert rc == 2
        assert "エラー" in capsys.readouterr().err

    def test_mask_key_lives_in_user_dir_not_next_to_output(self, tmp_path, capsys):
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        config = str(tmp_path / "config.json")
        rc = main(["--config", config, "file", "mask", str(src), "--ocr", "off", "--json"])
        assert rc == 0
        capsys.readouterr()
        # 出力先フォルダに鍵が無く、ユーザー領域に作られていること
        assert not (tmp_path / "key.bin").exists()
        assert (tmp_path / "home" / "file-key.bin").is_file()

    def test_audit_log_written_on_mask_and_restore(self, tmp_path, capsys):
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        config = str(tmp_path / "config.json")
        rc = main(["--config", config, "file", "mask", str(src), "--ocr", "off", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)

        rc = main(
            [
                "--config", config, "file", "restore",
                payload["text"], "--vault", payload["vault"], "--json",
            ]
        )
        assert rc == 0
        capsys.readouterr()

        audit_path = tmp_path / "home" / "audit.log"
        assert audit_path.is_file()
        entries = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        mask_events = [e for e in entries if e["event"] == "file.mask"]
        assert len(mask_events) == 1
        assert mask_events[0]["entities"] == payload["entities"]
        assert any(e["event"] == "file.restore" for e in entries)
        # ファイル名・パスは監査ログに残さない
        assert "invoice" not in json.dumps(entries, ensure_ascii=False)

    def test_audit_command_shows_entries(self, tmp_path, capsys):
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        config = str(tmp_path / "config.json")
        assert main(["--config", config, "file", "mask", str(src), "--ocr", "off"]) == 0
        capsys.readouterr()

        rc = main(["--config", config, "audit", "--last", "5", "--json"])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["entries"][0]["event"] == "file.mask"

        rc = main(["--config", config, "audit"])
        assert rc == 0
        assert "file.mask" in capsys.readouterr().out

    def test_mask_no_vault_writes_no_key(self, tmp_path, capsys):
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        config = str(tmp_path / "config.json")
        rc = main(
            ["--config", config, "file", "mask", str(src), "--ocr", "off", "--no-vault", "--json"]
        )
        assert rc == 0
        capsys.readouterr()
        assert not (tmp_path / "home" / "file-key.bin").exists()


class TestKeySeparation:
    def test_legacy_key_next_to_vault_is_preferred(self, tmp_path, monkeypatch):
        # 旧バージョンの出力 (key.bin が対応表の隣) を優先して使うこと。
        # ユーザー領域に無関係な鍵があっても復元できる必要がある。
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        result = mask_file(
            src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="off"
        )
        home = tmp_path / "home"
        home.mkdir()
        (home / "file-key.bin").write_bytes(os.urandom(32))
        monkeypatch.setenv("JP_PII_MASK_HOME", str(home))

        restored = restore_file(result.text_path, result.vault_path)
        assert restored.missing == []
        assert PHONE in restored.text

    def test_restore_without_any_key_fails_clearly(self, tmp_path, monkeypatch):
        src = tmp_path / "invoice.pdf"
        _write_pii_pdf(src)
        result = mask_file(
            src, None, detector=_make_detector(), vault=_make_vault(tmp_path), ocr="off"
        )
        # 対応表だけを別フォルダに移し、鍵 (key.bin) は元の場所に残す
        moved = tmp_path / "moved"
        moved.mkdir()
        moved_vault = moved / result.vault_path.name
        moved_text = moved / result.text_path.name
        shutil.copy(result.vault_path, moved_vault)
        shutil.copy(result.text_path, moved_text)
        monkeypatch.setenv("JP_PII_MASK_HOME", str(tmp_path / "empty-home"))

        with pytest.raises(FileMaskError) as excinfo:
            restore_file(moved_text, moved_vault)
        assert "file-key.bin" in str(excinfo.value)

    def test_raw_legacy_key_file_still_loads(self, tmp_path):
        # DPAPI 導入前に作られた生の 32 バイト鍵を上書きしないこと
        key_path = tmp_path / "key.bin"
        key = os.urandom(32)
        key_path.write_bytes(key)
        Vault(store_path=tmp_path / "out.vault", encrypt=True, key_path=key_path)
        assert key_path.read_bytes() == key

    @pytest.mark.skipif(os.name != "nt", reason="DPAPI は Windows 専用")
    def test_key_file_is_dpapi_protected(self, tmp_path):
        key_path = tmp_path / "file-key.bin"
        Vault(store_path=tmp_path / "out.vault", encrypt=True, key_path=key_path)
        blob = key_path.read_bytes()
        assert blob.startswith(b"JPPMK1\x00")
        assert len(blob) != 32
        # 同じユーザーなら新しい Vault からも読める (DPAPI 復号の往復)
        Vault(store_path=tmp_path / "out.vault", encrypt=True, key_path=key_path)
