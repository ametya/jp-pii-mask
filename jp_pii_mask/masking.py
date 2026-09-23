"""マスキングと復元の本体。

    masked, result = mask_text(原文, vault, detector)
    restored      = unmask_text(masked, vault)

復元の頑健性:
  LLM は稀にプレースホルダを書き換える (全角括弧にする、空白を入れる、
  記号を落とす)。そこで復元は
    1. 厳密一致 (最速)
    2. 正規表現による揺れ吸収
    3. ラベル+番号からの再構成
  の 3 段構えで行う。復元できなかった箇所は統計に計上し、呼び出し側が
  「復元漏れ」を検知できるようにする。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Iterable

from .detect import Detector, Entity, TYPE_LABELS
from .vault import PLACEHOLDER_RE, Vault

__all__ = ["MaskResult", "Masker", "StreamRestorer", "mask_text", "unmask_text"]


@dataclass
class MaskResult:
    """1 回のマスキング結果。"""

    text: str
    entities: list[Entity] = field(default_factory=list)
    replacements: dict[str, str] = field(default_factory=dict)  # placeholder -> original

    @property
    def count(self) -> int:
        return len(self.entities)

    def types_summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entities:
            out[e.type] = out.get(e.type, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "count": self.count,
            "entities": [e.as_dict() for e in self.entities],
            "types": self.types_summary(),
            "replacements": self.replacements,
        }


@dataclass
class UnmaskResult:
    """復元結果と統計。"""

    text: str
    restored: int = 0
    missing: list[str] = field(default_factory=list)


class Masker:
    """検出器と Vault を組み合わせてマスク／復元する。"""

    def __init__(
        self,
        detector: Detector | None = None,
        min_confidence: float = 0.5,
        redact_only: bool = False,
        redaction_text: str = "[REDACTED]",
    ) -> None:
        self.detector = detector or Detector(min_confidence=min_confidence)
        self.redact_only = redact_only
        self.redaction_text = redaction_text

    # -- マスク -----------------------------------------------------------
    def mask(self, text: str, vault: Vault) -> MaskResult:
        entities = self.detector.detect(text)
        return self.mask_with_entities(text, entities, vault)

    def mask_with_entities(
        self, text: str, entities: Iterable[Entity], vault: Vault
    ) -> MaskResult:
        """検出済みエンティティを使ってマスクする (位置は原文基準)。"""
        ents = sorted(entities, key=lambda e: e.start)
        if not ents:
            return MaskResult(text=text, entities=[])

        out: list[str] = []
        cursor = 0
        used: list[Entity] = []
        replacements: dict[str, str] = {}

        for ent in ents:
            if ent.start < cursor:
                continue
            original = text[ent.start:ent.end]
            if self.redact_only:
                placeholder = self.redaction_text
            else:
                placeholder = vault.placeholder_for(original, ent.type)
                replacements[placeholder] = original
            out.append(text[cursor:ent.start])
            out.append(placeholder)
            cursor = ent.end
            used.append(ent)

        out.append(text[cursor:])
        return MaskResult(text="".join(out), entities=used, replacements=replacements)

    # -- 復元 -------------------------------------------------------------
    def unmask(self, text: str, vault: Vault) -> UnmaskResult:
        """プレースホルダを原文に戻す。"""
        if not text:
            return UnmaskResult(text=text)

        restored_count = 0
        missing: list[str] = []

        def _replace(m: re.Match[str]) -> str:
            nonlocal restored_count
            whole = m.group(0)
            # 1) 厳密一致
            original = vault.original_for(whole)
            if original is not None:
                restored_count += 1
                return original
            # 2) ラベル + 番号で再構成
            label = m.group(1)
            index = m.group(2)
            for cand_label in (label, label.replace(" ", "")):
                recon = f"[{cand_label}_{int(index)}]"
                original = vault.original_for(recon)
                if original is not None:
                    restored_count += 1
                    return original
                # 3) 番号だけで一意に決まる場合は復元する
            # 3) 数字のみから検索 (同一番号が複数あれば曖昧なので行わない)
            matches = [e for e in vault.entries() if e.index == int(index)]
            if len(matches) == 1:
                restored_count += 1
                return matches[0].original
            missing.append(whole)
            return whole

        result = PLACEHOLDER_RE.sub(_replace, text)
        return UnmaskResult(text=result, restored=restored_count, missing=missing)


class StreamRestorer:
    """ストリーミング応答用の逐次復元器。

    問題: SSE のデルタはプレースホルダの途中で切れることがある
    (例: `[氏` + `名_1]`)。即座に置換すると復元に失敗する。

    解決: バッファに溜め、末尾がプレースホルダの先頭部分 (未完) である
    可能性がある間は出力を保留する。安全と判断できた分だけを払い出す。
    """

    # プレースホルダの開始記号。これ以降は未完の可能性を考慮して保留する。
    _OPENERS = "[［【"
    _MAX_LOOKBEHIND = 64

    def __init__(self, masker: Masker, vault: Vault) -> None:
        self.masker = masker
        self.vault = vault
        self._buffer = ""
        self._lock = threading.Lock()
        self.restored_total = 0

    def feed(self, delta: str) -> str:
        """デルタを受け取り、確定分の復元済みテキストを返す。"""
        with self._lock:
            self._buffer += delta
            return self._drain(final=False)

    def flush(self) -> str:
        """残りをすべて払い出す。"""
        with self._lock:
            return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        buf = self._buffer
        if not buf:
            return ""

        if final:
            self._buffer = ""
            res = self.masker.unmask(buf, self.vault)
            self.restored_total += res.restored
            return res.text

        # 保留すべき末尾を決める
        hold = self._pending_tail(buf)
        if hold == 0:
            self._buffer = ""
            res = self.masker.unmask(buf, self.vault)
            self.restored_total += res.restored
            return res.text

        emit, rest = buf[: len(buf) - hold], buf[len(buf) - hold:]
        self._buffer = rest
        if not emit:
            return ""
        res = self.masker.unmask(emit, self.vault)
        self.restored_total += res.restored
        return res.text

    def _pending_tail(self, buf: str) -> int:
        """末尾に「未完のプレースホルダ」がある場合、その長さを返す。"""
        limit = min(len(buf), self._MAX_LOOKBEHIND)
        start = len(buf) - limit
        for i in range(len(buf) - 1, start - 1, -1):
            ch = buf[i]
            if ch in self._OPENERS:
                tail = buf[i:]
                if PLACEHOLDER_RE.fullmatch(tail):
                    return 0            # すでに完成形
                # 閉じ括弧が無い、または数字が未確定 -> 保留
                if len(tail) <= 40:
                    return len(tail)
                return 0
            if ch in "]］】":
                return 0
        return 0


# ---------------------------------------------------------------------------
# 手続き型の入口
# ---------------------------------------------------------------------------
def mask_text(
    text: str,
    vault: Vault,
    detector: Detector | None = None,
    min_confidence: float = 0.5,
) -> tuple[str, MaskResult]:
    """テキストをマスクし、(マスク済み文字列, 結果) を返す。"""
    masker = Masker(detector=detector, min_confidence=min_confidence)
    result = masker.mask(text, vault)
    return result.text, result


def unmask_text(text: str, vault: Vault) -> str:
    """プレースホルダを原文に戻す (統計不要な簡易版)。"""
    return Masker().unmask(text, vault).text
