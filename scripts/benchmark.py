"""検出・マスク・復元の遅延を実測するベンチマーク。

使い方:
    python scripts/benchmark.py             # 表形式で表示
    python scripts/benchmark.py --json      # JSON で出力 (記録用)
    python scripts/benchmark.py --ner       # NER (GiNZA) を有効化

計測するもの (すべてローカルで完結。ネットワーク不要):
    1. 検出のみ            Detector.detect
    2. マスク (検出込み)   Masker.mask + メモリのみの Vault
    3. 復元                Masker.unmask
    4. 往復                mask + unmask
    5. ストリーミング復元  StreamRestorer (24 文字ずつの delta)
    6. チャット payload    MaskingProxy.mask_payload / restore_payload
                           (実際の API リクエスト形での走査コスト)

「AI の応答を待つ時間に比べて誤差か」を判断するための材料であり、
マシン性能に依存する。数値は README の「性能」節に記録する。
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jp_pii_mask.config import ConfigStore
from jp_pii_mask.detect import Detector
from jp_pii_mask.masking import Masker, StreamRestorer
from jp_pii_mask.proxy import MaskingProxy
from jp_pii_mask.vault import Vault

# ---------------------------------------------------------------------------
# テスト文書の生成 (決定的: 乱数種を固定)
# ---------------------------------------------------------------------------
_NAMES = ["田中太郎", "佐藤花子", "鈴木一郎", "高橋美咲", "伊藤健太", "渡辺さくら"]
_COMPANIES = ["株式会社サンプル商事", "サンプル工業株式会社", "有限会社テスト電機"]
_ADDRESSES = ["〒100-0001 東京都千代田区千代田1-1", "〒530-0001 大阪市北区梅田1-2-3"]
_PHONES = ["090-1234-5678", "03-1234-5678", "0120-123-456", "080-9876-5432"]
_EMAILS = ["taro.tanaka@example.co.jp", "hanako@example.com", "info@sample.co.jp"]
_BANKS = ["普通 1234567", "当座 7654321", "普通 2468101"]
_MY_NUMBERS = ["526018159084", "301661318600"]
_CORP_NUMBERS = ["9139099603086", "2462819482191"]
_BIRTHS = ["1990年1月1日", "平成2年5月3日", "2001/09/21"]

_PLAIN = [
    "本日の会議では来期の計画と予算配分について議論しました。",
    "売上は前年比で緩やかに増加しており、来期も同程度を見込んでいます。",
    "対応方針は来週までにまとめて共有しますので、ご確認をお願いします。",
    "資料の 3 ページ目に、現状の課題と対策案を整理しています。",
    "レビューで指摘された点はすべて反映済みです。追加の要望があればお知らせください。",
    "納期は変更ありません。進捗は週次の定例で共有します。",
]

_PII_TEMPLATES = [
    "担当は{name}です。連絡先は{phone}、メールは{email}です。",
    "請求書の送付先は{company}、住所は{address}です。",
    "本人確認のため生年月日({birth})とマイナンバー({my})を控えています。",
    "振込先は{bank}、法人番号は{corp}です。",
    "{company}の{name}様より、折り返しのご連絡がありました。",
    "緊急時は {phone} まで。住所は {address} です。",
]


def _build_text(target_chars: int, pii_ratio: float, rng: random.Random) -> str:
    """target_chars 程度の日本語テキストを組み立てる。

    pii_ratio は「PII を含む文の割合」。0 なら PII なしの普通の文章。
    """
    parts: list[str] = []
    length = 0
    while length < target_chars:
        if pii_ratio > 0 and rng.random() < pii_ratio:
            template = rng.choice(_PII_TEMPLATES)
            block = template.format(
                name=rng.choice(_NAMES),
                company=rng.choice(_COMPANIES),
                address=rng.choice(_ADDRESSES),
                phone=rng.choice(_PHONES),
                email=rng.choice(_EMAILS),
                bank=rng.choice(_BANKS),
                my=rng.choice(_MY_NUMBERS),
                corp=rng.choice(_CORP_NUMBERS),
                birth=rng.choice(_BIRTHS),
            )
        else:
            block = rng.choice(_PLAIN)
        parts.append(block)
        length += len(block)
    return "\n".join(parts)


def _scenarios() -> list[dict[str, Any]]:
    rng = random.Random(20261020)
    return [
        {
            "name": "chat_short (1 メッセージ)",
            "text": _build_text(120, 0.5, rng),
            "iterations": 200,
        },
        {
            "name": "chat_medium (会話 1 リクエスト)",
            "text": _build_text(1200, 0.3, rng),
            "iterations": 100,
        },
        {
            "name": "chat_long",
            "text": _build_text(12000, 0.2, rng),
            "iterations": 20,
        },
        {
            "name": "doc_xlong (長文 1 リクエスト)",
            "text": _build_text(60000, 0.1, rng),
            "iterations": 5,
        },
        {
            "name": "plain_medium (PII なし)",
            "text": _build_text(1200, 0.0, rng),
            "iterations": 100,
        },
        {
            "name": "plain_long (PII なし)",
            "text": _build_text(12000, 0.0, rng),
            "iterations": 20,
        },
    ]


# ---------------------------------------------------------------------------
# 計測
# ---------------------------------------------------------------------------
def _measure(fn: Callable[[], Any], iterations: int, warmup: int = 3) -> list[float]:
    for _ in range(warmup):
        fn()
    times: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return times


def _p50(values: list[float]) -> float:
    return statistics.median(values)


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))
    return ordered[idx]


def _round(values: list[float]) -> dict[str, float]:
    return {"p50": round(_p50(values), 2), "p95": round(_p95(values), 2)}


def run(use_ner: bool) -> dict[str, Any]:
    detector = Detector(use_ner=use_ner)
    masker = Masker(detector)

    with tempfile.TemporaryDirectory(prefix="jp-pii-bench-") as tmp:
        store = ConfigStore(str(Path(tmp) / "config.json"))
        if use_ner:
            store.update(use_ner=True)
        proxy = MaskingProxy(store)
        _ = proxy.detector  # 構築を先に済ませる (初回のみの費用を計測から除く)

        results: list[dict[str, Any]] = []
        for sc in _scenarios():
            text: str = sc["text"]
            n: int = sc["iterations"]

            entities = detector.detect(text)
            detect_ms = _measure(lambda t=text: detector.detect(t), n)

            mask_ms: list[float] = []
            unmask_ms: list[float] = []
            cycle_ms: list[float] = []
            for _ in range(n):
                vault = Vault()
                t0 = time.perf_counter()
                masked = masker.mask(text, vault)
                t1 = time.perf_counter()
                masker.unmask(masked.text, vault)
                t2 = time.perf_counter()
                mask_ms.append((t1 - t0) * 1000.0)
                unmask_ms.append((t2 - t1) * 1000.0)
                cycle_ms.append((t2 - t0) * 1000.0)

            stream_ms: list[float] | None = None
            if len(text) <= 15000:
                # 先に「ストリーミングで原文に戻ること」を 1 回確認してから計測する
                v_check = Vault()
                m_check = masker.mask(text, v_check)
                r_check = StreamRestorer(masker, v_check)
                pieces: list[str] = []
                for i in range(0, len(m_check.text), 24):
                    pieces.append(r_check.feed(m_check.text[i : i + 24]))
                pieces.append(r_check.flush())
                assert "".join(pieces) == text, "ストリーミング復元が原文と一致しない"

                def _stream(t: str = text) -> None:
                    v = Vault()
                    m = masker.mask(t, v)
                    r = StreamRestorer(masker, v)
                    for i in range(0, len(m.text), 24):
                        r.feed(m.text[i : i + 24])
                    r.flush()

                stream_ms = _measure(_stream, max(3, n // 10))

            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "あなたは有能なアシスタントです。"},
                    {"role": "user", "content": text},
                ],
            }
            payload_mask_ms: list[float] = []
            for _ in range(max(3, n // 2)):
                vault = Vault()
                t0 = time.perf_counter()
                masked_payload, _, _ = proxy.mask_payload(payload, vault)
                t1 = time.perf_counter()
                proxy.restore_payload(masked_payload, vault)
                t2 = time.perf_counter()
                payload_mask_ms.append((t2 - t0) * 1000.0)

            chars = len(text)
            cycle_p50 = _p50(cycle_ms)
            results.append(
                {
                    "name": sc["name"],
                    "chars": chars,
                    "entities": len(entities),
                    "iterations": n,
                    "detect_ms": _round(detect_ms),
                    "mask_ms": _round(mask_ms),
                    "unmask_ms": _round(unmask_ms),
                    "cycle_ms": _round(cycle_ms),
                    "stream_ms": _round(stream_ms) if stream_ms else None,
                    "payload_ms": _round(payload_mask_ms),
                    "cycle_chars_per_sec": round(chars / (cycle_p50 / 1000.0)),
                }
            )
            print(
                f"  {sc['name']:<34} {chars:>7,} 文字  PII {len(entities):>4} 件  "
                f"往復 {cycle_p50:>7.2f} ms  ({chars / (cycle_p50 / 1000.0):>10,.0f} 文字/秒)"
            )

    return {
        "env": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "ner": use_ner,
        "results": results,
    }


def _cell(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return f"{value['p50']:>9.2f}" if value else " " * 9


def _print_table(report: dict[str, Any]) -> None:
    print()
    print("=" * 96)
    print("  内訳 (ms)  ※ mask は検出を含む / cycle は mask + unmask / payload は JSON 走査込み")
    print("=" * 96)
    header = (
        f"  {'シナリオ':<30} {'検出':>9} {'マスク':>9} {'復元':>9}"
        f" {'往復':>9} {'stream':>9} {'payload':>9}"
    )
    print(header)
    print("  " + "-" * 92)
    for row in report["results"]:
        print(
            f"  {row['name']:<30}"
            f" {_cell(row, 'detect_ms')} {_cell(row, 'mask_ms')} {_cell(row, 'unmask_ms')}"
            f" {_cell(row, 'cycle_ms')} {_cell(row, 'stream_ms')} {_cell(row, 'payload_ms')}"
        )
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="jp-pii-mask の遅延ベンチマーク")
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    parser.add_argument("--ner", action="store_true", help="NER (GiNZA) を有効化して計測")
    args = parser.parse_args(argv)

    if args.ner:
        try:
            import spacy  # noqa: F401
        except ImportError:
            print('NER を使うには: python -m pip install -e ".[ner]"', file=sys.stderr)
            return 2

    if not args.json:
        print()
        print("jp-pii-mask ベンチマーク")
        print(
            f"  Python {platform.python_version()} / {platform.machine()} / "
            f"{platform.processor() or 'unknown CPU'}"
        )
        print(f"  NER: {'有効 (GiNZA)' if args.ner else '無効 (ルールベース + 辞書)'}")
        print()

    report = run(use_ner=args.ner)

    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        _print_table(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
