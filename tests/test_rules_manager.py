"""
测试筛选规则与反馈日志管理器（RulesManager）

全部使用 tmp_path 隔离目录，不触碰真实 rules/。
"""
import json
import threading
import os

import pytest
from unittest.mock import MagicMock, patch

from app.core.rules_manager import RulesManager, InsufficientFeedbackError
from app.core.llm_client import LLMClient

TEST_FEEDBACK = {
    "resume_id": "resume_001",
    "query_id": "query_001",
    "candidate_name": "张三",
    "ai_classification": "interview",
    "ai_reason": "技能匹配度高",
    "overall_score": 0.87,
    "human_classification": "reject",
    "human_reason": "只是关键词堆砌，无独立负责项目的证据",
}


def make_manager(tmp_path, **kwargs):
    return RulesManager(MagicMock(), str(tmp_path / "rules"), **kwargs)


class TestAddFeedback:
    def test_add_feedback_valid(self, tmp_path):
        manager = make_manager(tmp_path)
        feedback_id = manager.add_feedback(dict(TEST_FEEDBACK))
        assert feedback_id

        data = json.load(open(manager.feedback_path, encoding="utf-8"))
        assert data["total_count"] == 1
        assert len(data["entries"]) == 1
        entry = data["entries"][0]
        assert entry["feedback_id"] == feedback_id
        assert entry["human_classification"] == "reject"
        assert entry["candidate_name"] == "张三"

    def test_add_feedback_invalid_classification(self, tmp_path):
        manager = make_manager(tmp_path)
        with pytest.raises(ValueError):
            manager.add_feedback({**TEST_FEEDBACK, "human_classification": "unknown"})

    def test_add_feedback_trim(self, tmp_path):
        manager = make_manager(tmp_path, max_feedback_entries=3)
        for i in range(5):
            manager.add_feedback({**TEST_FEEDBACK, "resume_id": f"r{i}"})
        data = json.load(open(manager.feedback_path, encoding="utf-8"))
        assert len(data["entries"]) == 3          # 裁剪保留最新
        assert data["total_count"] == 5           # 累计不丢
        assert [e["resume_id"] for e in data["entries"]] == ["r2", "r3", "r4"]

    def test_list_feedback_newest_first(self, tmp_path):
        manager = make_manager(tmp_path)
        manager.add_feedback({**TEST_FEEDBACK, "resume_id": "r1"})
        manager.add_feedback({**TEST_FEEDBACK, "resume_id": "r2"})
        entries = manager.list_feedback()
        assert [e["resume_id"] for e in entries] == ["r2", "r1"]

    def test_get_feedback_map_by_query(self, tmp_path):
        """纠正按简历生效，跨查询匹配（纠正针对候选人而非某次查询）"""
        manager = make_manager(tmp_path)
        manager.add_feedback({**TEST_FEEDBACK, "resume_id": "r1", "query_id": "q1"})
        manager.add_feedback({**TEST_FEEDBACK, "resume_id": "r2", "query_id": "q2"})
        # 即使传入 q1，r2（q2 的反馈）也返回——跨查询生效
        map_q1 = manager.get_feedback_map("q1")
        assert set(map_q1.keys()) == {"r1", "r2"}
        assert map_q1["r1"]["human_classification"] == "reject"

    def test_get_feedback_map_latest_wins(self, tmp_path):
        """同一候选人多次纠正时，最新一条生效"""
        manager = make_manager(tmp_path)
        manager.add_feedback({**TEST_FEEDBACK, "resume_id": "r1", "human_classification": "reject"})
        manager.add_feedback({**TEST_FEEDBACK, "resume_id": "r1", "human_classification": "interview"})
        feedback_map = manager.get_feedback_map_for_resumes()
        assert feedback_map["r1"]["human_classification"] == "interview"

    def test_concurrent_add_feedback(self, tmp_path):
        """多线程并发写，文件保持合法 JSON（验证锁与原子写）。"""
        manager = make_manager(tmp_path)
        threads = [
            threading.Thread(target=manager.add_feedback, args=({**TEST_FEEDBACK, "resume_id": f"r{i}"},))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = json.load(open(manager.feedback_path, encoding="utf-8"))
        assert len(data["entries"]) == 20


class TestRulesPersistence:
    def test_get_active_rules_default_when_missing(self, tmp_path):
        manager = make_manager(tmp_path)
        rules = manager.get_active_rules()
        assert rules["version"] == 0
        assert rules["rules"] == []
        assert rules["active"] is True

    def test_active_rules_text_empty_when_no_rules(self, tmp_path):
        manager = make_manager(tmp_path)
        assert manager.active_rules_text() == ""

    def test_corrupted_file_backed_up(self, tmp_path):
        manager = make_manager(tmp_path)
        manager.rules_dir.mkdir(parents=True, exist_ok=True)
        manager.rules_path.write_text("{not valid json", encoding="utf-8")
        rules = manager.get_active_rules()
        assert rules["version"] == 0
        # 备份文件存在
        backups = list(manager.rules_dir.glob("screening_rules.json.bak.*"))
        assert len(backups) == 1

    def test_pending_feedback_count(self, tmp_path):
        manager = make_manager(tmp_path)
        assert manager.pending_feedback_count() == 0
        for i in range(3):
            manager.add_feedback({**TEST_FEEDBACK, "resume_id": f"r{i}"})
        assert manager.pending_feedback_count() == 3


class TestSummarizeRules:
    def _seed_feedback(self, manager, n=3):
        for i in range(n):
            manager.add_feedback({**TEST_FEEDBACK, "resume_id": f"r{i}"})

    def test_insufficient_feedback(self, tmp_path):
        manager = make_manager(tmp_path)
        manager.add_feedback(dict(TEST_FEEDBACK))
        with pytest.raises(InsufficientFeedbackError):
            manager.summarize_rules(min_feedback=3)
        # 文件未被写入
        assert not manager.rules_path.exists()

    def test_summarize_success(self, tmp_path):
        manager = make_manager(tmp_path)
        self._seed_feedback(manager)
        manager.llm_client.generate_text.return_value = json.dumps({
            "rules": ["不看关键词罗列，只看独立负责的真实项目与结果"],
            "summary": "AI 高估关键词匹配，HR 认为需看真实证据",
        }, ensure_ascii=False)

        new_rules = manager.summarize_rules(min_feedback=3)
        assert new_rules["version"] == 1
        assert len(new_rules["rules"]) == 1
        assert len(new_rules["based_on_feedback_ids"]) == 3
        assert manager.pending_feedback_count() == 0

        # 第二次总结：版本 +1，旧版本进 history
        manager.llm_client.generate_text.return_value = json.dumps({
            "rules": ["新规则"],
            "summary": "新总结",
        }, ensure_ascii=False)
        manager.add_feedback({**TEST_FEEDBACK, "resume_id": "r_new"})
        new_rules2 = manager.summarize_rules(min_feedback=1)
        assert new_rules2["version"] == 2
        data = json.load(open(manager.rules_path, encoding="utf-8"))
        # 每次总结都把被替换的版本压入 history（初始 v0 + 第一次总结的 v1）
        assert [h["version"] for h in data["history"]] == [0, 1]

    def test_summarize_with_code_fence(self, tmp_path):
        manager = make_manager(tmp_path)
        self._seed_feedback(manager)
        manager.llm_client.generate_text.return_value = (
            "```json\n" + json.dumps({"rules": ["规则A"], "summary": "总结B"}, ensure_ascii=False) + "\n```"
        )
        new_rules = manager.summarize_rules(min_feedback=3)
        assert new_rules["version"] == 1
        assert "规则A" in new_rules["rules"]

    def test_summarize_unparseable_no_write(self, tmp_path):
        manager = make_manager(tmp_path)
        self._seed_feedback(manager)
        manager.llm_client.generate_text.return_value = "这是一段无法解析的文字"
        with pytest.raises(ValueError):
            manager.summarize_rules(min_feedback=3)
        assert not manager.rules_path.exists()  # 版本不变、文件不动

    def test_rules_trim_to_max(self, tmp_path):
        manager = make_manager(tmp_path, max_rules=2)
        self._seed_feedback(manager)
        manager.llm_client.generate_text.return_value = json.dumps({
            "rules": ["规则1", "规则2", "规则3"],
            "summary": "s",
        }, ensure_ascii=False)
        new_rules = manager.summarize_rules(min_feedback=3)
        assert len(new_rules["rules"]) == 2


if __name__ == "__main__":
    pytest.main([__file__])
