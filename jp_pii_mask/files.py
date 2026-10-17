"""PDF・画像ファイル内の個人情報をローカルでマスクする。

プロキシ (proxy.py) が JSON API の本文を守るのに対し、こちらは
**手元のファイル** を守る。用途は「スキャンした請求書や本人確認書類を
AI に読ませたいが、個人情報は渡したくない」といったケース。

対応と出力
----------

入力:

  - PDF (テキスト層あり) — 文字単位の座標を取り、検出位置を黒塗り
  - PDF (スキャン / 画像のみ) — ページを画像化し、Windows OCR で
    文字を認識してから黒塗り
  - 画像 (PNG / JPEG / BMP / WebP / TIFF) — OCR して黒塗り

出力 (入力と同じフォルダ):

  1. `<name>.masked.pdf` / `<name>.masked.png` … — マスク済みファイル
     個人情報のあったページは画像化されるため、**テキスト層から
     個人情報をコピーで取り出せない**。検出の無かったページは
     原文のベクタページをそのまま複製する。
  2. `<name>.masked.txt` — プレースホルダ入りのテキスト。
     AI にはこちらを渡してもよい (ファイルは画像なので)。
  3. `<name>.masked.vault` — プレースホルダ⇔原文の対応表 (暗号化)。
     AI の回答に現れたプレースホルダを `file restore` で復元できる。

できないこと (README にも明記):

  - マスク済みファイル自体の復元 (画像化するため不可逆)。
    復元はテキスト (.txt) に対して行う。
  - OCR は Windows の標準 OCR のみ (winsdk は任意依存)。
  - 「検出漏れゼロ」は保証しない。
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .detect import Detector, Entity
from .masking import Masker, UnmaskResult
from .ocr import OCR_MISSING_HINT, OcrUnavailable, ocr_available, ocr_image
from .vault import Vault

log = logging.getLogger("jp_pii_mask.files")

__all__ = [
    "IMAGE_EXTENSIONS",
    "FileMaskError",
    "FileMaskResult",
    "mask_file",
    "restore_file",
]

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
)

MISSING_DEPS_HINT = (
    "PDF・画像のマスクに必要なパッケージがありません:\n"
    '  pip install "jp-pii-mask[file]"  (pypdfium2 と Pillow)'
)

# テキスト層がこれ未満 (空白除く文字数) ならスキャンページとみなし OCR する。
OCR_TEXT_THRESHOLD = 10

DEFAULT_DPI = 200
MIN_DPI = 72
MAX_DPI = 600
OCR_MODES = ("auto", "on", "off")


class FileMaskError(RuntimeError):
    """ファイルのマスク・復元に失敗したときの例外。"""


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Box:
    """ピクセル座標の矩形 (左上原点)。"""

    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def union(self, other: Box) -> Box:
        return Box(
            left=min(self.left, other.left),
            top=min(self.top, other.top),
            right=max(self.right, other.right),
            bottom=max(self.bottom, other.bottom),
        )

    def padded(self, pad: float) -> Box:
        return Box(self.left - pad, self.top - pad, self.right + pad, self.bottom + pad)

    def as_list(self) -> list[float]:
        return [self.left, self.top, self.right, self.bottom]


@dataclass(slots=True)
class Span:
    """テキスト範囲 [start, end) とその位置 (ピクセル座標)。"""

    start: int
    end: int
    box: Box | None


@dataclass(slots=True)
class PageSource:
    """1 ページ分のテキストと座標。"""

    text: str
    spans: list[Span] = field(default_factory=list)
    kind: str = "none"  # "pdf" | "ocr" | "none"


@dataclass
class FileMaskResult:
    """ファイル 1 件のマスク結果。"""

    input_path: Path
    masked_path: Path
    text_path: Path | None
    vault_path: Path | None
    kind: str
    pages: int
    pages_masked: int
    ocr_pages: int
    entities: int
    by_type: dict[str, int]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input": str(self.input_path),
            "masked": str(self.masked_path),
            "text": str(self.text_path) if self.text_path else None,
            "vault": str(self.vault_path) if self.vault_path else None,
            "kind": self.kind,
            "pages": self.pages,
            "pages_masked": self.pages_masked,
            "ocr_pages": self.ocr_pages,
            "entities": self.entities,
            "by_type": dict(self.by_type),
            "warnings": list(self.warnings),
        }


@dataclass
class _MaskedDoc:
    pages: int = 0
    pages_masked: int = 0
    ocr_pages: int = 0
    entities: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    masked_text: str = ""


# ---------------------------------------------------------------------------
# 任意依存の読み込み
# ---------------------------------------------------------------------------
def _pdfium() -> Any:
    try:
        import pypdfium2

        return pypdfium2
    except ImportError as exc:
        raise FileMaskError(MISSING_DEPS_HINT) from exc


def _pil() -> tuple[Any, Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as exc:
        raise FileMaskError(MISSING_DEPS_HINT) from exc
    return Image, ImageDraw, ImageOps


# ---------------------------------------------------------------------------
# 矩形のまとめ上げ
# ---------------------------------------------------------------------------
def merge_boxes(boxes: list[Box], h_gap: float = 6.0, v_overlap: float = 0.4) -> list[Box]:
    """同じ行にある近接した矩形を 1 つにまとめる。

    複数行にまたがるエンティティ (折り返した住所など) は
    行ごとに別の矩形として返す。行の判定は「縦方向の重なり」で行う。
    """
    clusters: list[Box] = []
    for box in boxes:
        if box.width <= 0 or box.height <= 0:
            continue
        changed = True
        while changed:
            changed = False
            for cluster in clusters:
                inter_top = max(cluster.top, box.top)
                inter_bottom = min(cluster.bottom, box.bottom)
                overlap = inter_bottom - inter_top
                min_height = max(1.0, min(cluster.height, box.height))
                same_line = overlap / min_height >= v_overlap
                near = box.left <= cluster.right + h_gap and box.right >= cluster.left - h_gap
                if same_line and near:
                    box = cluster.union(box)
                    clusters.remove(cluster)
                    changed = True
                    break
        clusters.append(box)
    return clusters


def entity_rects(entity: Entity, spans: list[Span], pad: float = 1.0) -> list[Box]:
    """エンティティに重なる文字/単語の矩形をまとめて返す。"""
    boxes = [
        span.box
        for span in spans
        if span.box is not None and span.start < entity.end and entity.start < span.end
    ]
    rects = merge_boxes([b for b in boxes if b is not None])
    return [r.padded(pad) for r in rects]


# ---------------------------------------------------------------------------
# 位置付きテキストの取り出し
# ---------------------------------------------------------------------------
def _charbox_to_image_box(
    left: float,
    bottom: float,
    right: float,
    top: float,
    page_width: float,
    page_height: float,
    rotation: int,
    scale: float,
) -> Box | None:
    """PDF の文字矩形を、描画画像 (左上原点) の矩形に変換する。

    PDF の座標は左下原点だが、/Rotate が付いたページは描画時に
    回転されるため、素直に (left, page_height - top) とすると
    黒塗りが別の場所に描かれる (実測で確認済みのバグ)。
    回転ごとの変換を実測に基づいて定義する。
    """
    rotation = int(rotation) % 360
    if rotation == 0:
        box = Box(left, page_height - top, right, page_height - bottom)
    elif rotation == 90:
        box = Box(bottom, left, top, right)
    elif rotation == 180:
        box = Box(page_width - right, bottom, page_width - left, top)
    else:  # 270
        box = Box(page_width - top, page_height - right, page_width - bottom, page_height - left)
    if box.right <= box.left or box.bottom <= box.top:
        return None
    return Box(
        left=box.left * scale,
        top=box.top * scale,
        right=box.right * scale,
        bottom=box.bottom * scale,
    )


def _extract_pdf_page(page: Any, dpi: int) -> PageSource:
    """PDF ページのテキスト層と文字座標を取り出す。"""
    page_width, page_height = page.get_size()
    rotation = 0
    with contextlib.suppress(Exception):
        rotation = page.get_rotation()
    if not isinstance(rotation, int):
        rotation = 0
    scale = dpi / 72.0
    text_page = page.get_textpage()
    try:
        count = text_page.count_chars()
        parts: list[str] = []
        spans: list[Span] = []
        pos = 0
        for index in range(count):
            char = text_page.get_text_range(index, 1)
            if not char:
                continue
            box = None
            if char.strip():
                with contextlib.suppress(Exception):
                    char_left, char_bottom, char_right, char_top = text_page.get_charbox(
                        index, loose=True
                    )
                    box = _charbox_to_image_box(
                        char_left,
                        char_bottom,
                        char_right,
                        char_top,
                        page_width,
                        page_height,
                        rotation,
                        scale,
                    )
            parts.append(char)
            if box is not None:
                spans.append(Span(start=pos, end=pos + len(char), box=box))
            pos += len(char)
        kind = "pdf" if spans else "none"
        return PageSource(text="".join(parts), spans=spans, kind=kind)
    finally:
        with contextlib.suppress(Exception):
            text_page.close()


def _needs_space(before: str, after: str) -> bool:
    """OCR の単語間に空白を入れるべきか (欧文の語間のみ)。"""
    return bool(
        before
        and after
        and before.isascii()
        and after.isascii()
        and before.isalnum()
        and after.isalnum()
    )


def _source_from_ocr_lines(lines: list[Any]) -> PageSource:
    """OCR 結果 (行 > 単語) を 1 本のテキスト + 座標に変換する。"""
    parts: list[str] = []
    spans: list[Span] = []
    pos = 0
    previous = ""
    for line in lines:
        for word in line.words:
            if not word.text:
                continue
            if previous and _needs_space(previous, word.text[0]):
                parts.append(" ")
                pos += 1
            start = pos
            parts.append(word.text)
            pos += len(word.text)
            spans.append(
                Span(
                    start=start,
                    end=pos,
                    box=Box(word.left, word.top, word.right, word.bottom),
                )
            )
            previous = word.text[-1]
        if previous:
            parts.append("\n")
            pos += 1
            previous = ""
    text = "".join(parts)
    return PageSource(text=text, spans=spans, kind="ocr")


def _render_page(page: Any, dpi: int) -> Any:
    """PDF ページを PIL 画像に描画する。"""
    bitmap = page.render(scale=dpi / 72.0)
    try:
        image = bitmap.to_pil()
    finally:
        with contextlib.suppress(Exception):
            bitmap.close()
    return image.convert("RGB")


# ---------------------------------------------------------------------------
# 検出と描画
# ---------------------------------------------------------------------------
def _detect_entities(
    detector: Detector, text: str, custom_terms: list[str] | None
) -> list[Entity]:
    """プロキシと同じ方針で検出する (追加語 + 重複解決)。"""
    if not text.strip():
        return []
    entities = detector.detect(text)
    for term in custom_terms or []:
        term = term.strip()
        if not term:
            continue
        for match in re.finditer(re.escape(term), text):
            entities.append(
                Entity("CUSTOM", match.start(), match.end(), match.group(0), 0.99, "custom")
            )
    return detector._resolve_overlaps(entities)


def _draw_masks(image: Any, source: PageSource, entities: list[Entity]) -> int:
    """検出位置を黒く塗りつぶす。描いた矩形数を返す。"""
    _, image_draw, _ = _pil()
    draw = image_draw.Draw(image)
    drawn = 0
    for entity in entities:
        rects = entity_rects(entity, source.spans)
        if not rects:
            raise FileMaskError(
                f"検出した個人情報の位置を特定できません ({entity.type}: {entity.text[:12]}…)。"
                " 安全のため処理を中止しました。"
            )
        for rect in rects:
            pad = max(1.0, rect.height * 0.06)
            draw.rectangle(rect.padded(pad).as_list(), fill=(0, 0, 0))
            drawn += 1
    return drawn


# ---------------------------------------------------------------------------
# マスク本体
# ---------------------------------------------------------------------------
def _page_source(
    page: Any,
    *,
    page_number: int,
    dpi: int,
    ocr_mode: str,
    result: _MaskedDoc,
) -> tuple[PageSource, Any | None, bool]:
    """ページのテキストを (テキスト層 or OCR) から取得する。

    戻り値: (テキスト源, 描画済み画像 or None, OCR を使ったか)
    """
    source = _extract_pdf_page(page, dpi)
    text_length = len(source.text.strip())
    want_ocr = ocr_mode == "on" or (ocr_mode == "auto" and text_length < OCR_TEXT_THRESHOLD)

    if not want_ocr:
        if text_length == 0 and ocr_mode == "off":
            result.warnings.append(
                f"{page_number} ページ目: テキスト層が無く --ocr off のため、"
                "このページは検出できません (画像は素通しされます)"
            )
        return source, None, False

    if not ocr_available():
        if ocr_mode == "on" or text_length == 0:
            raise FileMaskError(f"{page_number} ページ目: {OCR_MISSING_HINT}")
        result.warnings.append(
            f"{page_number} ページ目: スキャンと思われますが OCR が使えないため、"
            "少ないテキスト層のみを対象にしました"
        )
        return source, None, False

    image = _render_page(page, dpi)
    try:
        ocr_source = _source_from_ocr_lines(ocr_image(image))
    except OcrUnavailable as exc:
        if ocr_mode == "on":
            raise FileMaskError(f"{page_number} ページ目: OCR に失敗しました: {exc}") from exc
        result.warnings.append(f"{page_number} ページ目: OCR に失敗したためテキスト層を使用: {exc}")
        return source, image, False

    if ocr_source.text.strip():
        return ocr_source, image, True
    if ocr_mode == "on" and not source.text.strip():
        raise FileMaskError(f"{page_number} ページ目: OCR で文字を認識できませんでした")
    return source, image, False


def _pdf_bytes_from_image(image: Any, dpi: int) -> bytes:
    """1 ページの画像を PDF バイト列にする (テキスト層なし)。"""
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=dpi)
    return buffer.getvalue()


def _mask_pdf(
    src: Path,
    out: Path,
    *,
    detector: Detector,
    masker: Masker,
    vault: Vault,
    dpi: int,
    ocr_mode: str,
    custom_terms: list[str] | None,
) -> _MaskedDoc:
    pdfium = _pdfium()
    result = _MaskedDoc()
    document = pdfium.PdfDocument(str(src))
    masked_doc = pdfium.PdfDocument.new()
    pages_text: list[str] = []
    try:
        result.pages = len(document)
        if result.pages == 0:
            raise FileMaskError("PDF にページがありません。")
        for index in range(result.pages):
            page = document[index]
            page_number = index + 1
            source, image, used_ocr = _page_source(
                page, page_number=page_number, dpi=dpi, ocr_mode=ocr_mode, result=result
            )
            if used_ocr:
                result.ocr_pages += 1
            entities = _detect_entities(detector, source.text, custom_terms)
            if entities:
                if image is None:
                    image = _render_page(page, dpi)
                _draw_masks(image, source, entities)
                masked = masker.mask_with_entities(source.text, entities, vault)
                _import_pdf_image(pdfium, masked_doc, _pdf_bytes_from_image(image, dpi))
                result.pages_masked += 1
                result.entities += masked.count
                for key, value in masked.types_summary().items():
                    result.by_type[key] = result.by_type.get(key, 0) + value
                pages_text.append(masked.text)
            else:
                masked_doc.import_pages(document, pages=[index])
                pages_text.append(source.text)

        out.parent.mkdir(parents=True, exist_ok=True)
        masked_doc.save(str(out))
    finally:
        with contextlib.suppress(Exception):
            masked_doc.close()
        with contextlib.suppress(Exception):
            document.close()

    result.masked_text = _join_pages(pages_text)
    return result


def _import_pdf_image(pdfium: Any, target: Any, pdf_bytes: bytes) -> None:
    """画像 1 ページ分の PDF を、出力ドキュメントの末尾に追加する。"""
    temp = pdfium.PdfDocument(pdf_bytes)
    try:
        target.import_pages(temp, pages=[0])
    finally:
        with contextlib.suppress(Exception):
            temp.close()


def _join_pages(pages: list[str]) -> str:
    if len(pages) <= 1:
        return pages[0] if pages else ""
    blocks = [f"【ページ {i + 1}】\n{text}" for i, text in enumerate(pages)]
    return "\n\n".join(blocks)


def _mask_image(
    src: Path,
    out: Path,
    *,
    detector: Detector,
    masker: Masker,
    vault: Vault,
    ocr_mode: str,
    custom_terms: list[str] | None,
) -> _MaskedDoc:
    image_module, _, image_ops = _pil()
    result = _MaskedDoc(pages=1)
    if ocr_mode == "off":
        raise FileMaskError(
            "画像のマスクには OCR が必要です (--ocr auto または --ocr on)。\n"
            "  " + OCR_MISSING_HINT
        )
    if not ocr_available():
        raise FileMaskError(OCR_MISSING_HINT)

    with image_module.open(src) as raw:
        image = image_ops.exif_transpose(raw)
        image = image.convert("RGB")
        source = _source_from_ocr_lines(ocr_image(image))
    result.ocr_pages = 1

    entities = _detect_entities(detector, source.text, custom_terms)
    out.parent.mkdir(parents=True, exist_ok=True)
    if entities:
        _draw_masks(image, source, entities)
        _save_image(image, out)
        masked = masker.mask_with_entities(source.text, entities, vault)
        result.pages_masked = 1
        result.entities = masked.count
        result.by_type = masked.types_summary()
        result.masked_text = masked.text
    else:
        result.warnings.append(
            "個人情報を検出できませんでした (出力は入力の複製です)。"
            " スキャン品質や言語パックによっては認識できない場合があります。"
        )
        shutil.copyfile(src, out)
        result.masked_text = source.text
    return result


def _save_image(image: Any, out: Path) -> None:
    suffix = out.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        image.save(out, quality=90)
    elif suffix == ".png":
        image.save(out, optimize=True)
    else:
        image.save(out)


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------
def default_output_path(src: Path) -> Path:
    """入力 `a.pdf` に対する既定の出力 `a.masked.pdf`。"""
    return src.with_name(f"{src.stem}.masked{src.suffix}")


def output_path_for(src: Path, output_path: str | Path | None) -> Path:
    """出力パスを決め、拡張子を入力に合わせて補正する。"""
    out = Path(output_path) if output_path else default_output_path(src)
    expected = ".pdf" if src.suffix.lower() == ".pdf" else src.suffix.lower()
    if out.suffix.lower() != expected:
        out = Path(str(out) + expected)
    return out


def mask_file(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    detector: Detector,
    vault: Vault,
    dpi: int = DEFAULT_DPI,
    ocr: str = "auto",
    write_text: bool = True,
    custom_terms: list[str] | None = None,
) -> FileMaskResult:
    """PDF / 画像ファイルをマスクして保存する。"""
    _pdfium()  # 依存の有無を最初に確認する
    _pil()

    src = Path(input_path)
    if not src.is_file():
        raise FileMaskError(f"ファイルが見つかりません: {src}")
    if not MIN_DPI <= dpi <= MAX_DPI:
        raise FileMaskError(f"--dpi は {MIN_DPI}〜{MAX_DPI} の範囲で指定してください: {dpi}")
    if ocr not in OCR_MODES:
        raise FileMaskError(f"--ocr は {', '.join(OCR_MODES)} のいずれかです: {ocr}")

    suffix = src.suffix.lower()
    is_pdf = suffix == ".pdf"
    if not is_pdf and suffix not in IMAGE_EXTENSIONS:
        raise FileMaskError(
            f"対応していないファイル形式です: {suffix or '(拡張子なし)'}\n"
            "  対応形式: PDF / PNG / JPEG / BMP / WebP / TIFF"
        )

    out = output_path_for(src, output_path)
    if out.resolve() == src.resolve():
        raise FileMaskError("入力と出力が同じファイルです。上書きはできません。")

    masker = Masker(detector=detector)
    if is_pdf:
        document = _mask_pdf(
            src,
            out,
            detector=detector,
            masker=masker,
            vault=vault,
            dpi=dpi,
            ocr_mode=ocr,
            custom_terms=custom_terms,
        )
        kind = "pdf"
    else:
        document = _mask_image(
            src,
            out,
            detector=detector,
            masker=masker,
            vault=vault,
            ocr_mode=ocr,
            custom_terms=custom_terms,
        )
        kind = "image"

    text_path: Path | None = None
    if write_text:
        text_path = out.with_suffix(".txt")
        text_path.write_text(document.masked_text + "\n", encoding="utf-8")

    vault_path: Path | None = None
    if vault.store_path is not None:
        vault.save()
        vault_path = Path(vault.store_path)

    return FileMaskResult(
        input_path=src,
        masked_path=out,
        text_path=text_path,
        vault_path=vault_path,
        kind=kind,
        pages=document.pages,
        pages_masked=document.pages_masked,
        ocr_pages=document.ocr_pages,
        entities=document.entities,
        by_type=dict(document.by_type),
        warnings=list(document.warnings),
    )


def restore_file(
    text_path: str | Path,
    vault_path: str | Path,
    output_path: str | Path | None = None,
) -> UnmaskResult:
    """マスク済みテキスト (.txt) のプレースホルダを対応表で復元する。"""
    src = Path(text_path)
    vault_file = Path(vault_path)
    if not src.is_file():
        raise FileMaskError(f"テキストが見つかりません: {src}")
    if not vault_file.is_file():
        raise FileMaskError(
            f"対応表が見つかりません: {vault_file}\n"
            "  file mask が同じ場所に作った <name>.masked.vault を指定してください。"
        )

    vault = Vault(store_path=vault_file, encrypt=True)
    try:
        loaded = vault.load()
    except ValueError as exc:
        raise FileMaskError(
            "対応表を復号できませんでした。同じフォルダにある key.bin が必要です: "
            f"{exc}"
        ) from exc
    if not loaded:
        raise FileMaskError(f"対応表を読み込めませんでした: {vault_file}")

    text = src.read_text(encoding="utf-8", errors="replace")
    result = Masker().unmask(text, vault)
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.text, encoding="utf-8")
    return result


def mask_result_summary(result: FileMaskResult) -> str:
    """CLI 表示用の短いサマリ。"""
    lines = [
        f"  出力ファイル : {result.masked_path}",
    ]
    if result.text_path:
        lines.append(f"  テキスト     : {result.text_path}")
    if result.vault_path:
        lines.append(f"  対応表       : {result.vault_path} (restore に使用)")
    lines.append(
        f"  ページ       : {result.pages} (マスク {result.pages_masked} / OCR {result.ocr_pages})"
    )
    if result.entities:
        lines.append(f"  検出         : {result.entities} 件 {result.by_type}")
    else:
        lines.append("  検出         : 0 件 (個人情報は見つかりませんでした)")
    for warning in result.warnings:
        lines.append(f"  警告         : {warning}")
    return "\n".join(lines)
