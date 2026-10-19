"""PDF・画像の file 機能の疎通確認 (実ファイル・実 CLI・実 OCR)。

テストスイートが合成 PDF で検証するのに対し、こちらは

  1. 日本語の実文書 PDF (Edge/Chrome の print-to-pdf) を実際に生成
  2. スキャン PDF (画像のみ) と写真風 JPEG を生成
  3. jp-pii-mask の CLI を別プロセスで実行してマスク
  4. 出来上がったファイルの中身 (テキスト層・画素・対応表) を検証

を行う。「宣言した対応が実際に動くか」を確かめるのが目的。

使い方:
    python scripts/e2e_files.py

終了コード 0 = すべて PASS (SKIP を除く)。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont, ImageStat

from jp_pii_mask.ocr import ocr_available, ocr_image

PASS = "\033[92mPASS\033[0m" if os.name != "nt" else "PASS"
FAIL = "\033[91mFAIL\033[0m" if os.name != "nt" else "FAIL"
SKIP = "\033[93mSKIP\033[0m" if os.name != "nt" else "SKIP"

results: list[tuple[str, bool, str]] = []
skipped: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def skip(name: str, reason: str) -> None:
    skipped.append(name)
    print(f"  [{SKIP}] {name}  -- {reason}", flush=True)


# ---------------------------------------------------------------------------
# 入力の生成
# ---------------------------------------------------------------------------
BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]

PII_VALUES = [
    "田中太郎",
    "090-1234-5678",
    "taro.tanaka@example.co.jp",
    "123456789018",
    "4242 4242 4242 4242",
    "4242",
]

HTML = """<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><title>請求書</title></head>
<body style="font-family:'Yu Gothic','Meiryo',sans-serif;font-size:14px">
<h1>ご請求書</h1>
<p>お世話になっております。株式会社サンプル商事 営業部の田中太郎です。</p>
<table border="1" cellpadding="6">
<tr><td>電話番号</td><td>090-1234-5678</td></tr>
<tr><td>メール</td><td>taro.tanaka@example.co.jp</td></tr>
<tr><td>住所</td><td>〒100-0001 東京都千代田区千代田1-1 サンプルビル5階</td></tr>
<tr><td>マイナンバー</td><td>123456789018</td></tr>
<tr><td>カード番号</td><td>4242 4242 4242 4242</td></tr>
</table>
<p>以上、よろしくお願いいたします。</p>
</body>
</html>
"""


def find_browser() -> str | None:
    for path in BROWSER_CANDIDATES:
        if Path(path).exists():
            return path
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_font(size: int) -> ImageFont.FreeTypeFont | None:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return None


def generate_digital_pdf(workdir: Path) -> Path | None:
    """Edge/Chrome の print-to-pdf で「本物の」日本語テキスト PDF を作る。"""
    browser = find_browser()
    if browser is None:
        return None
    html_path = workdir / "invoice.html"
    html_path.write_text(HTML, encoding="utf-8")
    pdf_path = workdir / "digital.pdf"
    url = html_path.resolve().as_uri()
    for headless in ("--headless=new", "--headless"):
        cmd = [
            browser,
            headless,
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={workdir / 'browser-profile'}",
            f"--print-to-pdf={pdf_path}",
            url,
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        if pdf_path.exists():
            print(f"  ブラウザで PDF を生成: {Path(browser).name} ({proc.returncode})")
            return pdf_path
    return None


def generate_raster_inputs(workdir: Path) -> tuple[Path | None, Path | None]:
    """スキャン PDF (画像のみ) と写真風 JPEG を作る。"""
    font = find_font(36)
    if font is None:
        return None, None

    scan_image = Image.new("RGB", (1240, 480), "white")
    draw = ImageDraw.Draw(scan_image)
    draw.text((40, 40), "お客様各位", fill="black", font=font)
    draw.text((40, 120), "株式会社サンプル商事 営業部", fill="black", font=font)
    draw.text((40, 200), "担当: 田中太郎", fill="black", font=font)
    draw.text((40, 280), "TEL 090-1234-5678", fill="black", font=font)
    draw.text((40, 360), "Mail taro.tanaka@example.co.jp", fill="black", font=font)
    scan_pdf = workdir / "scan.pdf"
    scan_image.save(scan_pdf, format="PDF", resolution=150)

    photo = Image.new("RGB", (1100, 420), (250, 250, 245))
    draw = ImageDraw.Draw(photo)
    draw.text((40, 40), "ご連絡先の控え", fill="black", font=font)
    draw.text((40, 140), "携帯電話 090-1234-5678", fill="black", font=font)
    draw.text((40, 240), "メール taro.tanaka@example.co.jp", fill="black", font=font)
    photo_path = workdir / "photo.jpg"
    photo.save(photo_path, quality=92)
    return scan_pdf, photo_path


# ---------------------------------------------------------------------------
# 検証ヘルパ
# ---------------------------------------------------------------------------
def page_text(path: Path, index: int = 0) -> str:
    document = pdfium.PdfDocument(str(path))
    try:
        text_page = document[index].get_textpage()
        try:
            return text_page.get_text_range()
        finally:
            text_page.close()
    finally:
        document.close()


def page_count(path: Path) -> int:
    document = pdfium.PdfDocument(str(path))
    try:
        return len(document)
    finally:
        document.close()


def render_pdf_page(path: Path, index: int = 0, dpi: int = 200) -> Image.Image:
    document = pdfium.PdfDocument(str(path))
    try:
        bitmap = document[index].render(scale=dpi / 72.0)
        try:
            return bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
    finally:
        document.close()


def find_text_box(page_path: Path, needle: str, dpi: int) -> tuple[int, int, int, int] | None:
    """PDF ページ内の文字列の位置 (ピクセル) を文字単位座標から求める。"""
    document = pdfium.PdfDocument(str(page_path))
    try:
        page = document[0]
        _, height = page.get_size()
        scale = dpi / 72.0
        text_page = page.get_textpage()
        try:
            whole = text_page.get_text_range()
            index = whole.find(needle)
            if index < 0:
                return None
            left = top = 1e9
            right = bottom = -1e9
            for i in range(index, index + len(needle)):
                char_left, char_bottom, char_right, char_top = text_page.get_charbox(i, loose=True)
                left = min(left, char_left * scale)
                right = max(right, char_right * scale)
                top = min(top, (height - char_top) * scale)
                bottom = max(bottom, (height - char_bottom) * scale)
            if right <= left or bottom <= top:
                return None
            return int(left), int(top), int(right), int(bottom)
        finally:
            text_page.close()
    finally:
        document.close()


def region_mean(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    return float(ImageStat.Stat(image.crop(box).convert("L")).mean[0])


def run_cli(workdir: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["JP_PII_MASK_HOME"] = str(workdir / "home")
    return subprocess.run(
        [sys.executable, "-m", "jp_pii_mask.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        env=env,
        timeout=300,
        check=False,
    )


def load_vault_bytes(result: dict) -> bytes:
    return Path(result["vault"]).read_bytes()


def check_common_outputs(label: str, result: dict, require: list[str]) -> None:
    """マスク出力の共通検証。

    require: restore 後のテキストに必ず戻っているべき文字列。
    OCR は誤読があり得るため (例: ハイフンを長音に誤認)、
    OCR 系の入力では「090」のような誤読しにくい断片を指定する。
    """
    masked = Path(result["masked"])
    text_path = Path(result["text"])
    check(f"{label}: 出力ファイルが 3 種そろう", masked.exists() and text_path.exists() and result["vault"])

    if masked.suffix.lower() == ".pdf":
        masked_text = page_text(masked)
        leaked = [value for value in PII_VALUES if value in masked_text]
        check(
            f"{label}: 出力 PDF のテキスト層に個人情報が無い",
            not leaked,
            f"残存: {leaked}" if leaked else f"text={masked_text[:40]!r}",
        )
    else:
        # 画像出力はテキスト層の代わりに、マスク後の画像を OCR し直して
        # 個人情報が読めないことを確かめる。
        masked_image = Image.open(masked)
        ocr_text = "".join(
            word.text for line in ocr_image(masked_image) for word in line.words
        )
        leaked = [value for value in ("090", "@") if value in ocr_text]
        check(
            f"{label}: マスク済み画像を OCR しても個人情報が読めない",
            not leaked,
            f"読めた: {leaked} ({ocr_text[:40]!r})" if leaked else f"ocr={ocr_text[:40]!r}",
        )

    plain = text_path.read_text(encoding="utf-8")
    leaked_plain = [value for value in PII_VALUES if value in plain]
    check(
        f"{label}: マスク済みテキストに個人情報が無い",
        not leaked_plain,
        f"残存: {leaked_plain}" if leaked_plain else "placeholder あり",
    )
    check(f"{label}: プレースホルダが入っている", "[" in plain and "_" in plain)

    blob = load_vault_bytes(result)
    leaked_blob = [value for value in PII_VALUES if value.encode("utf-8") in blob]
    check(
        f"{label}: 対応表ファイルに平文の個人情報が無い (暗号化)",
        not leaked_blob,
        f"残存: {leaked_blob}" if leaked_blob else f"{len(blob)} bytes",
    )

    restored = run_cli(
        Path(result["masked"]).parent,
        ["file", "restore", result["text"], "--vault", result["vault"], "--json"],
    )
    ok_restore = restored.returncode == 0
    restored_text = ""
    if ok_restore:
        payload = json.loads(restored.stdout)
        restored_text = payload.get("text") or ""
        ok_restore = payload.get("missing", 1) == 0
    missing_required = [value for value in require if value not in restored_text]
    check(
        f"{label}: CLI restore で元の情報が戻る",
        ok_restore and not missing_required,
        f"rc={restored.returncode} missing={missing_required}",
    )


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="jp-pii-mask file 機能の疎通確認")
    parser.parse_args()

    print("=" * 66)
    print("  jp-pii-mask file 機能の疎通確認 (実ファイル・実 CLI・実 OCR)")
    print("=" * 66)

    workdir = Path(tempfile.mkdtemp(prefix="jp-pii-mask-files-e2e-"))
    print(f"\n作業ディレクトリ: {workdir}")

    # ---- [1] 入力の生成 --------------------------------------------------
    print("\n[1] 検証用ファイルの生成")
    digital_pdf = generate_digital_pdf(workdir)
    check("ブラウザで日本語テキスト PDF を生成", digital_pdf is not None)
    if digital_pdf is not None:
        text = page_text(digital_pdf)
        check(
            "生成 PDF から日本語テキスト層が読める",
            "田中太郎" in text and "090-1234-5678" in text,
            text[:60].replace("\n", " "),
        )

    scan_pdf, photo_path = generate_raster_inputs(workdir)
    check("スキャン PDF と JPEG を生成", scan_pdf is not None and photo_path is not None)

    # ---- [2] デジタル PDF (テキスト層ルート) -----------------------------
    if digital_pdf is not None:
        print("\n[2] デジタル PDF のマスク (テキスト層ルート)")
        proc = run_cli(workdir, ["file", "mask", str(digital_pdf), "--json"])
        check("CLI が終了コード 0", proc.returncode == 0, proc.stderr.strip()[:80])
        if proc.returncode == 0:
            result = json.loads(proc.stdout)
            check(
                "個人情報を検出した (氏名・法人・電話・メール・番号)",
                result["entities"] >= 5,
                f"entities={result['entities']} {result['by_type']}",
            )
            check("ページ数が保たれる", result["pages"] == 1 and page_count(Path(result["masked"])) == 1)
            check("検出のあったページが画像化された", result["pages_masked"] == 1)
            check_common_outputs("digital", result, PII_VALUES)

            # 鍵は出力フォルダに置かず、ユーザー領域に DPAPI 保護で置く
            outdir = Path(result["masked"]).parent
            home = workdir / "home"
            check(
                "digital: 出力フォルダに key.bin が作られない (鍵の分離)",
                not (outdir / "key.bin").exists(),
            )
            key_file = home / "file-key.bin"
            check("digital: ユーザー領域に file-key.bin が作られる", key_file.is_file())
            if key_file.is_file():
                key_blob = key_file.read_bytes()
                check(
                    "digital: 鍵が DPAPI で保護されている",
                    key_blob.startswith(b"JPPMK1") and len(key_blob) != 32,
                    f"{len(key_blob)} bytes",
                )

            # 画素で確認: 電話番号の位置が黒く塗られている
            masked_image = render_pdf_page(Path(result["masked"]), 0, 200)
            box = find_text_box(digital_pdf, "090-1234-5678", 200)
            if box is None:
                skip("digital: 黒塗りの画素確認", "元 PDF の文字位置を特定できず")
            else:
                check(
                    "digital: 電話番号の位置が黒塗りされている",
                    region_mean(masked_image, box) < 60,
                    f"mean={region_mean(masked_image, box):.1f}",
                )
    else:
        skip("デジタル PDF の検証", "Edge/Chrome が見つかりません")

    # ---- [3] スキャン PDF / 画像 (OCR ルート) ----------------------------
    if scan_pdf is None or photo_path is None:
        skip("スキャン PDF / 画像の検証", "日本語フォントが見つかりません")
    elif not ocr_available():
        skip("スキャン PDF / 画像の検証", "Windows 標準 OCR (winsdk) が使えません")
    else:
        print("\n[3] スキャン PDF のマスク (OCR ルート)")
        proc = run_cli(workdir, ["file", "mask", str(scan_pdf), "--json"])
        check("CLI が終了コード 0", proc.returncode == 0, proc.stderr.strip()[:80])
        if proc.returncode == 0:
            result = json.loads(proc.stdout)
            check("OCR で文字を認識し個人情報を検出", result["ocr_pages"] == 1 and result["entities"] >= 2,
                  f"entities={result['entities']} {result['by_type']}")
            check_common_outputs("scan", result, ["090", "taro.tanaka@example.co.jp", "田中太郎"])

            # スキャン PDF の元画像をレンダリングして電話番号の位置を特定
            original_render = render_pdf_page(scan_pdf, 0, 150)
            phone_boxes = [
                word for line in ocr_image(original_render) for word in line.words
                if "090" in word.text
            ]
            masked_render = render_pdf_page(Path(result["masked"]), 0, 150)
            if not phone_boxes:
                skip("scan: 黒塗りの画素確認", "OCR が電話番号の位置を返さず")
            else:
                word = phone_boxes[0]
                box = (int(word.left), int(word.top), int(word.right), int(word.bottom))
                check(
                    "scan: 電話番号の位置が黒塗りされている",
                    region_mean(masked_render, box) < 60,
                    f"mean={region_mean(masked_render, box):.1f}",
                )

        print("\n[4] 画像 (JPEG) のマスク (OCR ルート)")
        proc = run_cli(workdir, ["file", "mask", str(photo_path), "--json"])
        check("CLI が終了コード 0", proc.returncode == 0, proc.stderr.strip()[:80])
        if proc.returncode == 0:
            result = json.loads(proc.stdout)
            check("OCR で個人情報を検出", result["ocr_pages"] == 1 and result["entities"] >= 2,
                  f"entities={result['entities']} {result['by_type']}")
            check_common_outputs("image", result, ["090", "taro.tanaka@example.co.jp"])

            original_photo = Image.open(photo_path).convert("RGB")
            phone_boxes = [
                word for line in ocr_image(original_photo) for word in line.words
                if "090" in word.text
            ]
            masked_photo = Image.open(result["masked"]).convert("RGB")
            if not phone_boxes:
                skip("image: 黒塗りの画素確認", "OCR が電話番号の位置を返さず")
            else:
                word = phone_boxes[0]
                box = (int(word.left), int(word.top), int(word.right), int(word.bottom))
                check(
                    "image: 電話番号の位置が黒塗りされている",
                    region_mean(masked_photo, box) < 60,
                    f"mean={region_mean(masked_photo, box):.1f}",
                )

    # ---- [5] 異常系 ------------------------------------------------------
    print("\n[5] 異常系")
    proc = run_cli(workdir, ["file", "mask", str(workdir / "missing.pdf")])
    check("存在しないファイルは終了コード 2", proc.returncode == 2, proc.stderr.strip()[:60])
    proc = run_cli(
        workdir,
        ["file", "restore", str(workdir / "nothing.txt"), "--vault", str(workdir / "nothing.vault")],
    )
    check("対応表が無い restore は終了コード 2", proc.returncode == 2, proc.stderr.strip()[:60])

    # ---- 結果 ------------------------------------------------------------
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 66)
    print(f"  結果: {passed}/{total} PASS  /  SKIP {len(skipped)}")
    if passed != total:
        print("  失敗:")
        for name, ok, detail in results:
            if not ok:
                print(f"    - {name}  {detail}")
    print(f"  作業ディレクトリ (確認用に保持): {workdir}")
    print("=" * 66)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
