"""可逆マスキングのための対応表 (Vault)。

責務:
  - 原文 <-> プレースホルダ の双方向対応を保持する。
  - 同一原文には同一プレースホルダを返す (決定的)。これにより
    LLM の応答内で同じ人物が同じ記号で一貫して現れる。
  - 保存時は暗号化する。平文の個人情報をディスクに残さない。

設計上の判断:
  - 対応表は「セッション」単位で分離できる。会話 A の復元表が
    会話 B の復元に使われると情報漏洩になるため。
  - メモリ上も上限を設ける (LRU 的に古いものを捨てる) ことで、
    長時間稼働時のメモリ肥大を防ぐ。
  - 復号鍵は 0600 で保存し、Windows では DPAPI でユーザーアカウントに
    束縛する。鍵ファイルをコピーしても他ユーザー / 他 PC では使えない。
  - 鍵の置き場所 (key_path) は呼び出し側が決める。既定は従来互換で
    対応表の隣だが、ファイルマスクは `file_key_path()` により
    出力先フォルダから分離したユーザー領域 (= 設定ディレクトリ) に置く。
    出力物と鍵が同じフォルダにあると、フォルダごと共有・コピーされた
    時点で暗号化が無意味になるため。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import stat
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["PLACEHOLDER_RE", "Vault", "VaultEntry"]

log = logging.getLogger("jp_pii_mask.vault")

# DPAPI で保護した鍵ファイルの目印。この接頭辞が無ければ生の鍵として扱う
# (旧バージョンが作った鍵との互換のため)。
_DPAPI_MARKER = b"JPPMK1\x00"


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes | None:
    """Windows DPAPI で data を暗号化/復号する。使えない環境では None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        return None

    blob_ptr = ctypes.POINTER(DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_ptr, wintypes.LPCWSTR, blob_ptr, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, blob_ptr,
    ]
    # CryptUnprotectData の第 2 引数は説明文の出力先 (未使用なので None)。
    crypt32.CryptUnprotectData.argtypes = [
        blob_ptr, ctypes.c_void_p, blob_ptr, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, blob_ptr,
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buf = ctypes.create_string_buffer(bytes(data), len(data))
    blob_in = DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DataBlob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out),
    )
    if not ok:
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _read_key_file(path: Path) -> bytes:
    blob = path.read_bytes()
    if not blob.startswith(_DPAPI_MARKER):
        return blob
    raw = _dpapi_transform(blob[len(_DPAPI_MARKER):], protect=False)
    if raw is None:
        raise ValueError(
            "DPAPI で保護された鍵を復号できませんでした "
            "(別のユーザー / PC で作られた鍵です)"
        )
    return raw


def _write_key_file(path: Path, key: bytes) -> None:
    blob = key
    if os.name == "nt":
        protected = _dpapi_transform(key, protect=True)
        if protected is not None:
            blob = _DPAPI_MARKER + protected
    path.write_bytes(blob)
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

# プレースホルダの標準形式: [氏名_1]
#   - 角括弧は LLM がそのまま保持しやすい
#   - 日本語ラベルにより、モデルが「固有名詞の代わり」と認識しやすい
DEFAULT_PLACEHOLDER_TEMPLATE = "[{label}_{index}]"

# 復元時の揺れ吸収用: 全角括弧・アンダースコア欠落なども拾う
PLACEHOLDER_RE = re.compile(
    r"[\[［【]\s*([^\s\[\]［］【】]{1,24}?)\s*[_＿]?\s*(\d{1,6})\s*[\]］】]"
)

_SUFFIX_RE = re.compile(r"^(.*)_(\d{1,6})$")


@dataclass
class VaultEntry:
    """1 件の対応。"""

    placeholder: str
    original: str
    type: str
    index: int
    created_at: float = field(default_factory=time.time)
    count: int = 1

    def as_dict(self) -> dict:
        return {
            "placeholder": self.placeholder,
            "original": self.original,
            "type": self.type,
            "index": self.index,
            "created_at": self.created_at,
            "count": self.count,
        }

    @staticmethod
    def from_dict(d: dict) -> VaultEntry:
        return VaultEntry(
            placeholder=d["placeholder"],
            original=d["original"],
            type=d.get("type", "UNKNOWN"),
            index=int(d.get("index", 0)),
            created_at=float(d.get("created_at", time.time())),
            count=int(d.get("count", 1)),
        )


class Vault:
    """原文とプレースホルダの双方向対応表。

    Parameters
    ----------
    session_id:
        対応表を分離する単位。None の場合は単一の既定バケットを使う。
    store_path:
        永続化先ファイル。None ならメモリのみ。
    encrypt:
        保存時に暗号化するか。暗号化できない場合は False に落ちる。
    max_entries:
        メモリ保持する最大件数。超過時は古い順に破棄する。
    placeholder_template:
        プレースホルダの書式。
    key_path:
        復号鍵の保存先。None なら従来どおり store_path の隣の
        `key.bin` を使う (後方互換)。呼び出し側が明示すると
        出力先フォルダから鍵を分離できる。
    """

    def __init__(
        self,
        session_id: str | None = None,
        store_path: str | os.PathLike | None = None,
        encrypt: bool = True,
        max_entries: int = 5000,
        placeholder_template: str = DEFAULT_PLACEHOLDER_TEMPLATE,
        key_path: str | os.PathLike | None = None,
    ) -> None:
        self.session_id = session_id or "default"
        self.store_path = Path(store_path) if store_path else None
        self.max_entries = max_entries
        self.placeholder_template = placeholder_template
        self._key_path = Path(key_path) if key_path else None

        self._lock = threading.RLock()
        self._forward: OrderedDict[tuple[str, str], VaultEntry] = OrderedDict()
        self._reverse: dict[str, VaultEntry] = {}
        self._counters: dict[str, int] = {}
        self._encrypt = encrypt
        self._fernet = None

        if encrypt and self.store_path is not None:
            self._fernet = self._load_or_create_key()

    # -- 鍵管理 -----------------------------------------------------------
    @property
    def key_path(self) -> Path | None:
        if self._key_path is not None:
            return self._key_path
        if self.store_path is None:
            return None
        return self.store_path.with_name("key.bin")

    def _load_or_create_key(self):
        """AES-GCM 用の 32 バイト鍵を読み込むか、無ければ生成する。"""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError:
            self._encrypt = False
            return None

        kp = self.key_path
        assert kp is not None
        kp.parent.mkdir(parents=True, exist_ok=True)
        if kp.exists():
            return _read_key_file(kp)
        key = os.urandom(32)
        _write_key_file(kp, key)
        return key

    # -- 暗号化 -----------------------------------------------------------
    def _encrypt_bytes(self, data: bytes) -> bytes:
        if not self._encrypt or self._fernet is None:
            return data
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        ct = AESGCM(self._fernet).encrypt(nonce, data, b"jp-pii-mask-v1")
        return nonce + ct

    def _decrypt_bytes(self, data: bytes) -> bytes:
        if not self._encrypt or self._fernet is None:
            return data
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            nonce, ct = data[:12], data[12:]
            return AESGCM(self._fernet).decrypt(nonce, ct, b"jp-pii-mask-v1")
        except Exception as exc:  # 復号失敗は握りつぶさず明示する
            raise ValueError(f"Vault の復号に失敗しました: {exc}") from exc

    # -- 公開 API ---------------------------------------------------------
    def placeholder_for(self, original: str, entity_type: str, label: str | None = None) -> str:
        """原文に対するプレースホルダを返す (無ければ作る)。"""
        key = (entity_type, original)
        with self._lock:
            entry = self._forward.get(key)
            if entry is not None:
                entry.count += 1
                self._forward.move_to_end(key)
                return entry.placeholder

            idx = self._counters.get(entity_type, 0) + 1
            self._counters[entity_type] = idx
            ph_label = label or self._label_for(entity_type)
            placeholder = self.placeholder_template.format(label=ph_label, index=idx)

            # 万一の衝突 (ユーザー入力に既存プレースホルダが含まれる場合)
            while placeholder in self._reverse and self._reverse[placeholder].original != original:
                idx += 1
                self._counters[entity_type] = idx
                placeholder = self.placeholder_template.format(label=ph_label, index=idx)

            entry = VaultEntry(
                placeholder=placeholder,
                original=original,
                type=entity_type,
                index=idx,
            )
            self._forward[key] = entry
            self._reverse[placeholder] = entry
            self._evict_if_needed()
            return placeholder

    def original_for(self, placeholder: str) -> str | None:
        with self._lock:
            entry = self._reverse.get(placeholder)
            return entry.original if entry else None

    def entry_for(self, placeholder: str) -> VaultEntry | None:
        with self._lock:
            return self._reverse.get(placeholder)

    def entries(self) -> list[VaultEntry]:
        with self._lock:
            return list(self._reverse.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._reverse)

    def clear(self) -> None:
        with self._lock:
            self._forward.clear()
            self._reverse.clear()
            self._counters.clear()

    # -- 永続化 -----------------------------------------------------------
    def save(self) -> None:
        """対応表を保存する。store_path が無ければ何もしない。"""
        if self.store_path is None:
            return
        with self._lock:
            payload = {
                "version": 1,
                "session_id": self.session_id,
                "saved_at": time.time(),
                "counters": self._counters,
                "entries": [e.as_dict() for e in self._reverse.values()],
            }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        blob = self._encrypt_bytes(raw)
        path = self.store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(blob)
        with contextlib.suppress(OSError):
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(path)

    def load(self) -> bool:
        """保存済みの対応表を読み込む。成功したら True。"""
        if self.store_path is None or not self.store_path.exists():
            return False
        blob = self.store_path.read_bytes()
        raw = self._decrypt_bytes(blob)
        payload = json.loads(raw.decode("utf-8"))
        with self._lock:
            self._forward.clear()
            self._reverse.clear()
            self._counters = dict(payload.get("counters", {}))
            for d in payload.get("entries", []):
                e = VaultEntry.from_dict(d)
                self._forward[(e.type, e.original)] = e
                self._reverse[e.placeholder] = e
        return True

    def export_mapping(self) -> dict[str, str]:
        """プレースホルダ -> 原文 の対応を返す (デバッグ・監査用)。"""
        with self._lock:
            return {ph: e.original for ph, e in self._reverse.items()}

    # -- 内部 -------------------------------------------------------------
    def _evict_if_needed(self) -> None:
        while len(self._forward) > self.max_entries:
            _key, entry = self._forward.popitem(last=False)
            self._reverse.pop(entry.placeholder, None)

    @staticmethod
    def _label_for(entity_type: str) -> str:
        from .detect import TYPE_LABELS

        return TYPE_LABELS.get(entity_type, entity_type)


class SessionVaults:
    """セッション ID ごとに Vault を発行・保持するマネージャ。"""

    def __init__(
        self,
        root: str | os.PathLike | None = None,
        encrypt: bool = True,
        persist: bool = True,
        max_entries_per_session: int = 5000,
        ttl_seconds: float | None = 24 * 3600,
    ) -> None:
        self.root = Path(root) if root else None
        self.encrypt = encrypt
        self.persist = persist
        self.max_entries_per_session = max_entries_per_session
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._vaults: dict[str, Vault] = {}
        self._touched: dict[str, float] = {}

        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)

    def get(self, session_id: str | None) -> Vault:
        sid = session_id or "default"
        with self._lock:
            self._expire_locked()
            vault = self._vaults.get(sid)
            if vault is None:
                store = None
                if self.persist and self.root is not None:
                    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", sid)[:80] or "default"
                    store = self.root / f"{safe}.jpvault"
                vault = Vault(
                    session_id=sid,
                    store_path=store,
                    encrypt=self.encrypt,
                    max_entries=self.max_entries_per_session,
                )
                if store is not None and store.exists():
                    try:
                        vault.load()
                    except Exception as exc:
                        # 壊れた Vault は無視して作り直す (起動を止めない)。
                        # ただし黙殺せず警告する: 復号鍵の不一致は
                        # 「復元できない」という重大な症状につながるため。
                        log.warning("Vault の読み込みに失敗したため新規作成します: %s", exc)
                self._vaults[sid] = vault
            self._touched[sid] = time.time()
            return vault

    def save_all(self) -> None:
        with self._lock:
            for sid, v in self._vaults.items():
                try:
                    v.save()
                except Exception as exc:
                    log.warning("Vault の保存に失敗しました (session=%s): %s", sid, exc)

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._vaults.pop(session_id, None)
            self._touched.pop(session_id, None)

    def _expire_locked(self) -> None:
        if self.ttl_seconds is None:
            return
        now = time.time()
        stale = [sid for sid, t in self._touched.items() if now - t > self.ttl_seconds]
        for sid in stale:
            vault = self._vaults.pop(sid, None)
            if vault is not None and self.persist:
                try:
                    vault.save()
                except Exception as exc:
                    log.warning("期限切れ Vault の保存に失敗しました (session=%s): %s", sid, exc)
            self._touched.pop(sid, None)
