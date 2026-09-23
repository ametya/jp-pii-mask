"""設定の読み書きと既定値。

保存先:
  Windows : %USERPROFILE%\\.jp-pii-mask\\
  その他  : ~/.jp-pii-mask/

API キーの扱い:
  - 既定では環境変数を優先する (ファイルに残さない)。
  - GUI から入力した場合は config.json に保存するが、ファイル権限を 0600 に絞る。
  - 「保存しない」モード (store_api_key=False) も選べる。
"""

from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

__all__ = ["Config", "UPSTREAM_PRESETS", "config_dir", "default_config_path", "load_config"]

# 転送先の既定 URL。ユーザーが選ぶだけで使えるようにしておく。
UPSTREAM_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com",
        "env": "OPENAI_API_KEY",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com",
        "env": "ANTHROPIC_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "env": "DEEPSEEK_API_KEY",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "env": "GEMINI_API_KEY",
    },
    "azure_openai": {
        "label": "Azure OpenAI",
        "base_url": "https://YOUR-RESOURCE.openai.azure.com",
        "env": "AZURE_OPENAI_API_KEY",
    },
    "ollama": {
        "label": "ローカル (Ollama)",
        "base_url": "http://127.0.0.1:11434",
        "env": "",
    },
    "custom": {
        "label": "カスタム",
        "base_url": "",
        "env": "",
    },
}


def config_dir() -> Path:
    """設定ディレクトリを返す (存在しなければ作る)。"""
    override = os.environ.get("JP_PII_MASK_HOME")
    if override:
        p = Path(override)
    elif os.name == "nt":
        base = os.environ.get("USERPROFILE") or str(Path.home())
        p = Path(base) / ".jp-pii-mask"
    else:
        p = Path.home() / ".jp-pii-mask"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    """実行時設定。GUI / CLI / 設定ファイルのいずれからも同じ形で扱う。"""

    # 動作
    enabled: bool = True                 # プロキシ全体の ON/OFF
    masking_enabled: bool = True         # マスキングの ON/OFF (OFF なら素通し)
    mask_requests: bool = True
    restore_responses: bool = True

    # ネットワーク
    host: str = "127.0.0.1"
    port: int = 8787
    upstream: str = "https://api.openai.com"
    upstream_preset: str = "openai"
    api_key: str = ""                    # 空なら環境変数 / 受信ヘッダを使う
    store_api_key: bool = False
    forward_client_auth: bool = True     # クライアントの Authorization を上流へ渡す
    extra_headers: dict[str, str] = field(default_factory=dict)
    request_timeout: float = 300.0

    # 検出
    min_confidence: float = 0.5
    enabled_types: list[str] = field(default_factory=list)   # 空 = 全種別
    use_ner: bool = False
    custom_terms: list[str] = field(default_factory=list)    # ユーザー追加の固有名詞

    # マスク方法
    redact_only: bool = False            # True なら復元しない (完全消去)
    placeholder_template: str = "[{label}_{index}]"

    # Vault
    vault_persist: bool = True
    vault_encrypt: bool = True
    vault_ttl_hours: float = 24.0

    # ログ
    log_level: str = "info"
    audit_log: bool = True               # マスク件数などの統計を記録 (原文は残さない)

    # 内部
    config_path: str = ""

    # ------------------------------------------------------------------
    def resolve_api_key(self) -> str:
        """実効 API キーを決める。設定 > 環境変数 の順。"""
        if self.api_key:
            return self.api_key
        preset = UPSTREAM_PRESETS.get(self.upstream_preset, {})
        env_name = preset.get("env") or ""
        if env_name:
            v = os.environ.get(env_name, "")
            if v:
                return v
        for name in ("JP_PII_MASK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            v = os.environ.get(name, "")
            if v:
                return v
        return ""

    def normalized_upstream(self) -> str:
        return self.upstream.rstrip("/")

    def to_public_dict(self) -> dict[str, Any]:
        """GUI に返してよい形 (秘密を含めない)。"""
        d = asdict(self)
        d["api_key"] = ""
        d["api_key_set"] = bool(self.resolve_api_key())
        d["config_path"] = str(self.config_path)
        return d

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(**clean)


class ConfigStore:
    """設定ファイルの読み書き (スレッド安全)。"""

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else default_config_path()
        self._lock = threading.RLock()
        self._config = Config(config_path=str(self.path))
        self.load()

    @property
    def config(self) -> Config:
        with self._lock:
            return self._config

    def load(self) -> Config:
        with self._lock:
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        self._config = Config.from_dict(data)
                except Exception:
                    # 壊れた設定は既定値で上書きせず、そのまま既定を使う
                    pass
            self._config.config_path = str(self.path)
            return self._config

    def save(self) -> None:
        with self._lock:
            data = self._config.to_dict()
            if not self._config.store_api_key:
                data["api_key"] = ""
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            try:
                os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            tmp.replace(self.path)

    def update(self, **changes: Any) -> Config:
        """与えられた項目だけを更新して保存する。"""
        with self._lock:
            cfg = self._config
            for k, v in changes.items():
                if v is None:
                    continue
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            self.save()
            return self._config


def load_config(path: str | os.PathLike | None = None) -> Config:
    return ConfigStore(path).config
