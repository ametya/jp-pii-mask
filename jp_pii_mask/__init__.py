"""jp_pii_mask — 日本語に特化した個人情報マスキング／復元プロキシ。

公開 API の要約:

    from jp_pii_mask import mask_text, unmask_text

    vault = Vault()
    masked, result = mask_text("田中太郎の電話は090-1234-5678です", vault)
    restored = unmask_text(masked, vault)

設計原則:
  1. 可逆性: マスクした文字列は必ず復元できる (Vault が対応表を持つ)。
  2. 決定的: 同一セッション内では同じ原文 → 同じプレースホルダ。
  3. 日本語特化: 敬称・丁目丁番・マイナンバー・法人番号・全角数字を扱う。
  4. 監査可能: 依存は最小、外部送信なし、Vault はローカル暗号化。
  5. 劣化許容: 高度な NER が無くてもルール + 辞書で必ず動作する。
"""

from .detect import Detector, Entity
from .masking import Masker, mask_text, unmask_text
from .vault import Vault

__all__ = [
    "Detector",
    "Entity",
    "Masker",
    "Vault",
    "mask_text",
    "unmask_text",
]

__version__ = "0.1.0"
