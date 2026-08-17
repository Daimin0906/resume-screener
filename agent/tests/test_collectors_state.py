"""
测试收集器与状态管理（文件夹扫描 + 防重复）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.collectors import FolderCollector
from agent.state import StateStore, fingerprint


class TestStateStore:
    def test_mark_and_check(self, tmp_path):
        state = StateStore(tmp_path)
        fp = fingerprint("简历内容ABC")
        assert not state.is_processed(fp)
        state.mark_processed([fp])
        assert state.is_processed(fp)

    def test_persists_across_instances(self, tmp_path):
        state1 = StateStore(tmp_path)
        state1.mark_processed([fingerprint("x")])
        state2 = StateStore(tmp_path)
        assert state2.is_processed(fingerprint("x"))


class TestFolderCollector:
    def test_collect_new_files(self, tmp_path):
        state = StateStore(tmp_path / "data")
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "a.txt").write_text("姓名：张三\n技能：Python", encoding="utf-8")
        (inbox / "sub").mkdir()
        (inbox / "sub" / "b.md").write_text("姓名：李四\n技能：Java", encoding="utf-8")

        collector = FolderCollector(inbox, state)
        found = collector.collect()
        assert len(found) == 2  # 支持子目录
        names = {f["filename"] for f in found}
        assert names == {"a.txt", "b.md"}

    def test_skips_processed(self, tmp_path):
        state = StateStore(tmp_path / "data")
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        text = "姓名：张三\n技能：Python"
        (inbox / "a.txt").write_text(text, encoding="utf-8")
        state.mark_processed([fingerprint(text)])

        collector = FolderCollector(inbox, state)
        assert collector.collect() == []  # 已处理，不再收集

    def test_skips_unsupported_and_empty(self, tmp_path):
        state = StateStore(tmp_path / "data")
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "a.exe").write_text("x", encoding="utf-8")
        (inbox / "empty.txt").write_text("", encoding="utf-8")

        collector = FolderCollector(inbox, state)
        assert collector.collect() == []


if __name__ == "__main__":
    pytest.main([__file__])
