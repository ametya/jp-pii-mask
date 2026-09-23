"""検査用数字 (チェックディジット) のテスト。"""

from jp_pii_mask.validators import (
    is_corporate_number,
    is_japanese_phone,
    is_luhn,
    is_my_number,
    normalize_digits,
)


def my_number_check_digit(base11: str) -> str:
    """総務省令の方式で検査用数字を計算する (テスト用の独立実装)。"""
    total = 0
    for n in range(1, 12):
        q = int(base11[11 - n])
        p = n + 1 if n <= 6 else n - 5
        total += p * q
    d = 11 - (total % 11)
    return str(0 if d >= 10 else d)


def corp_number_check_digit(base12: str) -> str:
    total = 0
    for n in range(1, 13):
        q = int(base12[12 - n])
        p = 1 if n % 2 == 0 else 2
        total += p * q
    return str(9 - (total % 9))


class TestMyNumber:
    def test_accepts_valid(self):
        base = "12345678901"
        value = base + my_number_check_digit(base)
        assert is_my_number(value), value

    def test_rejects_wrong_check_digit(self):
        base = "12345678901"
        good = my_number_check_digit(base)
        bad = str((int(good) + 1) % 10)
        assert not is_my_number(base + bad)

    def test_rejects_when_check_is_10_mapped_to_0(self):
        # 検査用数字が 10 になる場合は 0 として扱う
        for base in ("00000000000", "11111111111", "98765432109"):
            cd = my_number_check_digit(base)
            assert is_my_number(base + cd), base

    def test_rejects_wrong_length(self):
        assert not is_my_number("1234567890")
        assert not is_my_number("1234567890123")

    def test_accepts_fullwidth(self):
        base = "12345678901"
        value = base + my_number_check_digit(base)
        full = "".join(chr(ord(c) - ord("0") + ord("０")) for c in value)
        assert is_my_number(full)


class TestCorporateNumber:
    def test_accepts_valid(self):
        base = "234567890123"
        value = corp_number_check_digit(base) + base
        assert is_corporate_number(value), value

    def test_rejects_wrong_check_digit(self):
        base = "234567890123"
        good = corp_number_check_digit(base)
        bad = str((int(good) + 1) % 9)
        assert not is_corporate_number(bad + base)

    def test_rejects_wrong_length(self):
        assert not is_corporate_number("123456789012")


class TestLuhn:
    def test_accepts_known_test_numbers(self):
        # 決済系で広く使われる公開テスト番号
        assert is_luhn("4242424242424242")
        assert is_luhn("4111111111111111")
        assert is_luhn("5555555555554444")

    def test_rejects_invalid(self):
        assert not is_luhn("4242424242424243")
        assert not is_luhn("1234567890123456")

    def test_accepts_grouped_with_spaces(self):
        assert is_luhn("4242 4242 4242 4242")
        assert is_luhn("4242-4242-4242-4242")


class TestPhone:
    def test_accepts_mobile(self):
        assert is_japanese_phone("090-1234-5678")
        assert is_japanese_phone("08012345678")

    def test_accepts_fixed(self):
        assert is_japanese_phone("03-1234-5678")
        assert is_japanese_phone("0120-123-456")

    def test_rejects_non_japanese(self):
        assert not is_japanese_phone("1234567890")
        assert not is_japanese_phone("+1-555-123-4567")

    def test_rejects_repeated_digits(self):
        assert not is_japanese_phone("000-0000-0000")


class TestNormalize:
    def test_strips_separators(self):
        assert normalize_digits("090-1234-5678") == "09012345678"
        assert normalize_digits("０９０ １２３４ ５６７８") == "09012345678"
