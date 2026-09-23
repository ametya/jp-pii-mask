"""コマンドライン入口。

使い方:
    jp-pii-mask serve                      # 既定 (127.0.0.1:8787, OpenAI 転送)
    jp-pii-mask serve --port 8787 --upstream https://api.deepseek.com
    jp-pii-mask detect "テキスト"          # 検出だけ試す
    jp-pii-mask mask "テキスト"            # マスク結果を表示
    jp-pii-mask demo                       # マスク→復元の往復を実演
    jp-pii-mask config --show              # 現在の設定を表示
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import ConfigStore, UPSTREAM_PRESETS, config_dir
from .detect import Detector
from .masking import Masker
from .vault import Vault


def _make_streams_robust() -> None:
    """標準出力・標準エラーを「落ちない」設定にする。

    Windows の既定コードページ (cp932) は絵文字を表現できず、
    それを print しようとすると UnicodeEncodeError でプロセスごと終了する。
    特に Tauri からサイドカーとして起動した場合は標準出力がパイプに
    なるため、この問題が必ず発生する。

    解決策: エンコードできない文字は例外ではなく置換文字にする。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .proxy import create_app

    store = ConfigStore(args.config)
    cfg = store.config

    changes = {}
    if args.port:
        changes["port"] = args.port
    if args.host:
        changes["host"] = args.host
    if args.upstream:
        changes["upstream"] = args.upstream
    if args.preset:
        changes["upstream_preset"] = args.preset
        preset = UPSTREAM_PRESETS.get(args.preset)
        if preset and preset.get("base_url") and not args.upstream:
            changes["upstream"] = preset["base_url"]
    if args.ner:
        changes["use_ner"] = True
    if getattr(args, "redact_only", False):
        changes["redact_only"] = True
    if changes:
        cfg = store.update(**changes)

    app = create_app(store)
    banner = f"""
  jp-pii-mask v0.1.0
  ─────────────────────────────────────────
  制御パネル : http://{cfg.host}:{cfg.port}/
  転送先 API : {cfg.normalized_upstream()}
  保護状態   : {"ON" if cfg.enabled else "OFF"}
  設定ファイル: {store.path}
  ─────────────────────────────────────────
  クライアントの base_url を上記パネルの URL に向けてください。
"""
    print(banner, flush=True)

    uvicorn.run(
        app,
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level,
        access_log=False,
    )
    return 0


def _cmd_detect(args: argparse.Namespace) -> int:
    det = Detector(use_ner=args.ner, min_confidence=args.min_confidence)
    ents = det.detect(args.text)
    if not ents:
        print("検出なし", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps([e.as_dict() for e in ents], ensure_ascii=False, indent=2))
    else:
        for e in ents:
            print(f"{e.type:20s} {e.confidence:.2f}  {e.text}")
    return 0


def _cmd_mask(args: argparse.Namespace) -> int:
    det = Detector(use_ner=args.ner, min_confidence=args.min_confidence)
    vault = Vault(encrypt=False)
    masker = Masker(detector=det, min_confidence=args.min_confidence)
    res = masker.mask(args.text, vault)
    if args.json:
        print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(res.text)
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    sample = (
        "お世話になっております。株式会社サンプル商事 営業部の田中太郎です。\n"
        "私の携帯は090-1234-5678、メールは taro.tanaka@example.co.jp です。\n"
        "請求書の送付先は〒100-0001 東京都千代田区千代田1-1 サンプルビル5階です。\n"
        "マイナンバーは 123456789018、法人番号は 3234567890123 です。\n"
        "念のため、実家の電話 03-1234-5678 も控えておいてください。"
    )
    text = args.text or sample
    det = Detector(use_ner=args.ner, min_confidence=args.min_confidence)
    vault = Vault(encrypt=False)
    masker = Masker(detector=det, min_confidence=args.min_confidence)

    print("─" * 60)
    print("【原文】")
    print(text)
    res = masker.mask(text, vault)
    print("\n【マスク後 (API に送られる形)】")
    print(res.text)
    print(f"\n  検出: {res.count} 件 / 種別: {res.types_summary()}")

    print("\n【復元後 (API から返ってきた想定)】")
    restored = masker.unmask(res.text, vault)
    print(restored.text)
    ok = restored.text == text
    print("\n" + "─" * 60)
    print(f"往復一致: {'PASS' if ok else 'FAIL'}  (復元 {restored.restored} 件 / 未復元 {len(restored.missing)} 件)")
    return 0 if ok else 2


def _cmd_config(args: argparse.Namespace) -> int:
    store = ConfigStore(args.config)
    cfg = store.config
    if args.set:
        changes = {}
        for pair in args.set:
            if "=" not in pair:
                print(f"無視: {pair} (key=value 形式で指定してください)", file=sys.stderr)
                continue
            k, v = pair.split("=", 1)
            cur = getattr(cfg, k, None)
            if isinstance(cur, bool):
                v = v.lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int):
                try:
                    v = int(v)
                except ValueError:
                    pass
            elif isinstance(cur, float):
                try:
                    v = float(v)
                except ValueError:
                    pass
            changes[k] = v
        store.update(**changes)
        print(f"更新しました: {sorted(changes)}")

    if args.show or not args.set:
        pub = cfg.to_public_dict()
        print(f"設定ファイル: {store.path}")
        print(json.dumps(pub, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jp-pii-mask",
        description="日本語に特化した個人情報マスキング／復元プロキシ",
    )
    p.add_argument("--config", help="設定ファイルのパス", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="プロキシを起動する")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--upstream", default=None, help="転送先のベース URL")
    s.add_argument("--preset", default=None, help="転送先プリセット (openai/anthropic/deepseek/gemini/ollama/custom)")
    s.add_argument("--ner", action="store_true", help="NER (GiNZA) を使う")
    s.add_argument("--redact-only", dest="redact_only", action="store_true", help="復元せず完全に消去する")
    s.set_defaults(func=_cmd_serve)

    d = sub.add_parser("detect", help="テキストの個人情報を検出する")
    d.add_argument("text")
    d.add_argument("--ner", action="store_true")
    d.add_argument("--min-confidence", type=float, default=0.5)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_detect)

    m = sub.add_parser("mask", help="テキストをマスクする")
    m.add_argument("text")
    m.add_argument("--ner", action="store_true")
    m.add_argument("--min-confidence", type=float, default=0.5)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=_cmd_mask)

    dm = sub.add_parser("demo", help="マスク→復元の往復を実演する")
    dm.add_argument("text", nargs="?", default=None)
    dm.add_argument("--ner", action="store_true")
    dm.add_argument("--min-confidence", type=float, default=0.5)
    dm.set_defaults(func=_cmd_demo)

    c = sub.add_parser("config", help="設定を表示・変更する")
    c.add_argument("--show", action="store_true")
    c.add_argument("--set", action="append", help="key=value 形式 (複数指定可)")
    c.set_defaults(func=_cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    _make_streams_robust()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n中断しました", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
