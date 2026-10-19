"""評価コーパス (eval/) の整合性と検出精度の回帰テスト。

scripts/eval_detection.py --check と同じ下限に加え、「どのラベルが
落ちたか」をそのまま失敗メッセージで示せるよう、個別の見逃し・
過検出も直接検査する (コーパスは小さいのでこれが最も敏感な回帰検知)。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.corpus import DOCUMENTS
from eval.metrics import MIN_PRECISION, MIN_RECALL, evaluate


def test_corpus_ids_unique():
    ids = [d.id for d in DOCUMENTS]
    assert len(ids) == len(set(ids))


def test_corpus_has_negative_documents():
    # 「何も検出しないべき文書」が無いと precision の回帰を検知できない
    assert any(not d.expected for d in DOCUMENTS)


def test_detection_meets_thresholds():
    report = evaluate()
    assert report.recall >= MIN_RECALL, report.misses
    assert report.precision >= MIN_PRECISION, report.false_positives


def test_no_misses_or_false_positives():
    report = evaluate()
    assert report.misses == [], f"未検出: {report.misses}"
    assert report.false_positives == [], f"過検出: {report.false_positives}"
