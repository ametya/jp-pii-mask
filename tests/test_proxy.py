"""プロキシの E2E テスト。

実際にローカルの偽上流 API サーバを起動し、
  「クライアント → プロキシ → 上流」の経路で
  ・上流に届いた本文が本当にマスクされているか
  ・クライアントに返る本文が本当に復元されているか
を確認する。モックではなく実 HTTP を通す。
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from jp_pii_mask.config import ConfigStore
from jp_pii_mask.proxy import create_app


# ---------------------------------------------------------------------------
# 偽の上流 API サーバ
# ---------------------------------------------------------------------------
class FakeUpstream:
    """受け取った本文を記録し、決まった応答を返す偽サーバ。"""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.received_raw: list[bytes] = []
        self.received_headers: list[dict[str, str]] = []
        self.app = self._build()
        self.port = self._free_port()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            raw = await request.body()
            body = json.loads(raw)
            self.received.append(body)
            self.received_raw.append(raw)
            self.received_headers.append({k.lower(): v for k, v in request.headers.items()})
            content = body.get("messages", [{}])[-1].get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)

            if body.get("stream"):

                async def gen():
                    # エコーを 2 チャンクに分けて返す (復元の分割耐性を試す)
                    half = max(1, len(content) // 2)
                    for part in (content[:half], content[half:]):
                        chunk = {
                            "id": "chatcmpl-test",
                            "object": "chat.completion.chunk",
                            "model": body.get("model", "test"),
                            "choices": [{"index": 0, "delta": {"content": part}}],
                        }
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"

                return StreamingResponse(gen(), media_type="text/event-stream")

            return {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": body.get("model", "test"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

        @app.get("/v1/models")
        async def models():
            return {"object": "list", "data": [{"id": "test-model"}]}

        return app

    def start(self) -> None:
        config = uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="warning", access_log=False
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if getattr(self._server, "started", False):
                # ソケットが受け付けるまで待つ
                for _ in range(100):
                    try:
                        with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                            return
                    except OSError:
                        time.sleep(0.05)
                return
            time.sleep(0.05)
        raise RuntimeError("偽上流サーバが起動しませんでした")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture(scope="module")
def upstream():
    server = FakeUpstream()
    server.start()
    yield server
    server.stop()


@pytest.fixture()
def proxy_client(upstream, tmp_path, monkeypatch):
    # 監査ログが実ユーザーの設定ディレクトリに書かれないようにする
    monkeypatch.setenv("JP_PII_MASK_HOME", str(tmp_path / "home"))
    store = ConfigStore(str(tmp_path / "config.json"))
    store.update(
        upstream=upstream.base_url,
        upstream_preset="openai",
        enabled=True,
        masking_enabled=True,
        mask_requests=True,
        restore_responses=True,
        vault_persist=False,
        api_key="",
        forward_client_auth=True,
    )
    app = create_app(store)
    with TestClient(app) as client:
        client.store = store  # テストから設定を触れるように
        yield client


def chat(client, content: str, stream: bool = False, **extra):
    payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": content}],
        "stream": stream,
    }
    payload.update(extra)
    return client.post("/v1/chat/completions", json=payload)


# ---------------------------------------------------------------------------
# 基本動作
# ---------------------------------------------------------------------------
class TestBasicRouting:
    def test_healthz(self, proxy_client):
        r = proxy_client.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_get_passthrough(self, proxy_client):
        r = proxy_client.get("/v1/models")
        assert r.status_code == 200
        assert r.json()["data"][0]["id"] == "test-model"

    def test_panel_served(self, proxy_client):
        r = proxy_client.get("/")
        assert r.status_code == 200
        assert "jp-pii-mask" in r.text

    def test_admin_state(self, proxy_client):
        r = proxy_client.get("/admin/api/state")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert "stats" in body

    def test_upstream_unreachable_returns_502(self, tmp_path):
        store = ConfigStore(str(tmp_path / "config.json"))
        store.update(upstream="http://127.0.0.1:9", vault_persist=False)
        with TestClient(create_app(store)) as client:
            r = client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert r.status_code == 502
            assert r.json()["error"]["type"] == "jp_pii_mask_upstream_error"


# ---------------------------------------------------------------------------
# マスキング
# ---------------------------------------------------------------------------
class TestRequestMasking:
    def test_pii_is_masked_before_upstream(self, proxy_client, upstream):
        upstream.received.clear()
        text = "田中太郎の携帯は090-1234-5678です。"
        r = chat(proxy_client, text)
        assert r.status_code == 200

        sent = json.dumps(upstream.received[-1], ensure_ascii=False)
        # 上流には原文が届いていない
        assert "田中太郎" not in sent
        assert "090-1234-5678" not in sent
        # 代わりにプレースホルダが届いている
        assert "[氏名_1]" in sent
        assert "[電話番号_1]" in sent

    def test_email_and_postal_masked(self, proxy_client, upstream):
        upstream.received.clear()
        text = "連絡先は a@example.co.jp、住所は〒100-0001 東京都千代田区千代田1-1です。"
        chat(proxy_client, text)
        sent = json.dumps(upstream.received[-1], ensure_ascii=False)
        assert "a@example.co.jp" not in sent
        assert "千代田区" not in sent

    def test_response_is_restored_to_client(self, proxy_client):
        text = "田中太郎の携帯は090-1234-5678です。"
        r = chat(proxy_client, text)
        content = r.json()["choices"][0]["message"]["content"]
        # 上流はマスク済みを返してくるので、クライアントには原文が戻る
        assert content == text

    def test_multiple_turns_consistent(self, proxy_client, upstream):
        text = "田中太郎です。田中太郎は営業部です。"
        chat(proxy_client, text)
        sent = json.dumps(upstream.received[-1], ensure_ascii=False)
        assert sent.count("[氏名_1]") == 2

    def test_masking_disabled_passes_through(self, proxy_client, upstream):
        proxy_client.post("/admin/api/state", json={"masking_enabled": False})
        upstream.received.clear()
        text = "田中太郎の携帯は090-1234-5678です。"
        r = chat(proxy_client, text)
        sent = json.dumps(upstream.received[-1], ensure_ascii=False)
        assert "田中太郎" in sent
        assert r.json()["choices"][0]["message"]["content"] == text
        proxy_client.post("/admin/api/state", json={"masking_enabled": True})

    def test_disabled_proxy_passes_through(self, proxy_client, upstream):
        proxy_client.post("/admin/api/state", json={"enabled": False})
        upstream.received.clear()
        chat(proxy_client, "田中太郎です。")
        sent = json.dumps(upstream.received[-1], ensure_ascii=False)
        assert "田中太郎" in sent
        proxy_client.post("/admin/api/state", json={"enabled": True})

    def test_system_prompt_masked_too(self, proxy_client, upstream):
        upstream.received.clear()
        proxy_client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [
                    {"role": "system", "content": "担当は佐藤花子です。"},
                    {"role": "user", "content": "よろしく"},
                ],
            },
        )
        sent = json.dumps(upstream.received[-1], ensure_ascii=False)
        assert "佐藤花子" not in sent

    def test_json_content_parts_masked(self, proxy_client, upstream):
        """マルチモーダル形式 (content が配列) でもマスクされること。"""
        upstream.received.clear()
        proxy_client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "田中太郎です。"}]}
                ],
            },
        )
        sent = json.dumps(upstream.received[-1], ensure_ascii=False)
        assert "田中太郎" not in sent
        assert "[氏名_1]" in sent

    def test_multipart_is_passed_and_counted(self, proxy_client):
        # multipart はマスク対象外。ただし統計にカウントし、素通しを可視化する。
        proxy_client.post(
            "/v1/chat/completions",
            files={"file": ("note.txt", "hello 田中太郎".encode(), "text/plain")},
        )
        stats = proxy_client.get("/admin/api/stats").json()
        assert stats["multipart_passed"] == 1

    def test_key_names_not_masked(self, proxy_client, upstream):
        """model 等の構造キーはマスクしない (上流が壊れないこと)。"""
        upstream.received.clear()
        chat(proxy_client, "テストです。", model="gpt-4o-mini")
        assert upstream.received[-1]["model"] == "gpt-4o-mini"

    def test_tools_schema_not_masked(self, proxy_client, upstream):
        """tools の JSON Schema は置換しない (関数呼び出しが壊れないこと)。"""
        upstream.received.clear()
        proxy_client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "天気は?"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "東京都の天気を返す",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                            },
                        },
                    }
                ],
            },
        )
        tool = upstream.received[-1]["tools"][0]["function"]
        assert tool["name"] == "get_weather"
        assert tool["parameters"]["properties"]["city"]["type"] == "string"

    def test_configured_api_key_becomes_bearer(self, proxy_client, upstream):
        proxy_client.post("/admin/api/state", json={"api_key": "sk-configured-key-xyz"})
        upstream.received_headers.clear()
        chat(proxy_client, "hello")
        assert upstream.received_headers[-1].get("authorization") == "Bearer sk-configured-key-xyz"
        proxy_client.post("/admin/api/state", json={"api_key": ""})

    def test_client_auth_forwarded_when_no_key_configured(self, proxy_client, upstream):
        upstream.received_headers.clear()
        proxy_client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer sk-from-client"},
        )
        assert upstream.received_headers[-1].get("authorization") == "Bearer sk-from-client"


# ---------------------------------------------------------------------------
# ストリーミング
# ---------------------------------------------------------------------------
class TestStreaming:
    def parse_sse(self, raw: str) -> str:
        """SSE 応答から本文を連結して取り出す。"""
        out = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            for ch in obj.get("choices", []):
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    out.append(delta["content"])
        return "".join(out)

    def test_stream_masked_upstream_restored_client(self, proxy_client, upstream):
        upstream.received.clear()
        text = "田中太郎の携帯は090-1234-5678、メールはa@example.co.jpです。"
        r = chat(proxy_client, text, stream=True)
        assert r.status_code == 200

        sent = json.dumps(upstream.received[-1], ensure_ascii=False)
        assert "田中太郎" not in sent
        assert "090-1234-5678" not in sent

        assert self.parse_sse(r.text) == text

    def test_stream_placeholder_split_across_chunks(self, proxy_client):
        """上流がプレースホルダを 2 チャンクに分割しても復元できること。"""
        text = "担当は佐藤花子です。"
        r = chat(proxy_client, text, stream=True)
        assert self.parse_sse(r.text) == text

    def test_stream_done_marker_preserved(self, proxy_client):
        r = chat(proxy_client, "こんにちは", stream=True)
        assert "[DONE]" in r.text

    def test_stream_without_mask_when_disabled(self, proxy_client):
        proxy_client.post("/admin/api/state", json={"masking_enabled": False})
        upstream_text = "田中太郎です。"
        r = chat(proxy_client, upstream_text, stream=True)
        assert self.parse_sse(r.text) == upstream_text
        proxy_client.post("/admin/api/state", json={"masking_enabled": True})


# ---------------------------------------------------------------------------
# 管理 API
# ---------------------------------------------------------------------------
class TestAdminApi:
    def test_toggle(self, proxy_client):
        before = proxy_client.get("/admin/api/state").json()["enabled"]
        r = proxy_client.post("/admin/api/toggle")
        assert r.json()["enabled"] is (not before)
        proxy_client.post("/admin/api/toggle")
        assert proxy_client.get("/admin/api/state").json()["enabled"] is before

    def test_update_upstream(self, proxy_client):
        r = proxy_client.post("/admin/api/state", json={"upstream": "https://api.deepseek.com"})
        assert r.status_code == 200
        assert proxy_client.get("/admin/api/state").json()["upstream"] == "https://api.deepseek.com"

    def test_api_key_never_returned(self, proxy_client):
        proxy_client.post("/admin/api/state", json={"api_key": "sk-secret-value-123"})
        state = proxy_client.get("/admin/api/state")
        assert "sk-secret-value-123" not in state.text
        assert state.json()["api_key_set"] is True

    def test_api_key_not_written_when_store_disabled(self, proxy_client, tmp_path):
        proxy_client.post(
            "/admin/api/state", json={"api_key": "sk-temp-9999", "store_api_key": False}
        )
        cfg_text = (tmp_path / "config.json").read_text(encoding="utf-8")
        assert "sk-temp-9999" not in cfg_text

    def test_detect_preview(self, proxy_client):
        r = proxy_client.get(
            "/admin/api/detect", params={"text": "田中太郎の携帯は090-1234-5678です。"}
        )
        body = r.json()
        assert body["count"] >= 2
        types = {e["type"] for e in body["entities"]}
        assert "PERSON" in types
        assert "PHONE" in types

    def test_stats_accumulate(self, proxy_client):
        proxy_client.post("/admin/api/stats/reset")
        chat(proxy_client, "田中太郎の携帯は090-1234-5678です。")
        st = proxy_client.get("/admin/api/stats").json()
        assert st["requests_total"] >= 1
        assert st["entities_masked"] >= 2
        assert st["placeholders_restored"] >= 2
        assert st["by_type"].get("PERSON", 0) >= 1

    def test_mapping_endpoint(self, proxy_client):
        chat(proxy_client, "田中太郎です。")
        r = proxy_client.get("/admin/api/mapping")
        assert r.status_code == 200
        assert "entries" in r.json()

    def test_presets_endpoint(self, proxy_client):
        r = proxy_client.get("/admin/api/presets")
        assert "openai" in r.json()
        assert "anthropic" in r.json()

    def test_invalid_update_rejected(self, proxy_client):
        r = proxy_client.post("/admin/api/state", json={"nonsense_field": 1})
        assert r.status_code == 400

    def test_post_returns_full_state_for_gui(self, proxy_client):
        """デスクトップ GUI は POST の応答をそのまま描画する。

        GET /admin/api/state と同じキーが最上位に含まれている必要がある。
        含まれていないと、トグル操作直後に状態表示が誤る。
        """
        r = proxy_client.post("/admin/api/state", json={"masking_enabled": False})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "applied" in body
        # GUI が参照するキー
        for key in (
            "enabled",
            "masking_enabled",
            "upstream",
            "port",
            "api_key_set",
            "stats",
        ):
            assert key in body, f"GUI が必要とするキーがありません: {key}"
        assert body["masking_enabled"] is False
        # GET と整合していること
        get_body = proxy_client.get("/admin/api/state").json()
        assert get_body["masking_enabled"] == body["masking_enabled"]
        proxy_client.post("/admin/api/state", json={"masking_enabled": True})

    def test_api_key_can_be_cleared_with_empty_string(self, proxy_client):
        """GUI の「キー消去」は空文字の POST で機能する。"""
        proxy_client.post("/admin/api/state", json={"api_key": "sk-to-be-cleared"})
        assert proxy_client.get("/admin/api/state").json()["api_key_set"] is True
        proxy_client.post("/admin/api/state", json={"api_key": ""})
        assert proxy_client.get("/admin/api/state").json()["api_key_set"] is False

    def test_audit_endpoint_records_mask_without_pii(self, proxy_client, upstream):
        chat(proxy_client, "田中太郎の電話は 090-1234-5678 です。")
        data = proxy_client.get("/admin/api/audit").json()
        assert data["enabled"] is True
        events = [e["event"] for e in data["entries"]]
        assert "proxy.mask" in events

        entry = next(e for e in data["entries"] if e["event"] == "proxy.mask")
        assert entry["count"] >= 2
        assert set(entry["types"]) & {"PERSON", "PHONE"}
        # 原文・検出値・生のセッション ID は記録しない
        dump = json.dumps(data, ensure_ascii=False)
        assert "田中太郎" not in dump
        assert "090-1234-5678" not in dump

    def test_audit_records_restore(self, proxy_client, upstream):
        chat(proxy_client, "メールは taro@example.jp です。")
        data = proxy_client.get("/admin/api/audit").json()
        assert any(e["event"] == "proxy.restore" for e in data["entries"])

    def test_audit_can_be_disabled_at_runtime(self, proxy_client, upstream):
        proxy_client.post("/admin/api/state", json={"audit_log": False})
        chat(proxy_client, "山田花子の電話は 090-1111-2222 です。")
        data = proxy_client.get("/admin/api/audit").json()
        assert data["enabled"] is False
        assert data["entries"] == []

    def test_vault_save_endpoint(self, proxy_client):
        assert proxy_client.post("/admin/api/vault/save").status_code == 200


# ---------------------------------------------------------------------------
# オリジン制御 (デスクトップ GUI 連携と、外部サイトからの盗み見防止)
# ---------------------------------------------------------------------------
class TestOriginGuard:
    def test_tauri_origin_allowed(self, proxy_client):
        """Tauri の webview から管理 API を読めること。"""
        r = proxy_client.get("/admin/api/state", headers={"Origin": "tauri://localhost"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "tauri://localhost"

    def test_windows_tauri_origin_allowed(self, proxy_client):
        r = proxy_client.get(
            "/admin/api/state", headers={"Origin": "http://tauri.localhost"}
        )
        assert r.status_code == 200

    def test_localhost_origin_allowed(self, proxy_client):
        r = proxy_client.get(
            "/admin/api/state", headers={"Origin": "http://localhost:3000"}
        )
        assert r.status_code == 200

    def test_remote_origin_blocked(self, proxy_client):
        """悪意あるサイトからの読み取りを拒否すること (原文が漏れない)。"""
        chat(proxy_client, "田中太郎です。")
        r = proxy_client.get(
            "/admin/api/mapping", headers={"Origin": "https://evil.example.com"}
        )
        assert r.status_code == 403
        assert "田中太郎" not in r.text

    def test_preflight_returns_cors(self, proxy_client):
        r = proxy_client.options(
            "/admin/api/state",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.status_code == 204
        assert "access-control-allow-origin" in {k.lower() for k in r.headers}

    def test_no_origin_still_works(self, proxy_client):
        """Origin を送らない CLI / SDK は従来どおり通ること。"""
        assert proxy_client.get("/admin/api/state").status_code == 200


class TestHostGuard:
    def test_allowed_hosts(self, proxy_client):
        for host in ("127.0.0.1", "localhost", "127.0.0.1:8787", "tauri.localhost"):
            resp = proxy_client.get("/healthz", headers={"host": host})
            assert resp.status_code == 200, host

    def test_unknown_host_is_blocked(self, proxy_client):
        resp = proxy_client.get("/healthz", headers={"host": "evil.example"})
        assert resp.status_code == 403
        assert resp.json()["error"]["type"] == "jp_pii_mask_host_denied"

    def test_origin_with_unknown_host_is_blocked(self, proxy_client):
        resp = proxy_client.get(
            "/admin/api/state",
            headers={"host": "evil.example", "origin": "http://localhost"},
        )
        assert resp.status_code == 403

    def test_non_loopback_bind_skips_host_check(self, upstream, tmp_path, monkeypatch):
        # 0.0.0.0 バインドでは LAN の実 IP が Host に載るため検証しない
        monkeypatch.setenv("JP_PII_MASK_HOME", str(tmp_path / "home"))
        store = ConfigStore(str(tmp_path / "config.json"))
        store.update(upstream=upstream.base_url, host="0.0.0.0", vault_persist=False)
        with TestClient(create_app(store)) as client:
            resp = client.get("/healthz", headers={"host": "192.168.1.20"})
        assert resp.status_code == 200


class TestSessionIsolation:
    def test_different_sessions_do_not_share_placeholders(self, proxy_client):
        r1 = chat(proxy_client, "田中太郎です。")
        r2 = proxy_client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "佐藤花子です。"}]},
            headers={"Authorization": "Bearer sk-other-session-1234"},
        )
        assert r1.status_code == 200 and r2.status_code == 200
        # どちらも原文に戻る (取り違えがない)
        assert r1.json()["choices"][0]["message"]["content"] == "田中太郎です。"
        assert r2.json()["choices"][0]["message"]["content"] == "佐藤花子です。"

    def test_explicit_session_header(self, proxy_client):
        r = proxy_client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [{"role": "user", "content": "田中太郎です。"}]},
            headers={"x-jp-pii-session": "my-app"},
        )
        assert r.json()["choices"][0]["message"]["content"] == "田中太郎です。"


class TestConfigPersistence:
    def test_settings_written_and_reloaded(self, tmp_path):
        store = ConfigStore(str(tmp_path / "config.json"))
        store.update(upstream="https://api.deepseek.com", upstream_preset="deepseek")
        store2 = ConfigStore(str(tmp_path / "config.json"))
        assert store2.config.upstream == "https://api.deepseek.com"
        assert store2.config.upstream_preset == "deepseek"

    def test_anthropic_preset_uses_x_api_key(self, tmp_path, upstream):
        store = ConfigStore(str(tmp_path / "config.json"))
        store.update(
            upstream=upstream.base_url,
            upstream_preset="anthropic",
            api_key="sk-ant-test-key",
            vault_persist=False,
        )
        with TestClient(create_app(store)) as client:
            upstream.received_headers.clear()
            client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            )
            headers = upstream.received_headers[-1]
            assert headers.get("x-api-key") == "sk-ant-test-key"
            assert headers.get("anthropic-version") == "2023-06-01"
