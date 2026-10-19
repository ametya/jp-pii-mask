"""疎通確認スクリプト (実際にプロセスを起動して検証する)。

テストスイート (in-process) とは別に、
  偽の上流 API サーバ (別プロセス)
  jp-pii-mask 本体 (別プロセス, 実 uvicorn)
  実 HTTP クライアント
の 3 者で通信し、「マスク -> 送信 -> 復元」が本当に成立することを確かめる。

使い方:
    python scripts/e2e_live.py

終了コード 0 = すべて PASS。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

PASS = "\033[92mPASS\033[0m" if os.name != "nt" else "PASS"
FAIL = "\033[91mFAIL\033[0m" if os.name != "nt" else "FAIL"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# 偽の上流 API サーバ
# ---------------------------------------------------------------------------
received: list[dict] = []


def build_upstream() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = json.loads(await request.body())
        received.append(body)
        content = body.get("messages", [{}])[-1].get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)

        if body.get("stream"):

            async def gen():
                half = max(1, len(content) // 2)
                for part in (content[:half], content[half:]):
                    chunk = {
                        "id": "chatcmpl-live",
                        "object": "chat.completion.chunk",
                        "model": body.get("model", "test"),
                        "choices": [{"index": 0, "delta": {"content": part}}],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        return {
            "id": "chatcmpl-live",
            "object": "chat.completion",
            "model": body.get("model", "test"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }

    return app


def start_upstream(port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(
        build_upstream(), host="127.0.0.1", port=port, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_port(port)
    return server, thread


def wait_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def terminate_tree(proc: subprocess.Popen, timeout: float = 10.0) -> bool:
    """子プロセスごと確実に終わらせる。

    PyInstaller の onefile 実行ファイルは、ブートローダ (親) が
    実際のアプリ (子) を spawn する。親だけを terminate すると
    子が孤児になってポートを掴んだまま残る。これは実測で確認した
    (exe を terminate したあと jp-pii-mask-server が生存していた)。

    そのため Windows では taskkill /T でプロセスツリーごと終わらせる。
    戻り値: 完全に停止したと確認できたら True。
    """
    if proc.poll() is not None and os.name != "nt":
        return True

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False

    # ツリーの残骸がないか確認する (taskkill は非同期に効くことがある)
    for _ in range(20):
        if not _descendants_alive(proc.pid):
            return True
        time.sleep(0.1)
    return False


def _descendants_alive(pid: int) -> bool:
    """pid を親とする子プロセスがまだ生きているか (Windows のみ)。"""
    if os.name != "nt":
        return False
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Get-CimInstance Win32_Process | Where-Object {{ $_.ParentProcessId -eq {pid} }} | Measure-Object | Select-Object -ExpandProperty Count"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(out.stdout.strip() or "0") > 0
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="jp-pii-mask の疎通確認")
    parser.add_argument(
        "--exe",
        default=None,
        help="検証対象の実行ファイル (PyInstaller 生成物など)。省略時は python -m jp_pii_mask.cli を使う。",
    )
    args = parser.parse_args()

    print("=" * 66)
    print("  jp-pii-mask 疎通確認 (実プロセス・実 HTTP)")
    if args.exe:
        print(f"  対象: {args.exe}")
    print("=" * 66)

    upstream_port = free_port()
    proxy_port = free_port()
    workdir = tempfile.mkdtemp(prefix="jp-pii-mask-e2e-")

    print(f"\n[1] 偽の上流 API サーバを起動 (127.0.0.1:{upstream_port})")
    up_server, _ = start_upstream(upstream_port)
    check("偽上流サーバ起動", wait_port(upstream_port), f"port={upstream_port}")

    print(f"\n[2] jp-pii-mask 本体を別プロセスで起動 (127.0.0.1:{proxy_port})")
    env = dict(os.environ)
    env["JP_PII_MASK_HOME"] = workdir
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT)
    log_path = Path(workdir) / "proxy.log"
    # 子プロセスの出力先として開いたままにする必要があるため、
    # with 文ではなく finally で閉じる (SIM115)。
    log_file = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    if args.exe:
        cmd = [
            str(Path(args.exe).resolve()),
            "serve",
            "--host", "127.0.0.1",
            "--port", str(proxy_port),
            "--upstream", f"http://127.0.0.1:{upstream_port}",
        ]
    else:
        cmd = [
            sys.executable, "-m", "jp_pii_mask.cli",
            "serve",
            "--host", "127.0.0.1",
            "--port", str(proxy_port),
            "--upstream", f"http://127.0.0.1:{upstream_port}",
        ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    started = wait_port(proxy_port)
    if not started:
        print(log_path.read_text(encoding="utf-8", errors="replace"))
    check("プロキシ起動 (serve サブコマンド)", started, f"pid={proc.pid} port={proxy_port}")

    if not started:
        proc.kill()
        up_server.should_exit = True
        return 1

    base = f"http://127.0.0.1:{proxy_port}"
    original = "田中太郎の携帯は090-1234-5678、メールはtaro@example.co.jpです。"

    try:
        with httpx.Client(timeout=30.0) as client:
            print("\n[3] ヘルスチェックと制御パネル")
            r = client.get(f"{base}/healthz")
            check("GET /healthz が 200", r.status_code == 200, str(r.json()))
            r = client.get(f"{base}/")
            check(
                "制御パネル (/) が配信される",
                r.status_code == 200 and "jp-pii-mask" in r.text,
                f"{len(r.text)} bytes",
            )

            print("\n[4] 非ストリーミング: マスク -> 送信 -> 復元")
            received.clear()
            r = client.post(
                f"{base}/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": original}],
                },
            )
            check("POST /v1/chat/completions が 200", r.status_code == 200)
            sent = json.dumps(received[-1], ensure_ascii=False) if received else ""
            check(
                "上流に届いた本文がマスクされている",
                "田中太郎" not in sent and "090-1234-5678" not in sent,
                sent[:90] + ("…" if len(sent) > 90 else ""),
            )
            check(
                "上流にプレースホルダが届いている",
                "[氏名_1]" in sent and "[電話番号_1]" in sent,
            )
            got = r.json()["choices"][0]["message"]["content"]
            check("クライアントへの応答が原文に復元されている", got == original, got)

            print("\n[5] ストリーミング (SSE) の逐次復元")
            received.clear()
            chunks: list[str] = []
            with client.stream(
                "POST",
                f"{base}/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": original}],
                    "stream": True,
                },
            ) as resp:
                check("ストリーム応答が 200", resp.status_code == 200)
                for line in resp.iter_lines():
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
                        d = ch.get("delta") or {}
                        if d.get("content"):
                            chunks.append(d["content"])
            streamed = "".join(chunks)
            sent = json.dumps(received[-1], ensure_ascii=False) if received else ""
            check("ストリームでも上流にはマスク済みが届く", "田中太郎" not in sent)
            check("ストリームの復元結果が原文と一致", streamed == original, streamed)

            print("\n[6] 管理 API (デスクトップ GUI が使う経路)")
            r = client.get(f"{base}/admin/api/state")
            state = r.json()
            check("GET /admin/api/state", r.status_code == 200 and state["enabled"] is True)
            check("API キーは応答に含まれない", "api_key" not in state or state["api_key"] == "")
            check(
                "統計が記録されている",
                state["stats"]["entities_masked"] >= 2,
                f"masked={state['stats']['entities_masked']} restored={state['stats']['placeholders_restored']}",
            )

            r = client.get(f"{base}/admin/api/audit")
            audit = r.json()
            events = [e["event"] for e in audit["entries"]]
            check(
                "監査 API: マスク/復元が記録されている",
                r.status_code == 200 and "proxy.mask" in events and "proxy.restore" in events,
                str(events[:5]),
            )
            check(
                "監査ログに原文が含まれない",
                "田中太郎" not in json.dumps(audit, ensure_ascii=False),
            )
            check(
                "監査ログファイルが設定ディレクトリに作られる",
                (Path(workdir) / "audit.log").is_file(),
                str(Path(workdir) / "audit.log"),
            )

            r = client.get(
                f"{base}/admin/api/detect",
                params={"text": "佐藤花子、〒100-0001 東京都千代田区千代田1-1"},
            )
            types = {e["type"] for e in r.json()["entities"]}
            check("検出プレビュー API", r.status_code == 200 and len(types) >= 2, str(types))

            r = client.post(f"{base}/admin/api/toggle")
            check("トグル API (OFF)", r.json()["enabled"] is False)
            received.clear()
            client.post(
                f"{base}/v1/chat/completions",
                json={"model": "m", "messages": [{"role": "user", "content": "田中太郎です。"}]},
            )
            sent = json.dumps(received[-1], ensure_ascii=False)
            check("OFF のときは素通しする", "田中太郎" in sent)
            r = client.post(f"{base}/admin/api/toggle")
            check("トグル API (ON)", r.json()["enabled"] is True)

            print("\n[7] オリジン制御 (GUI 連携と外部サイト遮断)")
            r = client.get(
                f"{base}/admin/api/state", headers={"Origin": "tauri://localhost"}
            )
            check(
                "Tauri webview からのアクセスを許可",
                r.status_code == 200
                and r.headers.get("access-control-allow-origin") == "tauri://localhost",
            )
            r = client.get(
                f"{base}/admin/api/mapping", headers={"Origin": "https://evil.example.com"}
            )
            check("外部オリジンからの読み取りを拒否 (403)", r.status_code == 403)

            r = client.get(f"{base}/admin/api/state", headers={"Host": "evil.example"})
            check("未知の Host からのアクセスを拒否 (403)", r.status_code == 403)
    finally:
        print("\n[8] 後始末")
        stopped = terminate_tree(proc)
        log_file.close()
        check("プロキシプロセスを停止 (プロセスツリーごと)", stopped, f"pid={proc.pid}")
        # ポートが実際に解放されたことまで確かめる (孤児が掴んでいないこと)
        time.sleep(0.3)
        check(
            "ポートが解放された (孤児プロセスなし)",
            not wait_port(proxy_port, timeout=1.0),
            f"port={proxy_port}",
        )
        up_server.should_exit = True
        time.sleep(0.3)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 66)
    print(f"  結果: {passed}/{total} PASS")
    if passed != total:
        print("  失敗:")
        for name, ok, detail in results:
            if not ok:
                print(f"    - {name} {detail}")
    print("=" * 66)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
