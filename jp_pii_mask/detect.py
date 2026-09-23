"""日本語個人情報の検出エンジン。

検出方式は 3 層のハイブリッド:

  第 1 層: 検証付きパターン (電話・郵便・マイナンバー等)
      正規表現で候補を出し、validators.py の検査用数字で確定させる。
      日本の公的番号は検査用数字を持つため、この層だけで誤検出をほぼ排除できる。

  第 2 層: 辞書 + 文脈 (氏名・法人・住所)
      姓辞書 / 法人格 / 都道府県をアンカーにし、周辺の敬称や助詞で境界を決める。

  第 3 層: オプション NER (GiNZA 等)
      インストールされていれば追加候補を出し、第 1・2 層の結果と統合する。
      未インストールでも動作は変わらない (劣化モード)。

重要な設計上の決断:
  - 検出は「重ならない区間の集合」を返す。重複は信頼度と区間長で解決する。
  - 各検出は confidence を持つ。mask 側が閾値で採否を決められる。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from . import validators as V
from .words import (
    ADDRESS_TERMINATORS,
    CONTEXT_KEYWORDS,
    HONORIFICS,
    ORG_FALSE_POSITIVES,
    ORG_LEGAL_FORMS,
    ORG_PREFIX_FORMS,
    PREFECTURES,
    SURNAME_FALSE_POSITIVES,
    SURNAMES,
)

__all__ = ["ENTITY_TYPES", "TYPE_LABELS", "Detector", "Entity"]

log = logging.getLogger("jp_pii_mask.detect")

# ---------------------------------------------------------------------------
# エンティティ種別
# ---------------------------------------------------------------------------
ENTITY_TYPES: tuple[str, ...] = (
    "PERSON",
    "ORGANIZATION",
    "ADDRESS",
    "PHONE",
    "EMAIL",
    "POSTAL_CODE",
    "MYNUMBER",
    "CORPORATE_NUMBER",
    "MYNUMBER_CARD",
    "BANK_ACCOUNT",
    "CREDIT_CARD",
    "DRIVER_LICENSE",
    "PASSPORT",
    "PENSION",
    "INSURANCE",
    "DATE_OF_BIRTH",
    "IP_ADDRESS",
    "POSTAL_ADDRESS_FULL",
    "API_KEY",
)

TYPE_LABELS: dict[str, str] = {
    "PERSON": "氏名",
    "ORGANIZATION": "法人名",
    "ADDRESS": "住所",
    "PHONE": "電話番号",
    "EMAIL": "メールアドレス",
    "POSTAL_CODE": "郵便番号",
    "MYNUMBER": "マイナンバー",
    "CORPORATE_NUMBER": "法人番号",
    "MYNUMBER_CARD": "マイナンバーカード番号",
    "BANK_ACCOUNT": "銀行口座番号",
    "CREDIT_CARD": "クレジットカード番号",
    "DRIVER_LICENSE": "運転免許証番号",
    "PASSPORT": "パスポート番号",
    "PENSION": "基礎年金番号",
    "INSURANCE": "保険証番号",
    "DATE_OF_BIRTH": "生年月日",
    "IP_ADDRESS": "IPアドレス",
    "POSTAL_ADDRESS_FULL": "住所",
    "API_KEY": "APIキー",
}


@dataclass(slots=True)
class Entity:
    """1 件の検出結果。

    start/end は Python の文字列スライス境界 (文字数、バイトではない)。
    """

    type: str
    start: int
    end: int
    text: str
    confidence: float = 0.8
    source: str = "rule"

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: Entity) -> bool:
        return self.start < other.end and other.start < self.end

    def as_dict(self) -> dict:
        return {
            "type": self.type,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "confidence": round(self.confidence, 3),
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# 正規表現
# ---------------------------------------------------------------------------
_DIGIT = r"[0-9０-９]"
_HYPHEN = r"[-‐‑‒–—―ー－―\s]"

RE_POSTAL = re.compile(
    r"〒?\s*(\d{3})\s*[-‐‑‒–—―ー－]\s*(\d{4})"
)
RE_POSTAL_PLAIN = re.compile(r"(?<!\d)(\d{3})(\d{4})(?!\d)")

RE_PHONE = re.compile(
    r"(?<![\d０-９])"
    r"(0(?:\d|[-‐‑‒–—―ー－()（）\s]){9,16})"
    r"(?![\d０-９])"
)

RE_EMAIL = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)

RE_MYNUMBER = re.compile(r"(?<![\d０-９])((?:\d[ \u3000\-]?){11}\d)(?![\d０-９])")
RE_CORP_NUMBER = re.compile(r"(?<![\d０-９])((?:\d[ \u3000\-]?){12}\d)(?![\d０-９])")

RE_CREDIT_CARD = re.compile(
    r"(?<![\d０-９])((?:\d{4}[ \u3000\-]?){3}\d{1,4}|\d{13,19})(?![\d０-９])"
)

# 生年月日: 1990年1月1日 / 平成2年1月1日 / 1990/01/01
RE_DATE_ERA = re.compile(
    r"(明治|大正|昭和|平成|令和)\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
RE_DATE_WESTERN = re.compile(
    r"(19|20)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
)
RE_DATE_SLASH = re.compile(r"(?<!\d)((?:19|20)\d{2})[/\-](\d{1,2})[/\-](\d{1,2})(?!\d)")

# IP アドレス (v4)
RE_IPV4 = re.compile(
    r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])"
)

# 免許証: 12 桁 (公安委員会コード 2 桁 + 8 桁 + 検査 2 桁)
RE_DRIVER_LICENSE = re.compile(r"(?<!\d)(\d{12})(?!\d)")

# パスポート: 2 英字 + 7 桁
RE_PASSPORT = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2}\d{7})(?![A-Za-z0-9])")

# 基礎年金番号: 4 桁 + 区切り + 6 桁
RE_PENSION = re.compile(r"(?<!\d)(\d{4})[-‐‑‒–—―ー－]?(\d{6})(?!\d)")

# 銀行口座: 「普通 1234567」のように種別語 + 7 桁程度
RE_BANK_ACCOUNT = re.compile(
    r"(?:普通|当座|貯蓄|定期|口座|ゆうちょ)"
    r"[\s:：]*"
    r"(\d{6,8})"
)

# API キー: sk-..., AIza..., 40 文字前後の英数
RE_API_KEY = re.compile(
    r"(?:sk-(?:proj-)?[A-Za-z0-9_\-]{16,}|"
    r"sk-ant-[A-Za-z0-9_\-]{16,}|"
    r"AIza[A-Za-z0-9_\-]{30,}|"
    r"gh[pousr]_[A-Za-z0-9]{30,}|"
    r"xox[baprs]-[A-Za-z0-9\-]{10,}|"
    r"hf_[A-Za-z0-9]{20,}|"
    r"glpat-[A-Za-z0-9_\-]{16,})"
)

# 住所: 都道府県 + 続き
RE_PREFECTURE = re.compile("|".join(re.escape(p) for p in PREFECTURES))

# 法人: 法人格 + 名称 / 名称 + 法人格
_RE_ORG_STOP = r"[\s、。，．,.\n\r\t　!?！？;；:：()（）「」『』【】<>〈〉\"'’“”]"


class _OrgPatterns:
    """法人名の正規表現を起動時に一度だけ構築する。"""

    # 名称として扱わない文字 (空白・約物)
    _EXCL = r"\s、。，．,.\n\r\t　!?！？;；:：()（）「」『』【】<>〈〉\"'’“”"

    # 後置形の名称から除外する助詞。
    # 「取引先はサンプル工業株式会社」のような文で、名称を
    # 「取引先はサンプル工業」と過剰に取ってしまうのを防ぐ。
    _PARTICLES = "はがをにへ"

    def __init__(self) -> None:
        legal = "|".join(re.escape(x) for x in ORG_LEGAL_FORMS + ORG_PREFIX_FORMS)
        # 前置形: 「株式会社サンプル商事」。
        # 法人格の直前が単語構成文字でないことを要求する。
        # これが無いと「サンプル工業株式会社」の「株式会社」を前置形と誤認する。
        self.prefix = re.compile(rf"(?<!\w)({legal})\s*([^{self._EXCL}]{{1,24}})")
        # 後置形: 「サンプル工業株式会社」。
        self.suffix = re.compile(
            rf"([^{self._EXCL}{self._PARTICLES}]{{1,24}}?)\s*(?:{legal})"
        )


# 前置形の名称に紛れ込む「文の続き」を切り落とすための目印。
# 例: 「株式会社サンプル商事に勤めています」-> 「サンプル商事」
_ORG_CONTINUATION = re.compile(
    r"(?:です|ます|でした|ました|します|した|して|する|など|から|まで|という|により|による|"
    r"として|について|[はがをにへも](?=[\u4e00-\u9fff\u30a0-\u30ff]))"
)


_ORGS = _OrgPatterns()


def _normalize(text: str) -> str:
    """比較用の正規化 (NFKC + 小文字化)。位置は変えない。

    NFKC は文字数を変えうるので、位置を壊さないよう 1 文字ずつ正規化する。
    """
    out = []
    for ch in text:
        n = unicodedata.normalize("NFKC", ch)
        out.append(n if len(n) == 1 else ch)
    return "".join(out)


def _has_context(text: str, start: int, end: int, kind: str, window: int = 12) -> bool:
    """検出箇所の前後に文脈キーワードがあるか。"""
    kws = CONTEXT_KEYWORDS.get(kind, ())
    if not kws:
        return False
    left = text[max(0, start - window):start]
    right = text[end:end + window]
    around = left + right
    return any(kw in around for kw in kws)


class Detector:
    """日本語 PII 検出器。

    Parameters
    ----------
    use_ner:
        GiNZA 等の NER を使うか。利用可能な場合のみ有効。
    min_confidence:
        この値未満の検出を捨てる。
    enabled_types:
        指定するとその種別のみ検出する。
    """

    def __init__(
        self,
        use_ner: bool = False,
        min_confidence: float = 0.5,
        enabled_types: Sequence[str] | None = None,
    ) -> None:
        self.use_ner = use_ner
        self.min_confidence = min_confidence
        self.enabled_types = set(enabled_types) if enabled_types else None
        self._nlp = None
        self._surname_set = set(SURNAMES)
        self._max_surname_len = max(len(s) for s in SURNAMES)

        if use_ner:
            self._nlp = self._load_ner()

    # -- NER (任意) --------------------------------------------------------
    @staticmethod
    def _load_ner():
        """GiNZA / spaCy を読み込む。無ければ None を返し劣化モードで動く。"""
        try:
            import spacy  # type: ignore
        except ImportError:
            return None
        for model in ("ja_ginza", "ja_core_news_lg", "ja_core_news_md", "ja_core_news_sm"):
            try:
                return spacy.load(model)
            except Exception as exc:
                log.debug("NER モデル %s を読み込めませんでした: %s", model, exc)
                continue
        return None

    @property
    def ner_available(self) -> bool:
        return self._nlp is not None

    # -- 公開 API ---------------------------------------------------------
    def detect(self, text: str) -> list[Entity]:
        """テキストから PII を検出し、重なりのない確定リストを返す。"""
        if not text:
            return []

        candidates: list[Entity] = []
        candidates.extend(self._detect_patterns(text))
        candidates.extend(self._detect_postal_addresses(text))
        candidates.extend(self._detect_addresses(text))
        candidates.extend(self._detect_organizations(text))
        candidates.extend(self._detect_persons(text))
        if self._nlp is not None:
            candidates.extend(self._detect_ner(text))

        resolved = self._resolve_overlaps(candidates)
        kept = [e for e in resolved if e.confidence >= self.min_confidence]
        if self.enabled_types is not None:
            kept = [e for e in kept if e.type in self.enabled_types]
        return kept

    def detect_as_dicts(self, text: str) -> list[dict]:
        return [e.as_dict() for e in self.detect(text)]

    # -- 第 1 層: 検証付きパターン ----------------------------------------
    def _detect_patterns(self, text: str) -> Iterator[Entity]:
        # メール
        for m in RE_EMAIL.finditer(text):
            yield Entity("EMAIL", m.start(), m.end(), m.group(0), 0.99, "regex")

        # API キー
        for m in RE_API_KEY.finditer(text):
            yield Entity("API_KEY", m.start(), m.end(), m.group(0), 0.95, "regex")

        # マイナンバー (12 桁 + 検査用数字)
        for m in RE_MYNUMBER.finditer(text):
            raw = m.group(1)
            if V.is_my_number(raw):
                conf = 0.98 if _has_context(text, m.start(), m.end(), "MYNUMBER") else 0.93
                yield Entity("MYNUMBER", m.start(), m.end(), raw, conf, "checksum")

        # 法人番号 (13 桁 + 検査用数字)
        for m in RE_CORP_NUMBER.finditer(text):
            raw = m.group(1)
            if V.is_corporate_number(raw):
                conf = 0.98 if _has_context(text, m.start(), m.end(), "CORPORATE_NUMBER") else 0.93
                yield Entity("CORPORATE_NUMBER", m.start(), m.end(), raw, conf, "checksum")

        # クレジットカード (Luhn)
        for m in RE_CREDIT_CARD.finditer(text):
            raw = m.group(1)
            if V.is_luhn(raw):
                conf = 0.97 if _has_context(text, m.start(), m.end(), "CREDIT_CARD") else 0.88
                yield Entity("CREDIT_CARD", m.start(), m.end(), raw, conf, "checksum")

        # 電話番号
        for m in RE_PHONE.finditer(text):
            raw = m.group(1)
            digits = V.normalize_digits(raw)
            if not V.is_japanese_phone(raw):
                continue
            if len(set(digits)) <= 2:
                continue
            ctx = _has_context(text, m.start(), m.end(), "PHONE")
            conf = 0.95 if ctx else 0.82
            if len(digits) == 11 and digits[1] in "789":
                conf = max(conf, 0.9 if not ctx else 0.96)
            if not ctx and len(digits) == 10:
                # 固定電話は 10 桁の数字列が偶然並ぶ可能性があるため少し下げる
                conf = 0.78
            yield Entity("PHONE", m.start(), m.end(), raw, conf, "regex")

        # 郵便番号 (〒 または ハイフン付き)
        for m in RE_POSTAL.finditer(text):
            conf = 0.97 if "〒" in m.group(0) else 0.9
            if _has_context(text, m.start(), m.end(), "POSTAL"):
                conf = max(conf, 0.96)
            yield Entity("POSTAL_CODE", m.start(), m.end(), m.group(0), conf, "regex")

        # パスポート
        for m in RE_PASSPORT.finditer(text):
            ctx = _has_context(text, m.start(), m.end(), "PASSPORT")
            yield Entity("PASSPORT", m.start(), m.end(), m.group(1), 0.9 if ctx else 0.6, "regex")

        # 生年月日
        for pat, conf in ((RE_DATE_ERA, 0.92), (RE_DATE_WESTERN, 0.9)):
            for m in pat.finditer(text):
                ctx = _has_context(text, m.start(), m.end(), "PERSON")
                yield Entity(
                    "DATE_OF_BIRTH", m.start(), m.end(), m.group(0),
                    conf if not ctx else min(0.96, conf + 0.04), "regex",
                )

        # IP アドレス
        for m in RE_IPV4.finditer(text):
            octets = m.group(1).split(".")
            if all(0 <= int(o) <= 255 for o in octets) and len(octets) == 4:
                # ループバック等、マスク不要なアドレスの除外。
                # ここでの "0.0.0.0" はバインド先ではなくアドレス文字列であり、
                # S104 (all interfaces へのバインド) は誤検知。
                if m.group(1) in ("0.0.0.0", "127.0.0.1", "255.255.255.255"):  # noqa: S104
                    continue
                yield Entity("IP_ADDRESS", m.start(), m.end(), m.group(1), 0.75, "regex")

        # 銀行口座 (文脈語 + 数字)
        for m in RE_BANK_ACCOUNT.finditer(text):
            yield Entity("BANK_ACCOUNT", m.start(), m.end(), m.group(0), 0.7, "regex")

        # 基礎年金番号 (文脈必須: 数字 10 桁は珍しくないため)
        for m in RE_PENSION.finditer(text):
            if _has_context(text, m.start(), m.end(), "PENSION"):
                yield Entity("PENSION", m.start(), m.end(), m.group(0), 0.88, "regex")

        # 免許証番号 (12 桁・文脈必須)
        for m in RE_DRIVER_LICENSE.finditer(text):
            if _has_context(text, m.start(), m.end(), "DRIVER_LICENSE"):
                yield Entity(
                    "DRIVER_LICENSE", m.start(), m.end(), m.group(1), 0.85, "regex",
                )

    # -- 郵便番号 + 住所の複合 -------------------------------------------
    def _detect_postal_addresses(self, text: str) -> Iterator[Entity]:
        """「〒123-4567 東京都…」を 1 エンティティとしてまとめる。

        郵便番号と住所を別々に置換しても復元は可能だが、
        1 つにまとめた方が LLM への文脈情報が保たれやすい。
        """
        for m in RE_POSTAL.finditer(text):
            if "〒" not in m.group(0):
                continue
            addr_start = m.end()
            # 郵便番号の直後は空白や改行を挟むことが多い
            while addr_start < len(text) and text[addr_start] in " 　\t":
                addr_start += 1
            m2 = RE_PREFECTURE.match(text, addr_start)
            if not m2:
                continue
            end = self._extend_address(text, m2.start())
            yield Entity(
                "POSTAL_ADDRESS_FULL", m.start(), end, text[m.start():end], 0.96, "composite",
            )

    # -- 第 2 層: 住所 -----------------------------------------------------
    def _extend_address(self, text: str, start: int) -> int:
        """住所本体の終端を推定する。"""
        i = start
        n = len(text)
        while i < n:
            ch = text[i]
            if ch in ADDRESS_TERMINATORS:
                break
            # 全角カナ・漢字・数字・ハイフン・ヶ などは住所の一部
            code = ord(ch)
            is_body = (
                0x3040 <= code <= 0x30FF      # かな・カナ
                or 0x4E00 <= code <= 0x9FFF   # 漢字
                or 0x30 <= code <= 0x39       # 0-9
                or 0xFF10 <= code <= 0xFF19   # ０-９
                or ch in "－ー-‐‑–—― 　丁目番地号棟階室条線ケヶが"
            )
            if not is_body:
                break
            i += 1
        # 末尾の助詞・記号を削る
        while i > start and text[i - 1] in "のをにへとがはでも 　":
            i -= 1
        return i

    def _detect_addresses(self, text: str) -> Iterator[Entity]:
        for m in RE_PREFECTURE.finditer(text):
            end = self._extend_address(text, m.start())
            if end - m.start() < 3:
                # 「東京都」だけでは住所として弱い。文脈があれば許可。
                if not _has_context(text, m.start(), end, "ADDRESS"):
                    continue
                yield Entity("ADDRESS", m.start(), end, text[m.start():end], 0.62, "dict")
                continue
            conf = 0.9
            if _has_context(text, m.start(), end, "ADDRESS"):
                conf = 0.95
            yield Entity("ADDRESS", m.start(), end, text[m.start():end], conf, "dict")

    # -- 第 2 層: 法人 -----------------------------------------------------
    def _detect_organizations(self, text: str) -> Iterator[Entity]:
        seen: set[tuple[int, int]] = set()

        def emit(start: int, end: int, name: str) -> Entity | None:
            if len(name) < 2 or name in ORG_FALSE_POSITIVES:
                return None
            if (start, end) in seen:
                return None
            seen.add((start, end))
            ctx = _has_context(text, start, end, "ORGANIZATION")
            conf = 0.9 if not ctx else 0.95
            if len(name) <= 2:
                conf -= 0.1
            return Entity("ORGANIZATION", start, end, text[start:end], conf, "dict")

        # 前置形: 「株式会社サンプル商事」
        for m in _ORGS.prefix.finditer(text):
            name = m.group(2)
            name_start = m.start(2)
            cut = _ORG_CONTINUATION.search(name)
            if cut:
                name = name[: cut.start()]
            ent = emit(m.start(1), name_start + len(name), name)
            if ent is not None:
                yield ent

        # 後置形: 「サンプル工業株式会社」
        for m in _ORGS.suffix.finditer(text):
            name = m.group(1)
            ent = emit(m.start(1), m.end(), name)
            if ent is not None:
                yield ent

    # -- 第 2 層: 氏名 -----------------------------------------------------
    @staticmethod
    def _is_name_char(ch: str) -> bool:
        """氏名の名の部分になりうる文字 (漢字・カタカナ)。

        ひらがなを含めないのは重要。含めると「田中太郎と申します」の
        「と」を名として取り込み、人名検出が壊れる。
        """
        code = ord(ch)
        return (
            0x4E00 <= code <= 0x9FFF      # 漢字
            or 0x3400 <= code <= 0x4DBF   # 漢字 (拡張 A)
            or ch == "々"
            or 0x30A1 <= code <= 0x30FA   # カタカナ
        )

    @staticmethod
    def _is_kana(ch: str) -> bool:
        """ひらがな・カタカナ。"""
        code = ord(ch)
        return 0x3041 <= code <= 0x309F or 0x30A1 <= code <= 0x30FA

    def _detect_persons(self, text: str) -> Iterator[Entity]:
        n = len(text)
        i = 0
        while i < n:
            matched = None
            # 長い姓から試す (「佐々木」を「佐」より先に)
            for length in range(min(self._max_surname_len, n - i), 0, -1):
                cand = text[i:i + length]
                if cand in self._surname_set:
                    matched = cand
                    break
            if matched is None:
                i += 1
                continue

            end = i + len(matched)
            # 直後に敬称があれば姓だけで人名として確定できる
            honorific_len = 0
            for h in HONORIFICS:
                if text.startswith(h, end):
                    honorific_len = len(h)
                    break

            if honorific_len:
                conf = 0.88
                if matched in SURNAME_FALSE_POSITIVES and len(matched) <= 2:
                    conf -= 0.25
                yield Entity("PERSON", i, end, text[i:end], conf, "dict")
                i = end + honorific_len
                continue

            # 直後に続く名の部分 (漢字・カタカナ 1-4 文字)
            j = end
            while j < n and (j - end) < 4 and self._is_name_char(text[j]):
                j += 1
            name_len = j - end

            if 1 <= name_len <= 4:
                if matched in SURNAME_FALSE_POSITIVES and len(matched) <= 2:
                    i += 1
                    continue
                if len(matched) == 1 and name_len == 1:
                    i += 1
                    continue
                ctx = _has_context(text, i, j, "PERSON")
                conf = 0.86 if not ctx else 0.93
                if len(matched) == 1:
                    conf -= 0.12
                if conf >= self.min_confidence:
                    yield Entity("PERSON", i, j, text[i:j], conf, "dict")
                i = j
                continue

            # かなの名 (「鈴木さくらさん」)。
            # かな連続は敬称を飲み込んでしまうため、「敬称の直前まで」を名とする。
            k = end
            while k < n and (k - end) < 8 and self._is_kana(text[k]):
                k += 1
            if k > end:
                found: tuple[int, str] | None = None
                for j in range(end + 1, k + 1):
                    for h in HONORIFICS:
                        if text.startswith(h, j):
                            found = (j, h)
                            break
                    if found:
                        break
                if found:
                    j, h = found
                    yield Entity("PERSON", i, j, text[i:j], 0.86, "dict")
                    i = j + len(h)
                    continue
                kana_len = k - end
                if 2 <= kana_len <= 5 and _has_context(text, i, k, "PERSON"):
                    yield Entity("PERSON", i, k, text[i:k], 0.7, "dict")
                    i = k
                    continue

            i = end

    # -- 第 3 層: NER ------------------------------------------------------
    def _detect_ner(self, text: str) -> Iterator[Entity]:
        assert self._nlp is not None
        try:
            doc = self._nlp(text)
        except Exception as exc:
            # NER の失敗は「取りこぼし」を意味する。黙殺せず記録する。
            log.warning("NER の推論に失敗しました (このテキストは NER なしで処理します): %s", exc)
            return
        mapping = {
            "Person": "PERSON",
            "PERSON": "PERSON",
            "ORG": "ORGANIZATION",
            "Organization": "ORGANIZATION",
            "GPE": "ADDRESS",
            "LOC": "ADDRESS",
            "Facility": "ADDRESS",
            "Address": "ADDRESS",
        }
        for ent in getattr(doc, "ents", []):
            etype = mapping.get(ent.label_)
            if not etype:
                continue
            start, end = ent.start_char, ent.end_char
            if end <= start:
                continue
            yield Entity(etype, start, end, text[start:end], 0.72, "ner")

    # -- 重複解決 ---------------------------------------------------------
    def _resolve_overlaps(self, entities: Iterable[Entity]) -> list[Entity]:
        """重なりを解消して確定リストを返す。

        優先順位:
          1. 検出元 (composite > checksum > regex > dict > ner)
          2. confidence
          3. 区間長 (長い方を優先)
        """
        source_rank = {
            "composite": 5,
            "checksum": 4,
            "regex": 3,
            "dict": 2,
            "ner": 1,
        }

        def sort_key(e: Entity):
            return (
                source_rank.get(e.source, 0),
                e.confidence,
                e.length,
            )

        ordered = sorted(entities, key=sort_key, reverse=True)
        kept: list[Entity] = []
        for cand in ordered:
            if any(cand.overlaps(k) for k in kept):
                continue
            kept.append(cand)
        kept.sort(key=lambda e: e.start)
        return kept
