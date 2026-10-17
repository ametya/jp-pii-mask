# ライセンスと OSS 構成

商用化を見据えた依存関係の整理です。**すべての依存が商用利用可能**であり、
コピーレフト（GPL 系）は 1 つも含まれていません。

---

## 1. 本プロジェクトのライセンス

**MIT License** — [`LICENSE`](../LICENSE)

商用利用・改変・再配布・クローズドソース化が可能です。
唯一の条件は、著作権表示とライセンス文を配布物に含めることです。

---

## 2. 実行時依存（必須）

| パッケージ | ライセンス | 用途 | 商用可 |
|---|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | HTTP フレームワーク | ✅ |
| [Starlette](https://github.com/encode/starlette) | BSD-3-Clause | ASGI | ✅ |
| [uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | ASGI サーバ | ✅ |
| [pydantic](https://github.com/pydantic/pydantic) | MIT | 設定スキーマ | ✅ |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 / BSD-3 | Vault 暗号化 | ✅ |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | 上流への転送 | ✅ |

補足: `uvicorn[standard]` を指定すると `httptools`(MIT) / `uvloop`(MIT/Apache-2.0) /
`watchfiles`(MIT) / `websockets`(BSD) が入ります。いずれも商用可です。

---

## 3. 任意依存

### 精度向上（NER）

| パッケージ | ライセンス | 商用可 |
|---|---|---|
| [spaCy](https://github.com/explosion/spaCy) | MIT | ✅ |
| [GiNZA](https://github.com/megagonlabs/ginza) | MIT | ✅ |
| [SudachiPy](https://github.com/WorksApplications/SudachiPy) | Apache-2.0 | ✅ |
| [SudachiDict](https://github.com/WorksApplications/SudachiDict) | Apache-2.0 | ✅ |

> **注意:** GiNZA が参照するモデルによっては、別途ライセンス条項を持つ場合があります。
> 同梱・配布する場合は各モデルのライセンスを個別に確認してください。

### PDF・画像ファイルのマスク

| パッケージ | ライセンス | 商用可 |
|---|---|---|
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU (HPND) | ✅ |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | BSD-3-Clause / Apache-2.0（PDFium 本体は BSD-3-Clause） | ✅ |
| [winsdk](https://github.com/pywinrt/pywinrt) | MIT | ✅ |

### 開発時のみ

| パッケージ | ライセンス | 商用可 |
|---|---|---|
| pytest | MIT | ✅ |
| ruff | MIT | ✅ |
| PyInstaller | GPL-2.0 **with Bootloader Exception** | ✅ |

> **PyInstaller の GPL について:**
> PyInstaller は GPL-2.0 ですが、**Bootloader Exception** により、
> PyInstaller で生成した実行ファイルには GPL は及びません。
> 生成物を商用配布することは可能です。ただし PyInstaller 自体を改変して
> 配布する場合は話が変わります。

### デスクトップ GUI

| パッケージ | ライセンス | 商用可 |
|---|---|---|
| [Tauri](https://github.com/tauri-apps/tauri) | MIT / Apache-2.0 | ✅ |
| [tauri-plugin-shell](https://github.com/tauri-apps/plugins-workspace) | MIT / Apache-2.0 | ✅ |
| [tauri-plugin-autostart](https://github.com/tauri-apps/plugins-workspace) | MIT / Apache-2.0 | ✅ |
| React / Svelte（使う場合） | MIT | ✅ |

---

## 4. 検討したが採用しなかったもの

| 候補 | ライセンス | 不採用の理由 |
|---|---|---|
| [Microsoft Presidio](https://github.com/microsoft/presidio) | MIT | ライセンスは問題なし。ただし日本語の検出精度が実務水準に届かず、フォーク保守コストが自前実装を上回る。設計は参考にした |
| [LiteLLM](https://github.com/BerriAI/litellm) | MIT | 多プロバイダ対応は魅力だが、依存が非常に多く、単一バイナリ化と監査容易性を損なう。「OpenAI 互換の 1 本の経路」で足りる |
| [Jan](https://github.com/menloresearch/jan) | AGPL-3.0 | **AGPL はコピーレフト。** ネットワーク越しの利用でもソース開示義務が生じうるため、商用クローズド製品には不適 |
| [Lobe Chat](https://github.com/lobehub/lobe-chat) | Apache-2.0 だが一部制限 | 追加条項の確認が必要。チャット UI 自体は本製品の主目的ではない |
| Electron | MIT | 常駐アプリとしてメモリ消費が大きく、ユーザー体験を損なう。Tauri を採用 |

**方針の一貫性:** 依存は「MIT / Apache-2.0 / BSD」に限定し、
AGPL・GPL（Exception 無し）・SSPL・BUSL は採用しません。
これにより、将来のクローズドソース化・買収・再ライセンスの選択肢を保ちます。

---

## 5. 商用化にあたっての法務チェックリスト

- [ ] 配布物に `LICENSE` と依存各社のライセンス表示を含める（`THIRD-PARTY-NOTICES`）
- [ ] GiNZA / spaCy の**モデル**のライセンスを個別確認
- [ ] macOS 配布: Apple Developer Program による署名と公证（notarization）
- [ ] Windows 配布: コード署名証明書の取得（EV 証明書推奨）
- [ ] プライバシーポリシーの策定
      — 「原文は外部送信しない」ことを技術的に保証し、明記する
- [ ] 利用規約、特定商取引法に基づく表記（直販の場合）
- [ ] 個人情報保護法上の位置づけの確認
      — 本製品は**処理を代行しない**（ローカル完結）ため、
      事業者が個人データを「委託」する構図にはなりにくい。ただし要確認
- [ ] 輸出管理（暗号化機能を含むため、該当法令の確認）

---

## 6. 第三者への開示

本製品を SaaS として提供する場合（将来構想）、
**利用者の原文がサーバーに到達しない構成**を維持することが
最大の差別化要因になります。逆に、クラウド版を作る場合は
「原文を保持しない」ことを技術的・契約的に保証する必要があります。

---

## 参考

- MIT License: <https://opensource.org/licenses/MIT>
- PyInstaller Bootloader Exception: <https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt>
- AGPL-3.0 のネットワーク条項: <https://www.gnu.org/licenses/agpl-3.0.html>
