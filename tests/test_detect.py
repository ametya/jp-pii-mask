"""検出エンジンのテスト。

重要: 「日本語の実務文書で本当に必要なものが取れるか」と
      「普通の日本語を PII と誤認しないか」の両方を検証する。
"""

import pytest

from jp_pii_mask.detect import Detector


@pytest.fixture(scope="module")
def det() -> Detector:
    return Detector(min_confidence=0.5)


def types_in(entities) -> set[str]:
    return {e.type for e in entities}


def texts_of(entities, etype: str) -> list[str]:
    return [e.text for e in entities if e.type == etype]


class TestEmail:
    def test_basic(self, det):
        ents = det.detect("連絡先は taro.tanaka@example.co.jp です。")
        assert "EMAIL" in types_in(ents)
        assert texts_of(ents, "EMAIL") == ["taro.tanaka@example.co.jp"]

    def test_plus_addressing(self, det):
        ents = det.detect("mail: user+tag@example.com")
        assert texts_of(ents, "EMAIL") == ["user+tag@example.com"]

    def test_not_confused_by_at_in_japanese(self, det):
        ents = det.detect("価格は1000円@税込です")
        assert "EMAIL" not in types_in(ents)


class TestPostal:
    def test_with_mark(self, det):
        ents = det.detect("〒100-0001")
        assert "POSTAL_CODE" in types_in(ents)

    def test_full_postal_address_composite(self, det):
        text = "送付先は〒100-0001 東京都千代田区千代田1-1です。"
        ents = det.detect(text)
        assert "POSTAL_ADDRESS_FULL" in types_in(ents)
        val = texts_of(ents, "POSTAL_ADDRESS_FULL")[0]
        assert val.startswith("〒100-0001")
        assert "東京都千代田区千代田1-1" in val

    def test_bare_seven_digits_not_detected(self, det):
        # 検査マークも文脈語も無い 7 桁は郵便番号と断定しない
        ents = det.detect("注文番号は1000001です。")
        assert "POSTAL_CODE" not in types_in(ents)


class TestPhone:
    def test_mobile(self, det):
        ents = det.detect("携帯は090-1234-5678です。")
        assert "PHONE" in types_in(ents)

    def test_fixed_with_context(self, det):
        ents = det.detect("電話番号: 03-1234-5678")
        phones = texts_of(ents, "PHONE")
        assert any("03-1234-5678" in p for p in phones)

    def test_toll_free(self, det):
        ents = det.detect("お問い合わせは0120-123-456まで。")
        assert "PHONE" in types_in(ents)


class TestMyNumber:
    def test_detects_valid_with_context(self, det):
        # 123456789018 は検査用数字が一致する 12 桁
        from jp_pii_mask.validators import is_my_number

        assert is_my_number("123456789018"), "テストデータの前提が崩れています"
        ents = det.detect("私のマイナンバーは123456789018です。")
        assert "MYNUMBER" in types_in(ents)

    def test_ignores_invalid_twelve_digits(self, det):
        ents = det.detect("管理番号は123456789019です。")
        assert "MYNUMBER" not in types_in(ents)


class TestCorporateNumber:
    def test_detects_valid(self, det):
        from jp_pii_mask.validators import is_corporate_number

        # 検査用数字が一致する 13 桁
        candidate = None
        for second in range(10):
            for val in [f"1{second}34567890123", f"9{second}87654321098"]:
                if is_corporate_number(val):
                    candidate = val
                    break
            if candidate:
                break
        assert candidate, "テストデータを作れませんでした"
        ents = det.detect(f"法人番号は{candidate}です。")
        assert "CORPORATE_NUMBER" in types_in(ents)


class TestCreditCard:
    def test_detects_luhn_valid(self, det):
        ents = det.detect("カード番号 4242 4242 4242 4242 でお願いします。")
        assert "CREDIT_CARD" in types_in(ents)

    def test_ignores_luhn_invalid(self, det):
        ents = det.detect("カード番号 4242 4242 4242 4243 でお願いします。")
        assert "CREDIT_CARD" not in types_in(ents)


class TestPerson:
    def test_surname_given(self, det):
        ents = det.detect("田中太郎と申します。")
        assert "PERSON" in types_in(ents)
        assert "田中太郎" in texts_of(ents, "PERSON")

    def test_with_honorific(self, det):
        ents = det.detect("佐藤様、お世話になっております。")
        assert "佐藤" in texts_of(ents, "PERSON")

    def test_long_surname(self, det):
        ents = det.detect("佐々木健一です。")
        assert "佐々木健一" in texts_of(ents, "PERSON")

    def test_does_not_grab_ordinary_words(self, det):
        # 「林」「森」等は単独では人名と断定しない
        ents = det.detect("林の中で森を抜けると原っぱに出た。")
        persons = texts_of(ents, "PERSON")
        assert not any(p in ("林", "森", "原") for p in persons), persons

    def test_kana_given_name(self, det):
        ents = det.detect("鈴木さくらさんにお伝えください。")
        assert "PERSON" in types_in(ents)


class TestOrganization:
    def test_prefix_form(self, det):
        ents = det.detect("株式会社サンプル商事に勤めています。")
        orgs = texts_of(ents, "ORGANIZATION")
        assert any("サンプル商事" in o for o in orgs)

    def test_suffix_form(self, det):
        ents = det.detect("取引先はサンプル工業株式会社です。")
        orgs = texts_of(ents, "ORGANIZATION")
        assert any("サンプル工業" in o for o in orgs)

    def test_legal_form_only(self, det):
        ents = det.detect("一般社団法人日本サンプル協会が主催します。")
        orgs = texts_of(ents, "ORGANIZATION")
        assert any("日本サンプル協会" in o for o in orgs)

    def test_bare_word_not_org(self, det):
        ents = det.detect("会社の近くで昼食を食べた。")
        # 「会社」単体を法人名として検出しない
        assert not any(o == "会社" for o in texts_of(ents, "ORGANIZATION"))


class TestAddress:
    def test_prefecture_plus_body(self, det):
        ents = det.detect("住所は東京都千代田区千代田1-1です。")
        addrs = texts_of(ents, "ADDRESS")
        assert any("東京都千代田区千代田1-1" in a for a in addrs)

    def test_hokkaido(self, det):
        ents = det.detect("在住は北海道札幌市中央区北一条西2丁目です。")
        addrs = texts_of(ents, "ADDRESS")
        assert any("北海道" in a for a in addrs)

    def test_prefecture_only_requires_context(self, det):
        ents = det.detect("東京都の人口は多い。")
        # 文脈語がなければ都道府県単独は検出しない (誤検出より取りこぼしを選ぶ)
        assert not any(a == "東京都" for a in texts_of(ents, "ADDRESS"))


class TestOtherTypes:
    def test_ip_address(self, det):
        ents = det.detect("サーバは 192.168.10.24 です。")
        assert "IP_ADDRESS" in types_in(ents)

    def test_ipv4_validation(self, det):
        ents = det.detect("バージョンは 999.999.999.999 です。")
        assert "IP_ADDRESS" not in types_in(ents)

    def test_api_key(self, det):
        ents = det.detect("キーは sk-proj-abcdefghijklmnopqrstuvwx です。")
        assert "API_KEY" in types_in(ents)

    def test_api_key_anthropic(self, det):
        ents = det.detect("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwx")
        assert "API_KEY" in types_in(ents)

    def test_date_of_birth(self, det):
        ents = det.detect("生年月日は1990年1月1日です。")
        assert "DATE_OF_BIRTH" in types_in(ents)

    def test_wareki_date(self, det):
        ents = det.detect("平成2年5月3日生まれ。")
        assert "DATE_OF_BIRTH" in types_in(ents)

    def test_bank_account(self, det):
        ents = det.detect("振込先は普通 1234567 です。")
        assert "BANK_ACCOUNT" in types_in(ents)


class TestNoFalsePositives:
    def test_plain_japanese(self, det):
        text = "本日の会議は10時からです。資料を準備してください。"
        assert det.detect(text) == []

    def test_numbers_in_prose(self, det):
        text = "売上は1234567円、前年比110%でした。"
        ents = det.detect(text)
        # 金額・比率を PII と誤認しない
        assert not types_in(ents) & {"MYNUMBER", "CORPORATE_NUMBER", "CREDIT_CARD"}

    def test_version_string(self, det):
        text = "バージョン 1.2.3 をリリースしました。"
        assert "IP_ADDRESS" not in types_in(det.detect(text))

    def test_timestamp_not_carded(self, det):
        text = "作成日時: 2024-01-15 10:30:00"
        assert "CREDIT_CARD" not in types_in(det.detect(text))


class TestOverlapResolution:
    def test_postal_not_duplicated(self, det):
        text = "〒100-0001 東京都千代田区千代田1-1"
        ents = det.detect(text)
        # 複合として 1 件にまとまり、内側の POSTAL_CODE / ADDRESS と重複しない
        ranges = [(e.start, e.end) for e in ents]
        for i, (s1, e1) in enumerate(ranges):
            for s2, e2 in ranges[i + 1:]:
                assert not (s1 < e2 and s2 < e1)

    def test_entities_sorted(self, det):
        text = "田中太郎の電話は090-1234-5678、メールはa@b.co.jpです。"
        ents = det.detect(text)
        starts = [e.start for e in ents]
        assert starts == sorted(starts)


class TestBoundaryRegressions:
    """検出境界の回帰テスト (過検出・飲み込みの再発防止)。"""

    def test_postal_not_matched_inside_phone(self, det):
        ents = det.detect("電話: 03-1234-5678")
        assert texts_of(ents, "PHONE") == ["03-1234-5678"]
        assert "POSTAL_CODE" not in types_in(ents)

    def test_pension_not_eaten_by_postal(self, det):
        ents = det.detect("基礎年金番号: 1234-567890")
        assert texts_of(ents, "PENSION") == ["1234-567890"]
        assert "POSTAL_CODE" not in types_in(ents)

    def test_phone_does_not_swallow_newline(self, det):
        ents = det.detect("TEL 03-9876-5432\n次の行です。")
        assert texts_of(ents, "PHONE") == ["03-9876-5432"]

    def test_phone_strips_trailing_parens(self, det):
        ents = det.detect("佐藤花子さん（03 9999 8888）にも連絡して")
        assert texts_of(ents, "PHONE") == ["03 9999 8888"]

    def test_slash_date_with_context(self, det):
        ents = det.detect("生年月日: 1988/11/30")
        assert texts_of(ents, "DATE_OF_BIRTH") == ["1988/11/30"]

    def test_plain_western_date_without_context(self, det):
        # 文脈の無い西暦日付は生年月日と断定しない (過検出防止)
        ents = det.detect("キャンペーンは2026年10月18日までです。")
        assert "DATE_OF_BIRTH" not in types_in(ents)

    def test_mynumber_card_vs_mynumber(self, det):
        card = det.detect("マイナンバーカード番号: 526018159084")
        assert texts_of(card, "MYNUMBER_CARD") == ["526018159084"]
        assert "MYNUMBER" not in types_in(card)
        plain = det.detect("マイナンバー: 526018159084")
        assert texts_of(plain, "MYNUMBER") == ["526018159084"]
        assert "MYNUMBER_CARD" not in types_in(plain)

    def test_org_prefix_stops_at_particle_no(self, det):
        # 「株式会社テスト工業の田中太郎」で人名を法人名に飲み込まない
        ents = det.detect("株式会社テスト工業の田中太郎です。")
        assert texts_of(ents, "ORGANIZATION") == ["株式会社テスト工業"]
        assert texts_of(ents, "PERSON") == ["田中太郎"]

    def test_honorific_not_swallowed_into_name(self, det):
        ents = det.detect("山本健太様")
        assert texts_of(ents, "PERSON") == ["山本健太"]

    def test_fullwidth_phone(self, det):
        # 先頭が全角の「０」の電話番号 (紙の帳票の全角入力)
        ents = det.detect("電話番号：０９０－１２３４－５６７８")
        assert texts_of(ents, "PHONE") == ["０９０－１２３４－５６７８"]
        assert "POSTAL_CODE" not in types_in(ents)

    def test_org_suffix_does_not_cross_newline(self, det):
        # 見出し行と法人名が新行だけを挟んで隣接しても結合しない
        ents = det.detect("支払先一覧\n\n株式会社サンプル商事に支払います。")
        assert texts_of(ents, "ORGANIZATION") == ["株式会社サンプル商事"]

    def test_dob_context_does_not_leak_from_next_line(self, det):
        # 「入社日」の値が次行の「生年月日」見出しを文脈として拾わない
        ents = det.detect("入社日: 2019年4月1日\n生年月日: 1992年9月30日")
        assert texts_of(ents, "DATE_OF_BIRTH") == ["1992年9月30日"]

    def test_dob_context_on_previous_line_still_works(self, det):
        # 帳票で見出しと値が行をまたぐ形は文脈として有効のまま
        ents = det.detect("生年月日:\n1992年9月30日")
        assert texts_of(ents, "DATE_OF_BIRTH") == ["1992年9月30日"]


class TestNerChunking:
    """SudachiPy の入力上限 (約 49 KB) を超える長文でも NER を失わないための分割。

    修正前は上限超えで例外になり、**その文書の NER 検出が丸ごと消えていた**
    (警告ログだけが残る)。分割そのものはモデル不要なので常に検証できる。
    """

    def test_short_text_is_single_chunk(self):
        from jp_pii_mask.detect import _ner_chunks

        assert list(_ner_chunks("短い文章です。")) == [(0, "短い文章です。")]

    def test_chunks_cover_text_without_loss(self):
        from jp_pii_mask.detect import _ner_chunks

        text = "本日の会議では来期の計画について話しました。\n" * 3000
        chunks = list(_ner_chunks(text))
        assert len(chunks) > 1
        assert "".join(chunk for _, chunk in chunks) == text
        offset = 0
        for off, chunk in chunks:
            assert off == offset
            assert text[off : off + len(chunk)] == chunk
            assert len(chunk.encode("utf-8")) <= 45_000
            offset += len(chunk)

    def test_single_long_sentence_is_hard_split(self):
        from jp_pii_mask.detect import _ner_chunks

        text = "あ" * 60_000  # 句点が無い = 文境界で切れない
        chunks = list(_ner_chunks(text))
        assert len(chunks) > 1
        assert "".join(chunk for _, chunk in chunks) == text
        assert all(len(chunk.encode("utf-8")) <= 45_000 for _, chunk in chunks)

    def test_ner_entities_beyond_sudachi_limit(self):
        pytest.importorskip("ja_ginza")
        from jp_pii_mask.detect import Detector

        det = Detector(use_ner=True)
        if not det.ner_available:
            pytest.skip("NER モデルを読み込めない環境")
        filler = "本日の会議では来期の計画について話しました。\n" * 2000  # 約 105 KB
        tail = "株式会社サンプル商事の綾小路麗華に連絡してください。"
        text = filler + tail
        ents = list(det._detect_ner(text))
        assert ents, "長文で NER が丸ごと落ちている"
        for e in ents:
            assert text[e.start : e.end] == e.text, "断片分割後の座標がずれている"
        assert any(e.start > 45_000 for e in ents), "後半の断片が処理されていない"


class TestCustomTerms:
    def test_custom_term_via_proxy_helper(self):
        import os
        import tempfile

        from jp_pii_mask.config import ConfigStore
        from jp_pii_mask.proxy import MaskingProxy

        with tempfile.TemporaryDirectory() as d:
            store = ConfigStore(os.path.join(d, "config.json"))
            store.update(custom_terms=["プロジェクトフェニックス"])
            proxy = MaskingProxy(store)
            ents = proxy._extra_entities("プロジェクトフェニックスの件です。")
            assert len(ents) == 1
            assert ents[0].type == "CUSTOM"
