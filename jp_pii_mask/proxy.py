"""OpenAI / Anthropic 互換のマスキング・プロキシ。

流れ:

    クライアント (Claude Desktop, Cursor, 自作スクリプト …)
        │  Authorization: Bearer sk-...
        ▼
    localhost:8787  ──  ① リクエスト本文の個人情報を検出
                       ② プレースホルダに置換 (Vault に記録)
                       ③ 上流 API へ転送
                       ④ 応答のプレースホルダを原文に復元
                       ⑤ クライアントへ返す (SSE は逐次復元)

対応形式:
  - OpenAI Chat Completions / Responses / Embeddings (JSON)
  - Anthropic Messages (JSON, SSE)
  - 上記以外の JSON も、文字列値を再帰的に処理するため概ね通る

意図的な制限 (README に明記):
  - multipart/form-data とバイナリ本文はマスクせず素通しする。
  - SSE の data 行が JSON でない場合は、素のテキストとして逐次復元する。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import Config, ConfigStore
from .detect import Detector, Entity
from .masking import Masker, StreamRestorer
from .vault import SessionVaults, Vault
from .webui import PANEL_HTML

log = logging.getLogger("jp_pii_mask.proxy")

__all__ = ["MaskingProxy", "Stats", "create_app"]

# ---------------------------------------------------------------------------
# マスク対象から外すキー
# ---------------------------------------------------------------------------
# 構造や識別子を表すキー。ここを置換すると上流 API が壊れる。
SKIP_MASK_KEYS: frozenset[str] = frozenset(
    {
        "model", "role", "type", "object", "id", "finish_reason", "tool_call_id",
        "encoding_format", "response_format", "tool_choice", "index", "logprobs",
        "name", "required", "properties", "enum", "format", "const", "seed",
        "reasoning_effort", "service_tier",
    }
)

# tools / functions の定義部分はスキーマであり、置換すると呼び出しが壊れる。
SKIP_MASK_SUBTREES: frozenset[str] = frozenset({"tools", "functions", "tool_choice"})

# 応答復元の対象キー。ここに含まれる文字列のみ復元する。
RESTORE_KEYS: frozenset[str] = frozenset(
    {
        "content", "text", "arguments", "reasoning_content", "thinking",
        "completion", "output_text", "value", "output", "input", "message",
        "delta", "choices", "data", "results", "answer", "response", "summary",
    }
)

# 復元対象の子を辿るための汎用キー (どの親の下でも辿る)
TRAVERSE_ALWAYS: frozenset[str] = frozenset(
    {"choices", "delta", "message", "content", "data", "output", "results", "error"}
)


# ---------------------------------------------------------------------------
# ローカル限定ガード
# ---------------------------------------------------------------------------
# デスクトップ GUI (Tauri) は別オリジン (tauri://localhost) から
# 127.0.0.1 の管理 API を叩くため、CORS を許可する必要がある。
# 一方で、任意の Web サイトが平文のローカル API を読めるのは危険
# (特に /admin/api/mapping は原文を返す)。
# そこで「ローカル系オリジンのみ許可 + Origin 付きの非許可要求は拒否」とする。

_ALLOWED_ORIGIN_RE = re.compile(
    r"^(?:https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?"
    r"|(?:tauri|https?)://tauri\.localhost"
    r"|tauri://localhost)$"
)


def _origin_allowed(origin: str) -> bool:
    return bool(_ALLOWED_ORIGIN_RE.match(origin.strip()))


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": (
            "Content-Type, Authorization, x-api-key, x-jp-pii-session, "
            "x-session-id, anthropic-version, anthropic-beta"
        ),
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


def _sse_delta(text: str) -> bytes:
    """SSE の 1 チャンク (OpenAI 互換の delta) を組み立てる。

    ストリーム復元の末尾で保留分を吐き出すときに使う。
    """
    payload = {"choices": [{"delta": {"content": text}}]}
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


@dataclass
class Stats:
    """監査用の集計。原文は一切保持しない。"""

    requests_total: int = 0
    requests_masked: int = 0
    entities_masked: int = 0
    placeholders_restored: int = 0
    restore_misses: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    started_at: float = field(default_factory=time.time)
    by_type: dict[str, int] = field(default_factory=dict)
    last_error: str = ""

    def add_types(self, counts: dict[str, int]) -> None:
        for k, v in counts.items():
            self.by_type[k] = self.by_type.get(k, 0) + v

    def as_dict(self) -> dict[str, Any]:
        d = {
            "requests_total": self.requests_total,
            "requests_masked": self.requests_masked,
            "entities_masked": self.entities_masked,
            "placeholders_restored": self.placeholders_restored,
            "restore_misses": self.restore_misses,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "by_type": dict(self.by_type),
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "last_error": self.last_error,
        }
        return d


class MaskingProxy:
    """設定・検出器・Vault をまとめて保持するプロキシ本体。"""

    def __init__(self, store: ConfigStore) -> None:
        self.store = store
        self.stats = Stats()
        self._detector: Detector | None = None
        self._detector_signature: tuple | None = None
        self._custom_res: list[re.Pattern[str]] = []
        self._custom_signature: tuple | None = None
        self._vaults = self._build_vaults(store.config)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # 構築
    # ------------------------------------------------------------------
    @staticmethod
    def _build_vaults(cfg: Config) -> SessionVaults:
        from .config import config_dir

        root = config_dir() / "vaults" if cfg.vault_persist else None
        return SessionVaults(
            root=root,
            encrypt=cfg.vault_encrypt,
            persist=cfg.vault_persist,
            ttl_seconds=(cfg.vault_ttl_hours * 3600 if cfg.vault_ttl_hours else None),
        )

    @property
    def config(self) -> Config:
        return self.store.config

    @property
    def detector(self) -> Detector:
        """設定が変わったら検出器を作り直す。"""
        cfg = self.config
        sig = (
            cfg.use_ner,
            cfg.min_confidence,
            tuple(sorted(cfg.enabled_types or [])),
        )
        if self._detector is None or self._detector_signature != sig:
            self._detector = Detector(
                use_ner=cfg.use_ner,
                min_confidence=cfg.min_confidence,
                enabled_types=cfg.enabled_types or None,
            )
            self._detector_signature = sig

        csig = tuple(cfg.custom_terms or [])
        if self._custom_signature != csig:
            self._custom_res = [
                re.compile(re.escape(t)) for t in (cfg.custom_terms or []) if t.strip()
            ]
            self._custom_signature = csig
        return self._detector

    @property
    def masker(self) -> Masker:
        cfg = self.config
        return Masker(
            detector=self.detector,
            min_confidence=cfg.min_confidence,
            redact_only=cfg.redact_only,
            redaction_text="[REDACTED]",
        )

    def vault_for(self, request: Request) -> Vault:
        """セッション ID を決めて Vault を返す。

        優先順: 明示ヘッダ > API キー > 既定。
        会話が混ざると復元事故になるため、クライアント単位で分離する。
        """
        sid = (
            request.headers.get("x-jp-pii-session")
            or request.headers.get("x-session-id")
            or ""
        )
        if not sid:
            auth = request.headers.get("authorization", "")
            sid = f"key:{auth[-18:]}" if auth else "default"
        return self._vaults.get(sid)

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.request_timeout, connect=20.0),
                follow_redirects=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._vaults.save_all()

    # ------------------------------------------------------------------
    # リクエスト側: マスク
    # ------------------------------------------------------------------
    def _extra_entities(self, text: str) -> list[Entity]:
        """ユーザー追加語 (custom_terms) を検出する。"""
        # detector プロパティを参照して _custom_res の遅延構築を確実にする。
        _ = self.detector
        out: list[Entity] = []
        for pat in self._custom_res:
            for m in pat.finditer(text):
                if m.end() > m.start():
                    out.append(Entity("CUSTOM", m.start(), m.end(), m.group(0), 0.99, "custom"))
        return out

    def mask_payload(self, payload: Any, vault: Vault) -> tuple[Any, int, dict[str, int]]:
        """JSON 全体を再帰的に走査して文字列値をマスクする。"""
        masker = self.masker
        total = 0
        type_counts: dict[str, int] = {}

        def walk(node: Any, key: str | None) -> Any:
            nonlocal total
            if isinstance(node, dict):
                out: dict[str, Any] = {}
                for k, v in node.items():
                    if k in SKIP_MASK_SUBTREES:
                        out[k] = v          # スキーマは素通し
                        continue
                    out[k] = walk(v, k)
                return out
            if isinstance(node, list):
                return [walk(v, key) for v in node]
            if isinstance(node, str):
                if key is not None and key in SKIP_MASK_KEYS:
                    return node
                if not node:
                    return node
                entities = masker.detector.detect(node)
                entities.extend(self._extra_entities(node))
                if not entities:
                    return node
                # 重複区間を除外
                entities = masker.detector._resolve_overlaps(entities)
                res = masker.mask_with_entities(node, entities, vault)
                if res.count:
                    total += res.count
                    for k, v in res.types_summary().items():
                        type_counts[k] = type_counts.get(k, 0) + v
                return res.text
            return node

        return walk(payload, None), total, type_counts

    # ------------------------------------------------------------------
    # 応答側: 復元
    # ------------------------------------------------------------------
    def restore_payload(self, payload: Any, vault: Vault) -> tuple[Any, int, int]:
        """JSON 全体を再帰的に走査し、プレースホルダを原文に戻す。"""
        masker = self.masker
        restored = 0
        misses = 0

        def walk(node: Any, key: str | None) -> Any:
            nonlocal restored, misses
            if isinstance(node, dict):
                return {k: walk(v, k) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(v, key) for v in node]
            if isinstance(node, str):
                if not node:
                    return node
                if (key is None or key not in RESTORE_KEYS) and not re.search(
                    r"[\[［【].*?\d+[\]］】]", node
                ):
                    # 対象キー以外でも、プレースホルダらしき文字列は復元する
                    return node
                res = masker.unmask(node, vault)
                restored += res.restored
                misses += len(res.missing)
                return res.text
            return node

        return walk(payload, None), restored, misses

    # ------------------------------------------------------------------
    # 転送
    # ------------------------------------------------------------------
    def build_upstream_headers(self, request: Request) -> dict[str, str]:
        cfg = self.config
        headers: dict[str, str] = {}
        for k, v in request.headers.items():
            lk = k.lower()
            if lk in {
                "host", "content-length", "connection", "accept-encoding",
                "transfer-encoding", "authorization", "x-api-key",
            }:
                continue
            headers[k] = v

        key = cfg.resolve_api_key()
        if cfg.forward_client_auth:
            if "authorization" in request.headers:
                headers["Authorization"] = request.headers["authorization"]
            if "x-api-key" in request.headers:
                headers["x-api-key"] = request.headers["x-api-key"]
        if key:
            # 設定 > クライアントの順で上書きする (ユーザーが GUI で指定した場合)
            if cfg.upstream_preset == "anthropic":
                headers["x-api-key"] = key
                headers.setdefault("anthropic-version", "2023-06-01")
            else:
                headers["Authorization"] = f"Bearer {key}"

        headers.update(cfg.extra_headers or {})
        return headers

    async def forward(self, request: Request, path: str) -> Response:
        cfg = self.config
        self.stats.requests_total += 1

        raw = await request.body()
        self.stats.bytes_in += len(raw)

        body: bytes = raw
        masked_count = 0
        type_counts: dict[str, int] = {}
        vault = self.vault_for(request)
        content_type = request.headers.get("content-type", "")
        is_json = "application/json" in content_type

        if cfg.enabled and cfg.masking_enabled and cfg.mask_requests and is_json and raw:
            try:
                payload = json.loads(raw)
            except Exception as exc:
                self.stats.last_error = f"リクエスト JSON 解析失敗: {exc}"
                payload = None
            if payload is not None:
                new_payload, masked_count, type_counts = self.mask_payload(payload, vault)
                body = json.dumps(new_payload, ensure_ascii=False).encode("utf-8")
                if masked_count:
                    self.stats.requests_masked += 1
                    self.stats.entities_masked += masked_count
                    self.stats.add_types(type_counts)
                    log.info("マスク: %d 件 %s", masked_count, type_counts)

        url = f"{cfg.normalized_upstream()}/{path.lstrip('/')}"
        if request.url.query:
            url = f"{url}?{request.url.query}"

        headers = self.build_upstream_headers(request)
        client = await self.client()

        method = request.method
        try:
            upstream_req = client.build_request(
                method, url, content=body, headers=headers
            )
            upstream = await client.send(upstream_req, stream=True)
        except Exception as exc:
            self.stats.last_error = f"上流接続失敗: {exc}"
            log.warning("上流接続失敗: %s", exc)
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "type": "jp_pii_mask_upstream_error",
                        "message": f"転送先 API に接続できませんでした: {exc}",
                        "upstream": cfg.normalized_upstream(),
                    }
                },
            )

        resp_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower()
            not in {"content-length", "content-encoding", "transfer-encoding", "connection"}
        }

        ctype = upstream.headers.get("content-type", "")
        do_restore = cfg.enabled and cfg.restore_responses

        # ---- SSE (ストリーミング) ----
        if "text/event-stream" in ctype and do_restore:
            return StreamingResponse(
                self._stream_restore(upstream, vault),
                status_code=upstream.status_code,
                headers=resp_headers,
                media_type="text/event-stream",
            )

        # ---- 通常応答 ----
        try:
            content = await upstream.aread()
        finally:
            await upstream.aclose()

        if do_restore and content:
            if "application/json" in ctype:
                try:
                    payload = json.loads(content)
                    new_payload, restored, misses = self.restore_payload(payload, vault)
                    content = json.dumps(new_payload, ensure_ascii=False).encode("utf-8")
                    self.stats.placeholders_restored += restored
                    self.stats.restore_misses += misses
                except Exception as exc:
                    self.stats.last_error = f"応答 JSON 解析失敗: {exc}"
            elif ctype.startswith("text/"):
                res = self.masker.unmask(content.decode("utf-8", "replace"), vault)
                content = res.text.encode("utf-8")
                self.stats.placeholders_restored += res.restored
                self.stats.restore_misses += len(res.missing)

        self.stats.bytes_out += len(content)
        return Response(
            content=content,
            status_code=upstream.status_code,
            headers=resp_headers,
        )

    async def _stream_restore(
        self, upstream: httpx.Response, vault: Vault
    ) -> AsyncIterator[bytes]:
        """SSE を逐次復元する。

        各 data 行を JSON として解析し、文字列値を StreamRestorer に通す。
        StreamRestorer はプレースホルダが行をまたいで分割された場合も
        正しく復元できるよう、未完の末尾を保留する。
        """
        masker = self.masker
        restorer = StreamRestorer(masker, vault)
        pending_plain: list[str] = []

        def restore_obj(node: Any, key: str | None) -> Any:
            if isinstance(node, dict):
                return {k: restore_obj(v, k) for k, v in node.items()}
            if isinstance(node, list):
                return [restore_obj(v, key) for v in node]
            if isinstance(node, str):
                if not node:
                    return node
                if key is not None and key in SKIP_MASK_KEYS:
                    return node
                return restorer.feed(node)
            return node

        try:
            async for line in upstream.aiter_lines():
                if not line:
                    yield b"\n"
                    continue
                if not line.startswith("data:"):
                    yield (line + "\n").encode("utf-8")
                    continue

                payload = line[5:].strip()
                if payload == "[DONE]":
                    rest = restorer.flush()
                    if rest:
                        yield _sse_delta(rest)
                    yield b"data: [DONE]\n"
                    continue

                try:
                    obj = json.loads(payload)
                except Exception:
                    # JSON でない data 行 (コメントや生テキスト) はそのまま復元する
                    out = restorer.feed(payload)
                    yield f"data: {out}\n".encode()
                    continue

                new_obj = restore_obj(obj, None)
                self.stats.placeholders_restored += 0
                yield (
                    "data: " + json.dumps(new_obj, ensure_ascii=False) + "\n"
                ).encode("utf-8")

            rest = restorer.flush()
            if rest:
                yield _sse_delta(rest)
        except Exception as exc:
            self.stats.last_error = f"ストリーム復元中のエラー: {exc}"
            log.warning("ストリーム復元エラー: %s", exc)
        finally:
            self.stats.placeholders_restored += restorer.restored_total
            await upstream.aclose()
            if pending_plain:
                pass


# ---------------------------------------------------------------------------
# FastAPI アプリ
# ---------------------------------------------------------------------------
def create_app(store: ConfigStore | None = None) -> FastAPI:
    store = store or ConfigStore()
    proxy = MaskingProxy(store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # 終了時に未保存の Vault を永続化し、接続を閉じる
        await proxy.aclose()

    app = FastAPI(
        title="jp-pii-mask",
        description="日本語に特化した個人情報マスキング／復元プロキシ",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.proxy = proxy

    @app.middleware("http")
    async def _local_origin_guard(request: Request, call_next):
        """ローカル以外のオリジンからの要求を遮断しつつ、GUI 用 CORS を返す。

        - Origin ヘッダが無い要求 (CLI / curl / 各 SDK) はそのまま通す。
        - 許可オリジン: localhost / 127.0.0.1 / tauri.localhost
        - それ以外の Origin が付いた要求は 403 (ブラウザ経由の盗み見を防ぐ)。
        """
        origin = request.headers.get("origin")
        if not origin:
            return await call_next(request)

        if not _origin_allowed(origin):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "type": "jp_pii_mask_origin_denied",
                        "message": "このオリジンからのアクセスは許可されていません。",
                    }
                },
            )

        if request.method == "OPTIONS":
            return Response(status_code=204, headers=_cors_headers(origin))

        response = await call_next(request)
        for k, v in _cors_headers(origin).items():
            response.headers[k] = v
        return response

    # ---- 管理 API --------------------------------------------------------
    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "enabled": proxy.config.enabled}

    @app.get("/admin/api/state")
    async def get_state() -> dict[str, Any]:
        cfg = proxy.config
        return {
            "enabled": cfg.enabled,
            "masking_enabled": cfg.masking_enabled,
            "mask_requests": cfg.mask_requests,
            "restore_responses": cfg.restore_responses,
            "upstream": cfg.normalized_upstream(),
            "upstream_preset": cfg.upstream_preset,
            "port": cfg.port,
            "host": cfg.host,
            "api_key_set": bool(cfg.resolve_api_key()),
            "store_api_key": cfg.store_api_key,
            "use_ner": cfg.use_ner,
            "ner_available": proxy.detector.ner_available,
            "min_confidence": cfg.min_confidence,
            "custom_terms": list(cfg.custom_terms),
            "stats": proxy.stats.as_dict(),
            "version": "0.1.0",
        }

    @app.post("/admin/api/state")
    async def set_state(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "JSON が必要です"})
        if not isinstance(data, dict):
            return JSONResponse(status_code=400, content={"error": "オブジェクトが必要です"})

        allowed = {
            "enabled", "masking_enabled", "mask_requests", "restore_responses",
            "upstream", "upstream_preset", "api_key", "store_api_key",
            "use_ner", "min_confidence", "custom_terms", "redact_only",
            "forward_client_auth", "port", "host", "vault_persist",
        }
        changes = {k: v for k, v in data.items() if k in allowed}
        if not changes:
            return JSONResponse(status_code=400, content={"error": "変更項目がありません"})

        cfg = store.update(**changes)
        if changes.get("vault_persist") is not None or changes.get("vault_encrypt") is not None:
            proxy._vaults = proxy._build_vaults(cfg)

        # デスクトップ GUI は「POST の応答をそのまま状態として描画」するため、
        # GET /admin/api/state と同じ形を最上位に含めて返す。
        payload = await get_state()
        payload["ok"] = True
        payload["applied"] = sorted(changes.keys())
        return payload

    @app.post("/admin/api/toggle")
    async def toggle() -> dict[str, Any]:
        cfg = store.update(enabled=not proxy.config.enabled)
        return {"ok": True, "enabled": cfg.enabled}

    @app.get("/admin/api/stats")
    async def stats() -> dict[str, Any]:
        return proxy.stats.as_dict()

    @app.post("/admin/api/stats/reset")
    async def reset_stats() -> dict[str, Any]:
        proxy.stats = Stats()
        return {"ok": True}

    @app.get("/admin/api/detect")
    async def detect_preview(text: str = "") -> dict[str, Any]:
        """検出結果のプレビュー (マスクはしない)。"""
        ents = proxy.detector.detect(text)
        ents.extend(proxy._extra_entities(text))
        ents = proxy.detector._resolve_overlaps(ents)
        return {
            "count": len(ents),
            "entities": [e.as_dict() for e in ents],
        }

    @app.get("/admin/api/presets")
    async def presets() -> dict[str, Any]:
        from .config import UPSTREAM_PRESETS

        return UPSTREAM_PRESETS

    @app.get("/admin/api/mapping")
    async def mapping(request: Request) -> dict[str, Any]:
        """現在のセッションの対応表を返す (ローカル専用の確認用)。"""
        vault = proxy.vault_for(request)
        return {"session": vault.session_id, "entries": [e.as_dict() for e in vault.entries()]}

    @app.post("/admin/api/vault/save")
    async def vault_save() -> dict[str, Any]:
        proxy._vaults.save_all()
        return {"ok": True}

    # ---- 制御パネル ------------------------------------------------------
    @app.get("/")
    async def panel() -> HTMLResponse:
        return HTMLResponse(PANEL_HTML)

    # ---- プロキシ本体 (全パスを透過) -----------------------------------
    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def passthrough(request: Request, path: str) -> Response:
        return await proxy.forward(request, path)

    return app
