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
)
