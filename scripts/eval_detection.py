"""検出精度の評価 (eval/corpus.py のラベル付き文書で recall / precision を測る)。

使い方:
    python scripts/eval_detection.py           # 人向けの表
    python scripts/eval_detection.py --json    # 機械可読
    python scripts/eval_detection.py --check   # CI 用 (下限割れで exit 1)

「検出できるか」だけでなく「検出しすぎないか」を同時に測る。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.metrics import MIN_PRECISION, MIN_RECALL, Report, evaluate


def _print_report(report: Report) -> None:
    print("型別の精度 (ラベル数 / 検出数 / 一致 / 再現率 / 適合率)")
    print("-" * 72)
    for type_name in sorted(report.rows):
        row = report.rows[type_name]
        print(
            f"  {type_name:<22} {row.expected:>3} / {row.detected:>3} /"
            f" {row.tp:>3}   {row.recall:>6.1%}  {row.precision:>6.1%}"
        )
    print("-" * 72)
    print(
        f"  全体: ラベル {report.expected_total} / 検出 {report.detected_total} /"
        f" 一致 {report.tp_total}"
    )
    print(f"  再現率 (recall)   : {report.recall:.1%}")
    print(f"  適合率 (precision): {report.precision:.1%}")

    if report.misses:
        print("\n未検出 (ラベルに対して検出が無い):")
        for item in report.misses:
            print(f"  - {item}")
    if report.false_positives:
        print("\n過検出 (ラベルに無い検出):")
        for item in report.false_positives:
            print(f"  - {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="検出精度の評価")
    parser.add_argument("--json", action="store_true", help="JSON で出力")
    parser.add_argument(
        "--check",
        action="store_true",
        help=f"しきい値 (recall {MIN_RECALL:.0%} / precision {MIN_PRECISION:.0%}) 未満なら exit 1",
    )
    args = parser.parse_args(argv)

    report = evaluate()

    if args.json:
        payload = {
            "recall": round(report.recall, 4),
            "precision": round(report.precision, 4),
            "expected_total": report.expected_total,
            "detected_total": report.detected_total,
            "matched": report.tp_total,
            "by_type": {
                t: {
                    "expected": r.expected,
                    "detected": r.detected,
                    "matched": r.tp,
                    "recall": round(r.recall, 4),
                    "precision": round(r.precision, 4),
                }
                for t, r in sorted(report.rows.items())
            },
            "misses": report.misses,
            "false_positives": report.false_positives,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(report)

    if args.check and (
        report.recall < MIN_RECALL or report.precision < MIN_PRECISION
    ):
        print(
            f"\n評価がしきい値を下回りました: "
            f"recall={report.recall:.1%} (下限 {MIN_RECALL:.0%}), "
            f"precision={report.precision:.1%} (下限 {MIN_PRECISION:.0%})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
