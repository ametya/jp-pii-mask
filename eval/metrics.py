"""評価コーパスから検出精度 (recall / precision) を計算する。

判定ルール:
  - 一致 = 型が同じ、かつ文字列が完全一致 (前後の空白は無視)。
    境界のズレ (法人名が助詞を飲み込む等) も検出したいため、
    部分一致は認めない。
  - 同じ値が文中に複数回現れる場合、その値への検出はすべて正解とみなす。

しきい値 (CI 用) は実測の少し下に置き、回帰の検知に使う。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jp_pii_mask.detect import Detector, Entity

from .corpus import DOCUMENTS, Document

# CI で守る下限。現状の実測値は recall / precision とも 100%。
# 数件の回帰は micro 平均では埋もれるため、しきい値は高めに置き、
# 個別の見逃し・過検出は tests/test_eval_corpus.py で直接検査する。
MIN_RECALL = 0.95
MIN_PRECISION = 0.95


@dataclass
class TypeRow:
    type: str
    expected: int = 0
    detected: int = 0
    tp: int = 0

    @property
    def fn(self) -> int:
        return self.expected - self.tp

    @property
    def fp(self) -> int:
        return self.detected - self.tp

    @property
    def recall(self) -> float:
        return self.tp / self.expected if self.expected else 1.0

    @property
    def precision(self) -> float:
        return self.tp / self.detected if self.detected else 1.0


@dataclass
class Report:
    rows: dict[str, TypeRow] = field(default_factory=dict)
    expected_total: int = 0
    detected_total: int = 0
    tp_total: int = 0
    misses: list[str] = field(default_factory=list)
    false_positives: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.tp_total / self.expected_total if self.expected_total else 1.0

    @property
    def precision(self) -> float:
        return self.tp_total / self.detected_total if self.detected_total else 1.0


def _matches(detected: Entity, exp_type: str, exp_text: str) -> bool:
    return detected.type == exp_type and (
        detected.text.strip(" 　") == exp_text.strip(" 　")
    )


def evaluate_document(document: Document, detected: list[Entity]) -> Report:
    report = Report()
    expected_keys: list[tuple[str, str]] = [
        (exp.type, exp.text) for exp in document.expected
    ]
    matched_expected: set[int] = set()
    matched_detected: set[int] = set()

    for di, det in enumerate(detected):
        row = report.rows.setdefault(det.type, TypeRow(det.type))
        row.detected += 1
        for ei, (exp_type, exp_text) in enumerate(expected_keys):
            if ei in matched_expected:
                continue
            if _matches(det, exp_type, exp_text):
                matched_expected.add(ei)
                matched_detected.add(di)
                break

    for ei, (exp_type, exp_text) in enumerate(expected_keys):
        row = report.rows.setdefault(exp_type, TypeRow(exp_type))
        row.expected += 1
        if ei in matched_expected:
            row.tp += 1
        else:
            report.misses.append(f"{document.id}: {exp_type} {exp_text!r}")

    for di, det in enumerate(detected):
        if di not in matched_detected:
            report.false_positives.append(
                f"{document.id}: {det.type} {det.text!r}"
            )

    report.expected_total = len(expected_keys)
    report.detected_total = len(detected)
    report.tp_total = len(matched_expected)
    return report


def evaluate(detector: Detector | None = None) -> Report:
    """全コーパスを評価して合算レポートを返す。"""
    if detector is None:
        detector = Detector(min_confidence=0.5)

    report = Report()
    for document in DOCUMENTS:
        detected = detector.detect(document.text)
        part = evaluate_document(document, detected)
        for type_name, row in part.rows.items():
            agg = report.rows.setdefault(type_name, TypeRow(type_name))
            agg.expected += row.expected
            agg.detected += row.detected
            agg.tp += row.tp
        report.misses.extend(part.misses)
        report.false_positives.extend(part.false_positives)
        report.expected_total += part.expected_total
        report.detected_total += part.detected_total
        report.tp_total += part.tp_total
    return report
