"""Vault とマスキング／復元のテスト。"""

import json
import os
import tempfile

import pytest

from jp_pii_mask.detect import Detector, Entity
from jp_pii_mask.masking import Masker, StreamRestorer, mask_text, unmask_text
from jp_pii_mask.vault import PLACEHOLDER_RE, SessionVaults, Vault


@pytest.fixture()
def vault() -> Vault:
    return Vault(encrypt=False)


@pytest.fixture()
def masker() -> Masker:
    return Masker(detector=Detector(min_confidence=0.5))


class TestVaultBasics:
    def test_deterministic_placeholder(self, vault):
        a = vault.placeholder_for("田中太郎", "PERSON")
        b = vault.placeholder_for("田中太郎", "PERSON")
        assert a == b
        assert a == "[氏名_1]"

    def test_distinct_originals_get_distinct_placeholders(self, vault):
        a = vault.placeholder_for("田中太郎", "PERSON")
        b = vault.placeholder_for("佐藤花子", "PERSON")
        assert a != b
        assert b == "[氏名_2]"

    def test_same_text_different_types(self, vault):
        # 同じ文字列でも種別が違えば別エントリ
        a = vault.placeholder_for("サンプル", "ORGANIZATION")
        b = vault.placeholder_for("サンプル", "CUSTOM")
        assert a != b

    def test_reverse_lookup(self, vault):
        ph = vault.placeholder_for("090-1234-5678", "PHONE")
        assert vault.original_for(ph) == "090-1234-5678"

    def test_unknown_placeholder_returns_none(self, vault):
        assert vault.original_for("[氏名_99]") is None

    def test_lru_eviction(self):
        v = Vault(encrypt=False, max_entries=3)
        for i in range(5):
            v.placeholder_for(f"名前{i}", "PERSON")
        assert len(v) == 3
        # 直近のものは残っている
        assert v.original_for("[氏名_5]") == "名前4"

    def test_clear(self, vault):
        vault.placeholder_for("田中太郎", "PERSON")
        vault.clear()
        assert len(vault) == 0


class TestVaultPersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s1.jpvault")
            v1 = Vault(session_id="s1", store_path=path, encrypt=True)
            ph = v1.placeholder_for("田中太郎", "PERSON")
            v1.save()
            assert os.path.exists(path)

            v2 = Vault(session_id="s1", store_path=path, encrypt=True)
            assert v2.load() is True
            assert v2.original_for(ph) == "田中太郎"
            # 継続して新しい項目を足しても番号が衝突しない
            ph2 = v2.placeholder_for("佐藤花子", "PERSON")
            assert ph2 != ph

    def test_saved_file_is_not_plaintext(self):
        """保存ファイルに原文が平文で現れないことを確認する (暗号化の実効性)。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s1.jpvault")
            v = Vault(session_id="s1", store_path=path, encrypt=True)
            v.placeholder_for("田中太郎", "PERSON")
            v.placeholder_for("090-1234-5678", "PHONE")
            v.save()
            blob = open(path, "rb").read()
            assert "田中太郎".encode("utf-8") not in blob
            assert "090-1234-5678".encode("utf-8") not in blob

    def test_key_file_permissions_are_restrictive(self):
        if os.name == "nt":
            pytest.skip("POSIX 権限は Windows では検証しない")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s1.jpvault")
            v = Vault(session_id="s1", store_path=path, encrypt=True)
            v.placeholder_for("田中太郎", "PERSON")
            v.save()
            mode = os.stat(v.key_path).st_mode & 0o777
            assert mode == 0o600, oct(mode)

    def test_encrypted_roundtrip_many_entries(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s1.jpvault")
            v = Vault(session_id="s1", store_path=path, encrypt=True)
            originals = [f"テスト{i}株式会社" for i in range(50)]
            placeholders = [v.placeholder_for(o, "ORGANIZATION") for o in originals]
            v.save()
            v2 = Vault(session_id="s1", store_path=path, encrypt=True)
            v2.load()
            for ph, orig in zip(placeholders, originals):
                assert v2.original_for(ph) == orig


class TestSessionVaults:
    def test_sessions_are_isolated(self):
        sv = SessionVaults(root=None, persist=False)
        v1 = sv.get("session-a")
        v2 = sv.get("session-b")
        v1.placeholder_for("田中太郎", "PERSON")
        assert len(v2) == 0

    def test_persistence_across_instances(self):
        with tempfile.TemporaryDirectory() as d:
            sv1 = SessionVaults(root=d, persist=True)
            v = sv1.get("s1")
            ph = v.placeholder_for("田中太郎", "PERSON")
            sv1.save_all()

            sv2 = SessionVaults(root=d, persist=True)
            v2 = sv2.get("s1")
            assert v2.original_for(ph) == "田中太郎"


class TestMaskUnmaskRoundtrip:
    def test_simple_roundtrip(self, vault, masker):
        text = "田中太郎の携帯は090-1234-5678です。"
        res = masker.mask(text, vault)
        assert "田中太郎" not in res.text
        assert "090-1234-5678" not in res.text
        assert masker.unmask(res.text, vault).text == text

    def test_complex_document_roundtrip(self, vault, masker):
        text = (
            "お世話になっております。株式会社サンプル商事 営業部の田中太郎です。\n"
            "私の携帯は090-1234-5678、メールは taro.tanaka@example.co.jp です。\n"
            "請求書の送付先は〒100-0001 東京都千代田区千代田1-1 サンプルビル5階です。\n"
            "マイナンバーは 123456789018 です。"
        )
        res = masker.mask(text, vault)
        assert res.count >= 5
        assert "taro.tanaka@example.co.jp" not in res.text
        restored = masker.unmask(res.text, vault)
        assert restored.text == text
        assert len(restored.missing) == 0

    def test_repeated_entity_uses_same_placeholder(self, vault, masker):
        text = "田中太郎です。田中太郎は営業部です。"
        res = masker.mask(text, vault)
        assert res.text.count("[氏名_1]") == 2
        assert masker.unmask(res.text, vault).text == text

    def test_placeholders_are_human_readable(self, vault, masker):
        res = masker.mask("090-1234-5678", vault)
        assert res.text == "[電話番号_1]"

    def test_no_pii_returns_unchanged(self, vault, masker):
        text = "本日の会議は10時からです。"
        res = masker.mask(text, vault)
        assert res.text == text
        assert res.count == 0

    def test_empty_string(self, vault, masker):
        res = masker.mask("", vault)
        assert res.text == ""

    def test_redact_only_mode(self, vault):
        m = Masker(detector=Detector(min_confidence=0.5), redact_only=True)
        res = m.mask("田中太郎の携帯は090-1234-5678です。", vault)
        assert "田中太郎" not in res.text
        assert "[REDACTED]" in res.text
        # 消去モードでは復元できない (設計通り)
        assert m.unmask(res.text, vault).text == res.text

    def test_idempotent_masking(self, vault, masker):
        """マスク済みテキストを再度マスクしても二重マスクにならない。"""
        text = "田中太郎の携帯は090-1234-5678です。"
        once = masker.mask(text, vault).text
        twice = masker.mask(once, vault).text
        assert once == twice

    def test_functional_api(self, vault):
        masked, res = mask_text("田中太郎です。", vault)
        assert "田中太郎" not in masked
        assert unmask_text(masked, vault) == "田中太郎です。"


class TestUnmaskRobustness:
    """LLM がプレースホルダを多少崩しても復元できること。"""

    def test_fullwidth_brackets(self, vault, masker):
        masker.mask("田中太郎です。", vault)
        # LLM が全角括弧に変えてしまった場合
        assert masker.unmask("［氏名_1］です。", vault).text == "田中太郎です。"

    def test_missing_underscore(self, vault, masker):
        masker.mask("田中太郎です。", vault)
        assert masker.unmask("[氏名1]です。", vault).text == "田中太郎です。"

    def test_spaces_inside(self, vault, masker):
        masker.mask("田中太郎です。", vault)
        assert masker.unmask("[ 氏名 _ 1 ]です。", vault).text == "田中太郎です。"

    def test_unknown_placeholder_left_as_is(self, vault, masker):
        result = masker.unmask("[氏名_99]です。", vault)
        assert result.text == "[氏名_99]です。"
        assert result.missing == ["[氏名_99]"]

    def test_missing_reported(self, vault, masker):
        masker.mask("田中太郎です。", vault)
        result = masker.unmask("[氏名_1]と[氏名_42]です。", vault)
        assert result.restored == 1
        assert result.missing == ["[氏名_42]"]


class TestStreamRestorer:
    def test_split_across_chunks(self, vault, masker):
        """プレースホルダがチャンク境界で分割されても壊れない。"""
        masker.mask("田中太郎の携帯は090-1234-5678です。", vault)
        sr = StreamRestorer(masker, vault)

        chunks = ["[氏", "名_1]", "の携帯は[電", "話番号_1]", "です。"]
        out = "".join(sr.feed(c) for c in chunks)
        out += sr.flush()
        assert "田中太郎" in out
        assert "090-1234-5678" in out
        assert "[氏名_1]" not in out
        assert out == "田中太郎の携帯は090-1234-5678です。"

    def test_holds_incomplete_tail(self, vault, masker):
        masker.mask("田中太郎です。", vault)
        sr = StreamRestorer(masker, vault)
        # 未完のまま保持される
        partial = sr.feed("こんにちは [氏名")
        assert "[氏名" not in partial
        rest = sr.feed("_1] です。") + sr.flush()
        assert partial + rest == "こんにちは 田中太郎 です。"

    def test_plain_text_passes_through(self, vault, masker):
        sr = StreamRestorer(masker, vault)
        out = sr.feed("普通の文章です。") + sr.flush()
        assert out == "普通の文章です。"

    def test_complete_placeholder_emitted_immediately(self, vault, masker):
        masker.mask("田中太郎です。", vault)
        sr = StreamRestorer(masker, vault)
        out = sr.feed("[氏名_1]")
        assert out == "田中太郎"

    def test_multiple_placeholders_in_one_chunk(self, vault, masker):
        masker.mask("田中太郎と佐藤花子です。", vault)
        sr = StreamRestorer(masker, vault)
        out = sr.feed("[氏名_1]と[氏名_2]です。") + sr.flush()
        assert out == "田中太郎と佐藤花子です。"


class TestPlaceholderRegex:
    def test_matches_standard(self):
        assert PLACEHOLDER_RE.findall("[氏名_1]") == [("氏名", "1")]

    def test_matches_fullwidth(self):
        assert PLACEHOLDER_RE.findall("［電話番号_12］") == [("電話番号", "12")]

    def test_matches_corner_brackets(self):
        assert PLACEHOLDER_RE.findall("【法人名_3】") == [("法人名", "3")]

    def test_does_not_match_markdown_link(self):
        assert PLACEHOLDER_RE.findall("[リンク](http://x)") == []
