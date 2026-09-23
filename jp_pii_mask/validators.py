"""日本の公的番号の検査用数字 (チェックディジット) 検証。

これらは「12桁の数字」等の緩いパターンから本物らしさを判定するために使う。
検査用数字が一致する確率は偶然には低いので、誤検出を大幅に削減できる。
"""

from __future__ import annotations


def _digits(value: str) -> str:
    """全角数字・ハイフン・空白を除去して半角数字列にする。"""
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if 0x30 <= code <= 0x39:            # 0-9
            out.append(ch)
        elif 0xFF10 <= code <= 0xFF19:      # ０-９
            out.append(chr(code - 0xFF10 + 0x30))
        # ハイフン・空白類・その他は無視
    return "".join(out)


def normalize_digits(value: str) -> str:
    return _digits(value)


def is_my_number(value: str) -> bool:
    """マイナンバー (個人番号) の 12 桁検査。

    総務省令で定める方式:
      Q_n : 基礎番号 (下 11 桁) の右から n 番目の数字
      P_n : n <= 6 なら n+1、n >= 7 なら n-5
      検査用数字 = 11 - (Σ P_n*Q_n mod 11)、結果が 10 なら 0
    """
    d = _digits(value)
    if len(d) != 12:
        return False
    base = d[:11]
    check = int(d[11])
    total = 0
    for n in range(1, 12):
        q = int(base[11 - n])          # 右から n 番目
        p = n + 1 if n <= 6 else n - 5
        total += p * q
    expected = 11 - (total % 11)
    if expected >= 10:
        expected = 0
    return check == expected


def is_corporate_number(value: str) -> bool:
    """法人番号の 13 桁検査。

    先頭 1 桁が検査用数字、残り 12 桁が基礎番号。
      Q_n : 基礎番号の右から n 番目の数字
      P_n : n が偶数なら 1、奇数なら 2
      検査用数字 = 9 - (Σ P_n*Q_n mod 9)
    """
    d = _digits(value)
    if len(d) != 13:
        return False
    check = int(d[0])
    base = d[1:]
    total = 0
    for n in range(1, 13):
        q = int(base[12 - n])
        p = 1 if n % 2 == 0 else 2
        total += p * q
    expected = 9 - (total % 9)
    return check == expected


def is_luhn(value: str) -> bool:
    """クレジットカード番号等の Luhn 検査。"""
    d = _digits(value)
    if not (12 <= len(d) <= 19):
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def is_japanese_phone(value: str) -> bool:
    """日本の電話番号として妥当か (桁数と先頭 0 の検査)。

    0 で始まる 10 桁 (固定) または 11 桁 (携帯・IP) を対象とする。
    ただし 0AB-J の携帯 11 桁は 070/080/090 始まりに限定して誤検出を減らす。
    """
    d = _digits(value)
    if not d.startswith("0"):
        return False
    # 0000 等の連番は電話番号らしくない。
    if len(set(d)) == 1:
        return False
    if len(d) == 11 and d.startswith("0120"):
        return True
    if len(d) == 11 and d.startswith("0800"):
        return True
    if len(d) == 11 and d.startswith("0570"):
        return True
    if len(d) == 10:
        # 固定電話・IP 電話。市外局番は 0 始まりの 1-5 桁。
        return True
    if len(d) == 11:
        # 携帯・PHS は 070/080/090 始まりの 11 桁。
        return d[1] in "789"
    return False


def is_my_number_card_number(value: str) -> bool:
    return is_my_number(value)
