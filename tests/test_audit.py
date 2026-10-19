"""監査ログ (audit.py) のテスト。

方針: 「書ける・読める・回せる」に加えて、監査ログ自体が
漏洩面にならないこと (原文を記録しない) を proxy テストで確認する。
"""

import json

from jp_pii_mask.audit import AuditLog, hash_id


def _lines(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestAuditLog:
    def test_record_and_tail_newest_first(self, tmp_path):
        log = AuditLog(tmp_path / "audit.log")
        log.record("file.mask", kind="pdf", entities=3, types={"PERSON": 1})
        log.record("proxy.mask", count=2)
        entries = log.tail(10)
        assert [e["event"] for e in entries] == ["proxy.mask", "file.mask"]
        assert entries[1]["types"] == {"PERSON": 1}
        assert all("ts" in e for e in entries)

    def test_tail_limit(self, tmp_path):
        log = AuditLog(tmp_path / "audit.log")
        for i in range(10):
            log.record("evt", n=i)
        assert [e["n"] for e in log.tail(3)] == [9, 8, 7]
        assert log.tail(0) == []

    def test_disabled_writes_nothing(self, tmp_path):
        path = tmp_path / "audit.log"
        AuditLog(path, enabled=False).record("file.mask", entities=1)
        assert not path.exists()

    def test_rotation_keeps_backups_and_latest(self, tmp_path):
        path = tmp_path / "audit.log"
        log = AuditLog(path, max_bytes=200, backups=2)
        for i in range(20):
            log.record("evt", n=i, pad="x" * 40)
        assert path.exists()
        assert (tmp_path / "audit.log.1").exists()
        assert log.tail(1)[0]["n"] == 19

    def test_broken_line_is_ignored(self, tmp_path):
        path = tmp_path / "audit.log"
        log = AuditLog(path)
        log.record("good", n=1)
        with path.open("a", encoding="utf-8") as f:
            f.write("{not json\n")
        entries = log.tail(5)
        assert len(entries) == 1
        assert entries[0]["event"] == "good"

    def test_write_failure_is_swallowed(self, tmp_path):
        # 書けない場所 (ディレクトリ) を指しても例外を投げない
        target = tmp_path / "adir"
        target.mkdir()
        AuditLog(target).record("evt")

    def test_clear_removes_all_files(self, tmp_path):
        path = tmp_path / "audit.log"
        log = AuditLog(path, max_bytes=100, backups=1)
        for i in range(10):
            log.record("evt", n=i, pad="x" * 40)
        log.clear()
        assert not path.exists()
        assert not (tmp_path / "audit.log.1").exists()
        assert log.tail(5) == []


class TestHashId:
    def test_stable_short_and_non_reversible(self):
        assert hash_id("session-abc") == hash_id("session-abc")
        assert len(hash_id("session-abc")) == 12
        assert "session" not in hash_id("session-abc")
        assert hash_id("a") != hash_id("b")
