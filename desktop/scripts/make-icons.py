#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
プレースホルダアイコン生成スクリプト (標準ライブラリのみ / Pillow 不要)

生成物 (desktop/src-tauri/icons/):
  32x32.png
  128x128.png
  128x128@2x.png      (256x256)
  icon.png            (512x512)
  icon.ico            (Windows: 16/32/48/64/128/256 を PNG 圧縮で格納)
  icon.icns           (macOS: icp4/icp5/ic07/ic08/ic09/ic10)

デザインは「青い角丸四角 + 白いシールド + キーホール」の仮アイコン。
本番用のアイコンが用意できたら、次の標準コマンドで作り直すこと:

    cargo tauri icon path/to/icon.png     # 1024x1024 PNG を推奨

⚠️ 未検証: このスクリプトの出力はまだ実際の Tauri ビルドで検証していない。
           生成ロジック自体は標準ライブラリのみで完結しているが、
           アイコンの見た目の良し悪しは確認していない。
"""

from __future__ import annotations

import os
import struct
import zlib

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "src-tauri", "icons"))

# 背景 (青) / シールド (白) / キーホール (濃紺)
BG_TOP = (37, 99, 235)
BG_BOTTOM = (23, 65, 165)
SHIELD = (255, 255, 255)
KEYHOLE = (23, 65, 165)

SUPERSAMPLE = 3  # 3x3 のスーパーサンプリングでアンチエイリアス


# ---------------------------------------------------------------------------
# 図形の定義 (座標は -1..1 の正規化空間。y は下向きが正)
# ---------------------------------------------------------------------------
def _rounded_square_alpha(x: float, y: float, r: float) -> bool:
    """角丸四角の内側なら True。r は角の半径 (-1..1 尺度)。"""
    ax, ay = abs(x), abs(y)
    if ax > 1.0 or ay > 1.0:
        return False
    if ax <= 1.0 - r or ay <= 1.0 - r:
        return True
    cx, cy = 1.0 - r, 1.0 - r
    return (ax - cx) ** 2 + (ay - cy) ** 2 <= r * r


def _shield_inside(x: float, y: float) -> bool:
    """シールド形の内側なら True。上辺は平ら、下は尖る。"""
    top, bottom = -0.62, 0.72
    if y < top or y > bottom:
        return False
    half = 0.60
    if y <= 0.0:
        width = half
    else:
        t = y / bottom
        width = half * (1.0 - t * t) ** 0.5
        if width <= 0.0:
            return False
    return abs(x) <= width


def _keyhole_inside(x: float, y: float) -> bool:
    """シールド内のキーホール (丸 + 下向きの軸) の内側なら True。"""
    # 丸の部分
    if (x / 0.17) ** 2 + ((y + 0.06) / 0.17) ** 2 <= 1.0:
        return True
    # 軸の部分
    if abs(x) <= 0.075 and 0.08 <= y <= 0.40:
        return True
    return False


def _shade(color_a, color_b, t: float):
    """2 色を t (0..1) で線形補間。"""
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(color_a, color_b))


# ---------------------------------------------------------------------------
# レンダリング
# ---------------------------------------------------------------------------
def render_rgba(size: int) -> bytes:
    """size x size の RGBA ピクセル列 (row-major) を返す。"""
    ss = SUPERSAMPLE
    inv = 1.0 / (size * ss)
    out = bytearray(size * size * 4)

    for py in range(size):
        for px in range(size):
            # スーパーサンプリングで被覆率を求める
            r_sum = g_sum = b_sum = a_sum = 0
            for sy in range(ss):
                for sx in range(ss):
                    # ピクセル中心を -1..1 に写像
                    x = ((px * ss + sx) + 0.5) * inv * 2.0 - 1.0
                    y = ((py * ss + sy) + 0.5) * inv * 2.0 - 1.0

                    if not _rounded_square_alpha(x, y, 0.30):
                        continue  # 透明

                    if _shield_inside(x, y):
                        if _keyhole_inside(x, y):
                            r, g, b = KEYHOLE
                        else:
                            r, g, b = SHIELD
                    else:
                        # 背景は上下グラデーション
                        t = (y + 1.0) * 0.5
                        r, g, b = _shade(BG_TOP, BG_BOTTOM, t)

                    r_sum += r
                    g_sum += g
                    b_sum += b
                    a_sum += 255

            n = ss * ss
            i = (py * size + px) * 4
            if a_sum == 0:
                out[i:i + 4] = b"\x00\x00\x00\x00"
                continue
            # 被覆ピクセルの平均色 + 被覆率をアルファに
            covered = a_sum // 255
            out[i] = r_sum // covered
            out[i + 1] = g_sum // covered
            out[i + 2] = b_sum // covered
            out[i + 3] = a_sum // n

    return bytes(out)


# ---------------------------------------------------------------------------
# PNG / ICO / ICNS のエンコーダ
# ---------------------------------------------------------------------------
def png_bytes(size: int, rgba: bytes) -> bytes:
    """8bit RGBA の PNG を組み立てる。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)  # filter type: None
        raw += rgba[y * stride:(y + 1) * stride]

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def ico_bytes(images) -> bytes:
    """
    ICO を組み立てる。images は [(size, png_data), ...]。
    Vista 以降は PNG 圧縮エントリを直接格納できる。
    """
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count

    entries = bytearray()
    payload = bytearray()
    for size, data in images:
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset
        )
        payload += data
        offset += len(data)

    return bytes(header + entries + payload)


# icns の OSType とピクセルサイズの対応 (PNG データを格納できるものだけ使う)
ICNS_TYPES = [
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic11", 64),
    (b"ic07", 128),
    (b"ic12", 128),
    (b"ic08", 256),
    (b"ic13", 256),
    (b"ic09", 512),
    (b"ic10", 512),
]


def icns_bytes(png_by_size: dict) -> bytes:
    """macOS の .icns コンテナを組み立てる (PNG データを格納)。"""
    body = bytearray()
    for ostype, size in ICNS_TYPES:
        data = png_by_size.get(size)
        if data is None:
            continue
        body += ostype + struct.pack(">I", len(data) + 8) + data

    total = 8 + len(body)
    return b"icns" + struct.pack(">I", total) + bytes(body)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)

    # 必要なサイズだけレンダリングして使い回す
    sizes = sorted({16, 32, 48, 64, 128, 256, 512})
    png_by_size = {}
    for size in sizes:
        rgba = render_rgba(size)
        png_by_size[size] = png_bytes(size, rgba)
        print(f"  レンダリング: {size}x{size}")

    # 個別 PNG
    files = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
        "icon.png": 512,
    }
    for name, size in files.items():
        path = os.path.join(OUT_DIR, name)
        with open(path, "wb") as fp:
            fp.write(png_by_size[size])
        print(f"  書き出し: {name}")

    # Windows .ico
    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_path = os.path.join(OUT_DIR, "icon.ico")
    with open(ico_path, "wb") as fp:
        fp.write(ico_bytes([(s, png_by_size[s]) for s in ico_sizes]))
    print("  書き出し: icon.ico")

    # macOS .icns
    icns_path = os.path.join(OUT_DIR, "icon.icns")
    with open(icns_path, "wb") as fp:
        fp.write(icns_bytes(png_by_size))
    print("  書き出し: icon.icns")

    print(f"\n完了しました: {OUT_DIR}")
    print("これらはプレースホルダです。本番用は `cargo tauri icon <png>` で生成してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
