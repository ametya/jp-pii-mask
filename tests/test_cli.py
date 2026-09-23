"""CLI のテスト。

特に重要なのは「Windows の cp932 環境で落ちないこと」。
PyInstaller で単一実行ファイル化し Tauri のサイドカーとして起動すると
標準出力がパイプになり、絵文字などを print すると
UnicodeEncodeError でプロセスごと終了してしまう。
"""

import io
import json
import sys

import pytest

from jp_pii_mask.cli import build_parser, main


class TestStreamRobustness:
    def test_reconfigure_sets_replace_errors(self):
        """エンコード不能文字で例外を出さない設定になっていること。"""
        from jp_pii_mask.cli import _make_streams_robust

        original_out, original_err = sys.stdout, sys.stderr
        buf_out = io.TextIOWrapper(io.BytesIO(), encoding="cp932", errors="strict")
        buf_err = io.TextIOWrapper(io.BytesIO(), encoding="cp932", errors="strict")
        try:
            sys.stdout, sys.stderr = buf_out, buf_err
            _make_streams_robust()
            # 例外にならず、置換される
            print("🌙 テスト", file=sys.stdout)
            sys.stdout.flush()
            assert sys.stdout.errors == "replace"
            assert sys.stderr.errors == "replace"
        finally:
            sys.stdout, sys.stderr = original_out, original_err

    def test_cp932_console_does_not_crash_on_emoji(self):
        original = sys.stdout
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp932", errors="strict")
        try:
            sys.stdout = stream
            from jp_pii_mask.cli import _make_streams_robust

            _make_streams_robust()
            print("🌙 ", flush=True)  # 例外にならないこと
        finally:
            sys.stdout = original
        # cp932 で表現できない絵文字は置換文字になっている
        assert b"?" in raw.getvalue()


class TestParser:
    def test_subcommands_present(self):
        parser = build_parser()
        for name in ("serve", "detect", "mask", "demo", "config"):
            assert name in parser._subparsers._group_actions[0].choices

    def test_requires_command(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_serve_defaults(self):
        parser = build_parser()
        ns = parser.parse_args(["serve"])
        assert ns.host is None
        assert ns.port is None
        assert ns.ner is False


class TestCommands:
    def test_demo_passes_roundtrip(self, capsys):
        rc = main(["demo"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "往復一致: PASS" in out
        assert "未復元 0 件" in out

    def test_demo_uses_checksum_valid_numbers(self, capsys):
        """デモ本文の公的番号は検査用数字が正しく、実際にマスクされること。"""
        main(["demo"])
        out = capsys.readouterr().out
        assert "[マイナンバー_1]" in out
        assert "[法人番号_1]" in out

    def test_detect_text(self, capsys):
        rc = main(["detect", "田中太郎の携帯は090-1234-5678です。"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "PERSON" in out
        assert "PHONE" in out

    def test_detect_json(self, capsys):
        rc = main(["detect", "田中太郎です。", "--json"])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data[0]["type"] == "PERSON"
        assert data[0]["text"] == "田中太郎"

    def test_detect_no_hit_returns_nonzero(self, capsys):
        rc = main(["detect", "本日の会議は10時からです。"])
        assert rc == 1

    def test_mask(self, capsys):
        rc = main(["mask", "田中太郎の携帯は090-1234-5678です。"])
        out = capsys.readouterr().out.strip()
        assert rc == 0
        assert "田中太郎" not in out
        assert "[氏名_1]" in out

    def test_mask_json(self, capsys):
        rc = main(["mask", "田中太郎です。", "--json"])
        out = capsys.readouterr().out
        assert rc == 0
        data = json.loads(out)
        assert data["count"] == 1
        assert data["types"] == {"PERSON": 1}

    def test_config_show(self, capsys, tmp_path):
        rc = main(["--config", str(tmp_path / "c.json"), "config", "--show"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "設定ファイル" in out

    def test_config_set_and_persist(self, capsys, tmp_path):
        path = str(tmp_path / "c.json")
        rc = main(["--config", path, "config", "--set", "min_confidence=0.7"])
        assert rc == 0
        from jp_pii_mask.config import ConfigStore

        assert ConfigStore(path).config.min_confidence == 0.7

    def test_config_set_bool(self, tmp_path):
        path = str(tmp_path / "c.json")
        main(["--config", path, "config", "--set", "enabled=false"])
        from jp_pii_mask.config import ConfigStore

        assert ConfigStore(path).config.enabled is False

    def test_config_set_int(self, tmp_path):
        path = str(tmp_path / "c.json")
        main(["--config", path, "config", "--set", "port=9000"])
        from jp_pii_mask.config import ConfigStore

        assert ConfigStore(path).config.port == 9000
