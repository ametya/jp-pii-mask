"""ローカル監査ログ (JSONL)。

目的:
  「いつ・何件・どの種類をマスク/復元したか」をローカルに記録し、
  後から説明できるようにする (誰が何を送ったか、は記録しない)。

記録しないもの (重要):
  - 原文・検出値そのもの
  - ファイルパス・ファイル名 (ファイル名自体に個人情報が入り得る)
  - セッション ID は SHA-256 の先頭 12 文字のみ (相関は取れるが復元不可)

設計:
  - 1 行 1 JSON (JSONL)。追記のみ。サイズで .1/.2 にローテーション。
  - 失敗しても本体の処理は止めない (ベストエフォート)。
  - 書き込みはスレッドセーフ (プロセスをまたぐ同時書き込みは想定外)。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("jp_pii_mask.audit")

__all__ = ["AuditLog", "default_audit_path", "hash_id"]

DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_BACKUPS = 2


def default_audit_path() -> Path:
    """既定の監査ログの場所 (設定ディレクトリ)。"""
    from .config import config_dir

    return config_dir() / "audit.log"


def hash_id(value: str) -> str:
    """セッション ID 等をそのまま残さないための短いハッシュ。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class AuditLog:
    """JSONL の追記専用監査ログ。"""

    def __init__(
        self,
        path: str | Path | None = None,
        enabled: bool = True,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backups: int = DEFAULT_BACKUPS,
    ) -> None:
        self.path = Path(path) if path else default_audit_path()
        self.enabled = enabled
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()

    def record(self, event: str, **fields: Any) -> None:
        """1 件記録する。失敗しても例外は投げない。"""
        if not self.enabled:
            return
        entry = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "event": event,
        }
        entry.update(fields)
        line = json.dumps(entry, ensure_ascii=False)
        try:
            with self._lock:
                self._rotate_if_needed(len(line.encode("utf-8")) + 1)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as exc:  # 監査ログが本体を止めてはいけない
            log.warning("監査ログの書き込みに失敗しました: %s", exc)

    def tail(self, limit: int = 50) -> list[dict]:
        """最新の limit 件を新しい順で返す。壊れた行は無視する。"""
        if limit <= 0 or not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return []
        out: list[dict] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
            if len(out) >= limit:
                break
        return out

    def clear(self) -> None:
        """ログ本体とローテーション分を削除する (テスト・保守用)。"""
        with self._lock:
            for path in self._all_paths():
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("監査ログの削除に失敗しました (%s): %s", path, exc)

    # -- 内部 -------------------------------------------------------------
    def _all_paths(self) -> list[Path]:
        paths = [self.path]
        for i in range(1, self.backups + 1):
            paths.append(self.path.with_name(f"{self.path.name}.{i}"))
        return paths

    def _rotate_if_needed(self, incoming: int) -> None:
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size + incoming <= self.max_bytes:
                return
            for i in range(self.backups, 0, -1):
                src = self.path.with_name(f"{self.path.name}.{i}")
                if i == self.backups:
                    src.unlink(missing_ok=True)
                    continue
                dst = self.path.with_name(f"{self.path.name}.{i + 1}")
                if src.exists():
                    src.replace(dst)
            self.path.replace(self.path.with_name(f"{self.path.name}.1"))
        except OSError as exc:
            # ローテーションに失敗しても追記自体は続ける
            log.warning("監査ログのローテーションに失敗しました: %s", exc)
