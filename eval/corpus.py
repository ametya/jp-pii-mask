"""検出精度を測るための小さな評価コーパス。

目的
----
「商用に耐えうるか」「この変更で精度が落ちていないか」を
主観ではなく数字で確認するための、人手でラベル付けした少量の
日本語文書セット。完璧な網羅ではなく、**回帰検知**と
**雑な見積もりの下限**を与えることを目的とする。

ルール
------
- `expected` には「人間が個人情報と判断する値」を書く。
  検出器が最終的に選ぶ型 (合成型優先: POSTAL_ADDRESS_FULL 等) で書く。
- 個人情報でない数字・文字列 (検査用数字の合わない番号など) は
  expected に書かない。誤検出 (precision) の測定対象になる。
- OCR 入力は「OCR が実際に返す揺れ」(長音・空白) を含める。
  OCR が返さない揺れ (文字化け等) は含めない。

値について
----------
マイナンバー・法人番号・カード番号は**検査用数字が正しい値**を
`jp_pii_mask.validators` で生成して埋めている。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Expected:
    """1 件の「ここは個人情報」というラベル。"""

    type: str
    text: str


@dataclass(frozen=True)
class Document:
    """評価対象の 1 文書。"""

    id: str
    title: str
    text: str
    expected: tuple[Expected, ...]

    def __post_init__(self) -> None:
        for exp in self.expected:
            if exp.text not in self.text:
                raise ValueError(
                    f"コーパスの不整合: {self.id} に {exp.text!r} がありません"
                )


# 検査用数字が正しいサンプル値 (validators で生成)
_MYNUMBER_A = "526018159084"
_MYNUMBER_B = "301661318600"
_CORP_A = "9139099603086"
_CORP_B = "2462819482191"
_CARD_A = "4242935181909372"
_CARD_B = "4242865797543234"


DOCUMENTS: tuple[Document, ...] = (
    Document(
        id="invoice_vendor",
        title="請求書 (取引先の担当者・振込先)",
        text=(
            "請求書\n\n"
            "株式会社サンプル商事 御中\n"
            "いつもお世話になっております。株式会社テスト工業の田中太郎です。\n\n"
            "ご請求金額: 1,234,000円\n"
            "お問い合わせ先: 03-1234-5678\n"
            "メール: taro.tanaka@example.co.jp\n\n"
            "振込先: 普通 1234567\n"
            "カード決済控え: 4242935181909372\n"
        ),
        expected=(
            Expected("ORGANIZATION", "株式会社サンプル商事"),
            Expected("ORGANIZATION", "株式会社テスト工業"),
            Expected("PERSON", "田中太郎"),
            Expected("PHONE", "03-1234-5678"),
            Expected("EMAIL", "taro.tanaka@example.co.jp"),
            Expected("BANK_ACCOUNT", "普通 1234567"),
            Expected("CREDIT_CARD", _CARD_A),
        ),
    ),
    Document(
        id="hr_offer",
        title="採用通知 (応募者の連絡先・マイナンバー)",
        text=(
            "採用通知書\n\n"
            "山田花子 様\n\n"
            "このたびは弊社の選考にご応募いただきありがとうございます。\n"
            f"入社手続きのため、マイナンバー（{_MYNUMBER_A}）と"
            "生年月日（1995年4月12日）を\nご提出ください。\n\n"
            "株式会社サンプル商事 人事部\n"
            "連絡先: 090-8765-4321\n"
            "メール: hanako.yamada@example.co.jp\n"
        ),
        expected=(
            Expected("PERSON", "山田花子"),
            Expected("MYNUMBER", _MYNUMBER_A),
            Expected("DATE_OF_BIRTH", "1995年4月12日"),
            Expected("ORGANIZATION", "株式会社サンプル商事"),
            Expected("PHONE", "090-8765-4321"),
            Expected("EMAIL", "hanako.yamada@example.co.jp"),
        ),
    ),
    Document(
        id="insurance_claim",
        title="保険請求 (被保険者・基礎年金番号)",
        text=(
            "診療報酬明細の確認依頼\n\n"
            "保険者住所: 東京都千代田区千代田1-1\n"
            "被保険者: 佐藤次郎\n"
            "基礎年金番号: 1234-567890\n"
            "生年月日: 昭和60年3月7日\n"
            "電話: 03-9876-5432\n"
        ),
        expected=(
            Expected("ADDRESS", "東京都千代田区千代田1-1"),
            Expected("PERSON", "佐藤次郎"),
            Expected("PENSION", "1234-567890"),
            Expected("DATE_OF_BIRTH", "昭和60年3月7日"),
            Expected("PHONE", "03-9876-5432"),
        ),
    ),
    Document(
        id="license_passport",
        title="本人確認書類 (免許証・パスポート)",
        text=(
            "本人確認書類の控え\n\n"
            "氏名: 鈴木一郎\n"
            "運転免許証番号: 123456789012\n"
            "パスポート番号: TR1234567\n"
            "生年月日: 1988/11/30\n"
            "現住所: 〒150-0001 東京都渋谷区神宮前1-2-3\n"
        ),
        expected=(
            Expected("PERSON", "鈴木一郎"),
            Expected("DRIVER_LICENSE", "123456789012"),
            Expected("PASSPORT", "TR1234567"),
            Expected("DATE_OF_BIRTH", "1988/11/30"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒150-0001 東京都渋谷区神宮前1-2-3"
            ),
        ),
    ),
    Document(
        id="chat_multi",
        title="チャットログ (複数人の会話・表記ゆれ)",
        text=(
            "太郎: 昨日の件、田中太郎の携帯は090-1234-5678だよ\n"
            "花子: ありがとう。メールは hanako@example.jp でいい？\n"
            "太郎: あと、佐藤花子さん（03 9999 8888）にも連絡して\n"
        ),
        expected=(
            Expected("PERSON", "田中太郎"),
            Expected("PHONE", "090-1234-5678"),
            Expected("EMAIL", "hanako@example.jp"),
            Expected("PERSON", "佐藤花子"),
            Expected("PHONE", "03 9999 8888"),
        ),
    ),
    Document(
        id="ocr_scan",
        title="スキャン書類の OCR 読み取り (長音の揺れ)",
        text=(
            "お客様各位\n"
            "株式会社サンプル商事 営業部\n"
            "担当 田中太郎\n"
            "TEL 03ー1234ー5678\n"
            "Mail taro.tanaka@example.co.jp\n"
        ),
        expected=(
            Expected("ORGANIZATION", "株式会社サンプル商事"),
            Expected("PERSON", "田中太郎"),
            Expected("PHONE", "03ー1234ー5678"),
            Expected("EMAIL", "taro.tanaka@example.co.jp"),
        ),
    ),
    Document(
        id="web_config",
        title="開発メモ (API キー・サーバ IP)",
        text=(
            "# API設定メモ\n"
            "OPENAI_API_KEY=sk-proj-Abcdefghijklmnopqrstuvwxyz0123456789ABCD\n"
            "SERVER_IP=133.242.10.5\n"
            "管理者メール: admin@sample.co.jp\n"
            "緊急連絡先: 080-1111-2222\n"
        ),
        expected=(
            Expected(
                "API_KEY",
                "sk-proj-Abcdefghijklmnopqrstuvwxyz0123456789ABCD",
            ),
            Expected("IP_ADDRESS", "133.242.10.5"),
            Expected("EMAIL", "admin@sample.co.jp"),
            Expected("PHONE", "080-1111-2222"),
        ),
    ),
    Document(
        id="corp_registry",
        title="登記情報 (法人番号)",
        text=(
            "法人番号: 9139099603086\n"
            "商号: 株式会社テスト工業\n"
            "所在地: 〒101-0051 東京都千代田区神田神保町2-3-1\n"
        ),
        expected=(
            Expected("CORPORATE_NUMBER", _CORP_A),
            Expected("ORGANIZATION", "株式会社テスト工業"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒101-0051 東京都千代田区神田神保町2-3-1"
            ),
        ),
    ),
    Document(
        id="pension_notice",
        title="年金記録のお知らせ (マイナンバー・住所)",
        text=(
            "年金記録のお知らせ\n\n"
            "対象者: 高橋三郎\n"
            "基礎年金番号: 5678-123456\n"
            f"マイナンバー: {_MYNUMBER_B}\n"
            "住所: 大阪府大阪市北区梅田1-1-1\n"
        ),
        expected=(
            Expected("PERSON", "高橋三郎"),
            Expected("PENSION", "5678-123456"),
            Expected("MYNUMBER", _MYNUMBER_B),
            Expected("ADDRESS", "大阪府大阪市北区梅田1-1-1"),
        ),
    ),
    Document(
        id="bank_transfer",
        title="振込依頼書 (口座・担当者)",
        text=(
            "振込依頼書\n\n"
            "受取人: 株式会社サンプル商事\n"
            "口座: 普通 7654321\n"
            "連絡先: 06-1234-5678\n"
            "担当者: 中村由美\n"
        ),
        expected=(
            Expected("ORGANIZATION", "株式会社サンプル商事"),
            Expected("BANK_ACCOUNT", "普通 7654321"),
            Expected("PHONE", "06-1234-5678"),
            Expected("PERSON", "中村由美"),
        ),
    ),
    Document(
        id="email_footer",
        title="メール署名 (住所・FAX)",
        text=(
            "--\n"
            "田中太郎\n"
            "株式会社サンプル商事 営業部\n"
            "TEL: 03-1234-5678 / FAX: 03-1234-5679\n"
            "Mail: taro@sample-shoji.co.jp\n"
            "〒100-0001 東京都千代田区千代田1-1-1\n"
        ),
        expected=(
            Expected("PERSON", "田中太郎"),
            Expected("ORGANIZATION", "株式会社サンプル商事"),
            Expected("PHONE", "03-1234-5678"),
            Expected("PHONE", "03-1234-5679"),
            Expected("EMAIL", "taro@sample-shoji.co.jp"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒100-0001 東京都千代田区千代田1-1-1"
            ),
        ),
    ),
    Document(
        id="meeting_negative",
        title="議事録 (個人情報を含まない数字の山)",
        text=(
            "定例会議メモ\n\n"
            "資料番号 1234567890123 の改訂版を共有します。\n"
            "テストカード 4242 4242 4242 4241 は検査を通らないダミーです。\n"
            "存在しない郵便番号の例: 123-456\n"
            "価格は 1,234,000円、消費税は 123,400円です。\n"
        ),
        expected=(),
    ),
    Document(
        id="marketing_negative",
        title="案内文 (日付・時刻のみ)",
        text=(
            "製品のご案内\n\n"
            "受付時間は9:00から18:00です。\n"
            "お問い合わせは平日のみ承ります。\n"
            "キャンペーンは2026年10月18日までです。\n"
        ),
        expected=(),
    ),
    Document(
        id="customer_note",
        title="顧客メモ (携帯・生年月日・住所)",
        text=(
            "お客様メモ\n\n"
            "山本健太様\n"
            "携帯: 070-5555-6666\n"
            "メール: kenta.yamamoto@example.com\n"
            "生年月日: 平成3年8月8日\n"
            "住所: 〒460-0001 愛知県名古屋市中区三の丸3-1-1\n"
        ),
        expected=(
            Expected("PERSON", "山本健太"),
            Expected("PHONE", "070-5555-6666"),
            Expected("EMAIL", "kenta.yamamoto@example.com"),
            Expected("DATE_OF_BIRTH", "平成3年8月8日"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒460-0001 愛知県名古屋市中区三の丸3-1-1"
            ),
        ),
    ),
    Document(
        id="student_list",
        title="受講者名簿 (氏名・生年月日)",
        text=(
            "受講者名簿\n\n"
            "1. 小林美咲  生年月日 1998年2月14日\n"
            "2. 加藤大輔  生年月日 平成12年7月7日\n"
            "3. 伊藤愛    生年月日 2001/09/21\n"
        ),
        expected=(
            Expected("PERSON", "小林美咲"),
            Expected("DATE_OF_BIRTH", "1998年2月14日"),
            Expected("PERSON", "加藤大輔"),
            Expected("DATE_OF_BIRTH", "平成12年7月7日"),
            Expected("PERSON", "伊藤愛"),
            Expected("DATE_OF_BIRTH", "2001/09/21"),
        ),
    ),
    Document(
        id="ocr_invoice",
        title="スキャン請求書 (空白入り見出し)",
        text=(
            "請 求 書\n"
            "株式会社テスト工業\n"
            "担 当 田中太郎\n"
            "TEL 052ー111ー2222\n"
            "Mail taro.tanaka@example.co.jp\n"
        ),
        expected=(
            Expected("ORGANIZATION", "株式会社テスト工業"),
            Expected("PERSON", "田中太郎"),
            Expected("PHONE", "052ー111ー2222"),
            Expected("EMAIL", "taro.tanaka@example.co.jp"),
        ),
    ),
    Document(
        id="keys_leak",
        title="環境変数の共有ミス (API キー複数)",
        text=(
            "# 環境変数（共有注意）\n"
            "GEMINI_API_KEY=AIzaSyA1234567890abcdefghijklmnopqrstuv\n"
            "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
            "連絡先: dev@example.co.jp\n"
        ),
        expected=(
            Expected(
                "API_KEY", "AIzaSyA1234567890abcdefghijklmnopqrstuv"
            ),
            Expected("API_KEY", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
            Expected("EMAIL", "dev@example.co.jp"),
        ),
    ),
    Document(
        id="mynumber_card",
        title="本人確認の控え (マイナンバーカード番号)",
        text=(
            "本人確認の控え\n\n"
            "氏名: 渡辺咲子\n"
            f"マイナンバーカード番号: {_MYNUMBER_A}\n"
            "電話: 090-2222-3333\n"
        ),
        expected=(
            Expected("PERSON", "渡辺咲子"),
            Expected("MYNUMBER_CARD", _MYNUMBER_A),
            Expected("PHONE", "090-2222-3333"),
        ),
    ),
    Document(
        id="postal_address",
        title="送付先 (郵便番号 + 住所)",
        text=("書類送付先\n\n〒150-0002 東京都渋谷区渋谷2-24-12\n"),
        expected=(
            Expected("POSTAL_ADDRESS_FULL", "〒150-0002 東京都渋谷区渋谷2-24-12"),
        ),
    ),
    Document(
        id="zenkaku_contact",
        title="登録票 (全角数字の電話・郵便番号)",
        text=(
            "お客様登録票\n\n"
            "氏名: 中村大輔\n"
            "電話番号：０９０－１２３４－５６７８\n"
            "〒１００－０００１ 東京都千代田区千代田1-1\n"
        ),
        expected=(
            Expected("PERSON", "中村大輔"),
            Expected("PHONE", "０９０－１２３４－５６７８"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒１００－０００１ 東京都千代田区千代田1-1"
            ),
        ),
    ),
    Document(
        id="employee_record",
        title="社員情報 (入社日と生年月日の区別)",
        text=(
            "社員情報\n\n"
            "社員番号: 987654\n"
            "氏名: 中村由美\n"
            "入社日: 2019年4月1日\n"
            "生年月日: 1992年9月30日\n"
            "メール: yumi.nakamura@example.co.jp\n"
            "緊急連絡先: 090-3333-4444\n"
        ),
        expected=(
            Expected("PERSON", "中村由美"),
            Expected("DATE_OF_BIRTH", "1992年9月30日"),
            Expected("EMAIL", "yumi.nakamura@example.co.jp"),
            Expected("PHONE", "090-3333-4444"),
        ),
    ),
    Document(
        id="multi_bank",
        title="支払先一覧 (複数口座・種別の違い)",
        text=(
            "支払先一覧\n\n"
            "株式会社サンプル商事  普通 1234567\n"
            "有限会社テスト電機    当座 7654321\n"
            "株式会社テスト工業    普通 2468101\n"
        ),
        expected=(
            Expected("ORGANIZATION", "株式会社サンプル商事"),
            Expected("BANK_ACCOUNT", "普通 1234567"),
            Expected("ORGANIZATION", "有限会社テスト電機"),
            Expected("BANK_ACCOUNT", "当座 7654321"),
            Expected("ORGANIZATION", "株式会社テスト工業"),
            Expected("BANK_ACCOUNT", "普通 2468101"),
        ),
    ),
    Document(
        id="family_notes",
        title="家族構成メモ (かな書きの生まれ)",
        text=(
            "家族構成メモ\n\n"
            "父: 佐藤一郎 昭和40年2月3日生まれ\n"
            "母: 佐藤幸子 1968年11月15日生まれ\n"
            "長男: 佐藤健 2005/04/02生まれ\n"
            "緊急連絡先: 03-5555-6666\n"
        ),
        expected=(
            Expected("PERSON", "佐藤一郎"),
            Expected("DATE_OF_BIRTH", "昭和40年2月3日"),
            Expected("PERSON", "佐藤幸子"),
            Expected("DATE_OF_BIRTH", "1968年11月15日"),
            Expected("PERSON", "佐藤健"),
            Expected("DATE_OF_BIRTH", "2005/04/02"),
            Expected("PHONE", "03-5555-6666"),
        ),
    ),
    Document(
        id="shipping_label",
        title="お届け先 (かな住所・受取人)",
        text=(
            "お届け先\n\n"
            "〒220-0012 神奈川県横浜市西区みなとみらい2-3-5\n"
            "受取人: 伊藤愛\n"
            "電話: 045-222-3333\n"
        ),
        expected=(
            Expected(
                "POSTAL_ADDRESS_FULL", "〒220-0012 神奈川県横浜市西区みなとみらい2-3-5"
            ),
            Expected("PERSON", "伊藤愛"),
            Expected("PHONE", "045-222-3333"),
        ),
    ),
    Document(
        id="stock_table_negative",
        title="在庫管理表 (商品コード・社員番号の山)",
        text=(
            "在庫管理表\n\n"
            "商品コード: 4901234567890\n"
            "見積番号: A-12345\n"
            "社員番号 20260101\n"
            "部屋番号: 305\n"
        ),
        expected=(),
    ),
    Document(
        id="sales_yearly_negative",
        title="売上推移 (年だけの数字)",
        text=(
            "売上推移\n\n"
            "2024年 1200万円\n"
            "2025年 1350万円\n"
            "2026年 1500万円\n"
        ),
        expected=(),
    ),
    Document(
        id="delivery_addresses",
        title="配送先一覧 (郵便番号なしの住所)",
        text=(
            "配送先一覧\n\n"
            "東京都港区赤坂1-2-3\n"
            "大阪府大阪市中央区本町2-1-1\n"
            "福岡県福岡市博多区博多駅前1-1-1\n"
        ),
        expected=(
            Expected("ADDRESS", "東京都港区赤坂1-2-3"),
            Expected("ADDRESS", "大阪府大阪市中央区本町2-1-1"),
            Expected("ADDRESS", "福岡県福岡市博多区博多駅前1-1-1"),
        ),
    ),
    Document(
        id="chat_reserve",
        title="予約チャット (URL を誤検出しない)",
        text=(
            "顧客: 予約を変更したいです\n"
            "窓口: かしこまりました。お名前をどうぞ\n"
            "顧客: 山本健太です\n"
            "窓口: ご連絡先は 070-5555-6666 ですね\n"
            "窓口: 空き状況は https://example.com/reserve?id=123 でご確認ください\n"
        ),
        expected=(
            Expected("PERSON", "山本健太"),
            Expected("PHONE", "070-5555-6666"),
        ),
    ),
    Document(
        id="receipt_scan",
        title="領収書の OCR (御中・様付き)",
        text=(
            "領収書\n\n"
            "株式会社テスト工業 御中\n"
            "高橋美咲様\n"
            "携帯 052ー111ー2222\n"
            "Mail misaki.takahashi@example.co.jp\n"
        ),
        expected=(
            Expected("ORGANIZATION", "株式会社テスト工業"),
            Expected("PERSON", "高橋美咲"),
            Expected("PHONE", "052ー111ー2222"),
            Expected("EMAIL", "misaki.takahashi@example.co.jp"),
        ),
    ),
    Document(
        id="contract_parties",
        title="業務委託契約書 (甲・乙の代表者と所在地)",
        text=(
            "業務委託契約書\n\n"
            "甲: 株式会社サンプル商事\n"
            "代表者: 田中太郎\n"
            "所在地: 〒100-0001 東京都千代田区千代田1-1-1\n"
            "連絡先: 03-1234-5678\n\n"
            "乙: 株式会社テスト工業\n"
            "代表者: 佐藤次郎\n"
            "所在地: 〒150-0002 東京都渋谷区渋谷2-24-12\n"
            "連絡先: 03-9876-5432\n"
        ),
        expected=(
            Expected("ORGANIZATION", "株式会社サンプル商事"),
            Expected("PERSON", "田中太郎"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒100-0001 東京都千代田区千代田1-1-1"
            ),
            Expected("PHONE", "03-1234-5678"),
            Expected("ORGANIZATION", "株式会社テスト工業"),
            Expected("PERSON", "佐藤次郎"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒150-0002 東京都渋谷区渋谷2-24-12"
            ),
            Expected("PHONE", "03-9876-5432"),
        ),
    ),
    Document(
        id="medical_record",
        title="診療記録 (患者の生年月日・住所)",
        text=(
            "診療記録\n\n"
            "患者氏名: 山田花子\n"
            "生年月日: 昭和62年6月15日\n"
            "住所: 〒150-0001 東京都渋谷区神宮前1-2-3\n"
            "電話: 03-5555-7777\n"
            "アレルギー: なし\n"
        ),
        expected=(
            Expected("PERSON", "山田花子"),
            Expected("DATE_OF_BIRTH", "昭和62年6月15日"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒150-0001 東京都渋谷区神宮前1-2-3"
            ),
            Expected("PHONE", "03-5555-7777"),
        ),
    ),
    Document(
        id="school_notice",
        title="学校からのお知らせ (担任・連絡先)",
        text=(
            "保護者各位\n\n"
            "運動会のお知らせ\n\n"
            "担任: 小林美咲\n"
            "連絡先: 045-333-4444\n"
            "メール: misaki.kobayashi@example.ed.jp\n"
            "雨天時は 070-8888-9999 までご連絡ください。\n"
        ),
        expected=(
            Expected("PERSON", "小林美咲"),
            Expected("PHONE", "045-333-4444"),
            Expected("EMAIL", "misaki.kobayashi@example.ed.jp"),
            Expected("PHONE", "070-8888-9999"),
        ),
    ),
    Document(
        id="hotel_booking",
        title="宿泊予約控え (チェックイン日は生年月日ではない)",
        text=(
            "宿泊予約控え\n\n"
            "ご宿泊者: 高橋三郎\n"
            "チェックイン: 2026年10月20日\n"
            "電話: 06-1234-5678\n"
            "メール: saburo.takahashi@example.com\n"
            "住所: 大阪府大阪市北区梅田1-1-1\n"
        ),
        expected=(
            Expected("PERSON", "高橋三郎"),
            Expected("PHONE", "06-1234-5678"),
            Expected("EMAIL", "saburo.takahashi@example.com"),
            Expected("ADDRESS", "大阪府大阪市北区梅田1-1-1"),
        ),
    ),
    Document(
        id="ec_order",
        title="通販の注文確認 (注文番号は個人情報ではない)",
        text=(
            "ご注文内容\n\n"
            "お届け先: 鈴木一郎\n"
            "〒220-0012 神奈川県横浜市西区みなとみらい2-3-5\n"
            "電話番号: 080-1111-2222\n"
            "メール: ichiro.suzuki@example.jp\n"
            "注文番号: 20261020-0001\n"
        ),
        expected=(
            Expected("PERSON", "鈴木一郎"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒220-0012 神奈川県横浜市西区みなとみらい2-3-5"
            ),
            Expected("PHONE", "080-1111-2222"),
            Expected("EMAIL", "ichiro.suzuki@example.jp"),
        ),
    ),
    Document(
        id="job_application",
        title="履歴書 (氏名・生年月日・連絡先)",
        text=(
            "履歴書\n\n"
            "氏名: 伊藤愛\n"
            "生年月日: 2001/09/21\n"
            "住所: 〒460-0001 愛知県名古屋市中区三の丸3-1-1\n"
            "電話: 090-2222-3333\n"
            "メール: ai.ito@example.com\n"
        ),
        expected=(
            Expected("PERSON", "伊藤愛"),
            Expected("DATE_OF_BIRTH", "2001/09/21"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒460-0001 愛知県名古屋市中区三の丸3-1-1"
            ),
            Expected("PHONE", "090-2222-3333"),
            Expected("EMAIL", "ai.ito@example.com"),
        ),
    ),
    Document(
        id="payroll_csv",
        title="給与CSV (カンマ区切りの氏名・連絡先・口座)",
        text=(
            "社員番号,氏名,メール,電話,口座\n"
            "1001,中村由美,yumi.nakamura@example.co.jp,090-3333-4444,普通 1234567\n"
            "1002,山本健太,kenta.yamamoto@example.com,070-5555-6666,当座 7654321\n"
        ),
        expected=(
            Expected("PERSON", "中村由美"),
            Expected("EMAIL", "yumi.nakamura@example.co.jp"),
            Expected("PHONE", "090-3333-4444"),
            Expected("BANK_ACCOUNT", "普通 1234567"),
            Expected("PERSON", "山本健太"),
            Expected("EMAIL", "kenta.yamamoto@example.com"),
            Expected("PHONE", "070-5555-6666"),
            Expected("BANK_ACCOUNT", "当座 7654321"),
        ),
    ),
    Document(
        id="access_log",
        title="アクセスログ (IP・日時・担当者)",
        text=(
            "2026-10-20 09:12:01 GET /api/users 200 133.242.10.5\n"
            "2026-10-20 09:12:05 POST /api/login 401 203.0.113.9\n"
            "2026-10-20 09:12:10 GET /api/health 200 10.0.0.12\n"
            "担当: 中村由美 (yumi.nakamura@example.co.jp)\n"
        ),
        expected=(
            Expected("IP_ADDRESS", "133.242.10.5"),
            Expected("IP_ADDRESS", "203.0.113.9"),
            Expected("IP_ADDRESS", "10.0.0.12"),
            Expected("PERSON", "中村由美"),
            Expected("EMAIL", "yumi.nakamura@example.co.jp"),
        ),
    ),
    Document(
        id="business_card",
        title="名刺のテキスト (会社・氏名・住所)",
        text=(
            "株式会社サンプル商事\n"
            "営業部 課長\n"
            "田中太郎\n"
            "携帯: 090-1234-5678\n"
            "メール: taro.tanaka@example.co.jp\n"
            "〒100-0001 東京都千代田区千代田1-1-1\n"
        ),
        expected=(
            Expected("ORGANIZATION", "株式会社サンプル商事"),
            Expected("PERSON", "田中太郎"),
            Expected("PHONE", "090-1234-5678"),
            Expected("EMAIL", "taro.tanaka@example.co.jp"),
            Expected(
                "POSTAL_ADDRESS_FULL", "〒100-0001 東京都千代田区千代田1-1-1"
            ),
        ),
    ),
    Document(
        id="invoice_numbers_negative",
        title="帳票番号だけのメモ (個人情報なし)",
        text=(
            "請求書番号: INV-2026-0042\n"
            "注文番号: 20261020-0001\n"
            "顧客コード: 1234567890123\n"
            "納品書: 9876543210985\n"
        ),
        expected=(),
    ),
    Document(
        id="seating_negative",
        title="会議室予約表 (時刻・定員・内線)",
        text=(
            "会議室予約表\n\n"
            "10:00-11:00 会議室A (定員 12名)\n"
            "13:30-15:00 会議室B (定員 8名)\n"
            "内線: 1234\n"
            "資料番号: 20261020-01\n"
        ),
        expected=(),
    ),
    Document(
        id="ocr_fax",
        title="FAX 送付状 (OCR の長音揺れ)",
        text=(
            "FAX 送付状\n\n"
            "宛先: 株式会社テスト工業 御中\n"
            "送信者: 渡辺咲子\n"
            "TEL 03ー1111ー2222\n"
            "FAX 03ー1111ー2223\n"
            "Mail sakiko.watanabe@example.co.jp\n"
        ),
        expected=(
            Expected("ORGANIZATION", "株式会社テスト工業"),
            Expected("PERSON", "渡辺咲子"),
            Expected("PHONE", "03ー1111ー2222"),
            Expected("PHONE", "03ー1111ー2223"),
            Expected("EMAIL", "sakiko.watanabe@example.co.jp"),
        ),
    ),
    Document(
        id="chat_support",
        title="サポートチャット (注文番号と連絡先)",
        text=(
            "顧客: 注文番号 20261020-0001 の件で連絡しました\n"
            "担当: お名前をお願いします\n"
            "顧客: 加藤大輔です\n"
            "担当: ご登録のメール daisuke.kato@example.com にご案内します\n"
        ),
        expected=(
            Expected("PERSON", "加藤大輔"),
            Expected("EMAIL", "daisuke.kato@example.com"),
        ),
    ),
    Document(
        id="address_variants",
        title="配送先候補 (北海道・京都・沖縄)",
        text=(
            "配送先候補\n\n"
            "〒060-0001 北海道札幌市中央区北1条西2-3\n"
            "京都府京都市中京区河原町通3-4\n"
            "沖縄県那覇市久茂地1-1-1\n"
        ),
        expected=(
            Expected(
                "POSTAL_ADDRESS_FULL", "〒060-0001 北海道札幌市中央区北1条西2-3"
            ),
            Expected("ADDRESS", "京都府京都市中京区河原町通3-4"),
            Expected("ADDRESS", "沖縄県那覇市久茂地1-1-1"),
        ),
    ),
    Document(
        id="dob_forms",
        title="本人確認票 (令和元年・カナ氏名)",
        text=(
            "本人確認票\n\n"
            "氏名: 佐藤花子\n"
            "生年月日: 令和元年5月1日\n"
            "別名: 佐藤ハナコ\n"
            "電話: 080-4444-5555\n"
        ),
        expected=(
            Expected("PERSON", "佐藤花子"),
            Expected("DATE_OF_BIRTH", "令和元年5月1日"),
            Expected("PERSON", "佐藤ハナコ"),
            Expected("PHONE", "080-4444-5555"),
        ),
    ),
    Document(
        id="mixed_english",
        title="英語混在の議事録 (英語名は対象外)",
        text=(
            "Meeting notes\n\n"
            "Attendees: John Smith, 田中太郎\n"
            "Email: taro.tanaka@example.co.jp\n"
            "Phone: 03-1234-5678\n"
            "Next meeting: 2026-11-10\n"
        ),
        expected=(
            Expected("PERSON", "田中太郎"),
            Expected("EMAIL", "taro.tanaka@example.co.jp"),
            Expected("PHONE", "03-1234-5678"),
        ),
    ),
    Document(
        id="ocr_spaced_phone",
        title="OCR 読み取り結果 (数字の間に空白)",
        text=(
            "スキャン結果\n\n"
            "電話番号: 0 9 0 1 2 3 4 5 6 7 8\n"
            "担当: 山本健太\n"
        ),
        expected=(
            Expected("PHONE", "0 9 0 1 2 3 4 5 6 7 8"),
            Expected("PERSON", "山本健太"),
        ),
    ),
)
