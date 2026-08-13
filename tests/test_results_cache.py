"""
测试筛选结果缓存（P2）：命中/失效/规则版本变化
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from config.config import settings

client = TestClient(app)

QUERY_DATA = {
    "id": "query_001",
    "text": "Python开发",
    "metadata": {"keywords": ["Python"], "required_skills": ["Python"]},
    "created_at": "2025-01-01T00:00:00",
}

CANDIDATE = {
    "id": "candidate_001",
    "rank": 1,
    "name": "张三",
    "metadata": {"name": "张三", "skills": ["Python"], "work_experience": [], "education": []},
    "scores": {"overall_score": 0.9, "skill_score": 0.9},
    "classification": "interview",
}


def _mock_pipeline(analyzed, mock_analyzer):
    formatted = {
        "total_candidates": len(analyzed),
        "candidates": [{
            "id": c["id"], "rank": c.get("rank", 1), "name": c.get("name", ""),
            "contact_info": {}, "scores": c.get("scores", {}),
            "basic_info": {"skills": ["Python"], "work_experience": [], "education": []},
            "analysis": c.get("analysis", ""),
            "classification": c.get("classification", "review"),
            "classification_reason": c.get("classification_reason", ""),
            "classification_source": c.get("classification_source", "llm"),
            "assessment": c.get("assessment", {}), "corrected_by_human": False,
            "strengths": c.get("strengths", []), "risks": c.get("risks", []),
        } for c in analyzed],
        "summary": {"average_score": 0.9},
    }
    qs = MagicMock()
    qs.__contains__.return_value = True
    qs.__getitem__.return_value = QUERY_DATA
    return patch.multiple(
        "app.api.routes",
        query_storage=qs,
        retriever=MagicMock(retrieve=MagicMock(return_value=analyzed)),
        hard_filter=MagicMock(filter_resumes=MagicMock(return_value=analyzed)),
        scorer=MagicMock(score_resumes=MagicMock(return_value=analyzed)),
        ranker=MagicMock(rank_resumes=MagicMock(return_value=analyzed)),
        candidate_analyzer=mock_analyzer,
        result_formatter=MagicMock(format_results=MagicMock(return_value=formatted)),
    )


class TestResultsCache:
    def test_cache_hit_avoids_second_analysis(self, monkeypatch):
        """开启缓存：第一次 GET 调 analyzer，第二次同版本命中缓存不调"""
        monkeypatch.setattr(settings, "RESULTS_CACHE_ENABLED", True)
        monkeypatch.setattr(settings, "RESULTS_CACHE_TTL_SECONDS", 300)

        mock_analyzer = MagicMock()
        mock_analyzer.analyze_candidates.return_value = [{
            **CANDIDATE, "analysis": "报告", "classification": "interview",
        }]

        ctx = _mock_pipeline([CANDIDATE], mock_analyzer)
        with ctx:
            resp1 = client.get("/api/v1/results/query_001")
            resp2 = client.get("/api/v1/results/query_001")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["candidates"][0]["classification"] == "interview"
        # 第二次命中缓存：analyze_candidates 只调用一次
        assert mock_analyzer.analyze_candidates.call_count == 1

    def test_feedback_invalidates_cache(self, monkeypatch):
        """提交反馈后缓存被删除 → 再次 GET 重新分析"""
        monkeypatch.setattr(settings, "RESULTS_CACHE_ENABLED", True)
        monkeypatch.setattr(settings, "RESULTS_CACHE_TTL_SECONDS", 300)

        mock_analyzer = MagicMock()
        mock_analyzer.analyze_candidates.return_value = [{
            **CANDIDATE, "analysis": "报告", "classification": "interview",
        }]

        with _mock_pipeline([CANDIDATE], mock_analyzer):
            client.get("/api/v1/results/query_001")
            first_calls = mock_analyzer.analyze_candidates.call_count
            assert first_calls == 1

            # 提交反馈（rules_manager 用隔离实例，add_feedback 真实执行）
            fb = client.post("/api/v1/feedback", json={
                "resume_id": "candidate_001", "query_id": "query_001",
                "human_classification": "reject", "human_reason": "测试",
            })
            assert fb.status_code == 200

            # 缓存已被失效 → 再次 GET 重新分析
            client.get("/api/v1/results/query_001")
            assert mock_analyzer.analyze_candidates.call_count == 2

    def test_cache_key_includes_rules_version(self, monkeypatch, isolated_rules_dir):
        """规则版本变化 → 缓存键变化，不命中旧缓存"""
        monkeypatch.setattr(settings, "RESULTS_CACHE_ENABLED", True)

        mock_analyzer = MagicMock()
        mock_analyzer.analyze_candidates.return_value = [{
            **CANDIDATE, "analysis": "报告", "classification": "interview",
        }]

        with _mock_pipeline([CANDIDATE], mock_analyzer):
            client.get("/api/v1/results/query_001")
            assert mock_analyzer.analyze_candidates.call_count == 1

            # 升级规则到 v1（直接写规则文件）
            isolated_rules_dir.rules_dir.mkdir(parents=True, exist_ok=True)
            isolated_rules_dir._save_json(isolated_rules_dir.rules_path, {
                "schema_version": 1, "version": 1, "updated_at": "2026-01-01T00:00:00",
                "active": True, "rules": ["新规则"], "summary": "s",
                "based_on_feedback_ids": [], "based_on_last_ts": None, "history": [],
            })

            client.get("/api/v1/results/query_001")
            # 版本变化 → 缓存不命中 → 重新分析（且规则注入）
            assert mock_analyzer.analyze_candidates.call_count == 2
            rules_text = mock_analyzer.analyze_candidates.call_args[0][2]
            assert "新规则" in rules_text

    def test_cache_disabled_by_default(self, monkeypatch):
        """默认关闭缓存（conftest 注入）时每次都重新分析"""
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_candidates.return_value = [{
            **CANDIDATE, "analysis": "报告", "classification": "interview",
        }]
        with _mock_pipeline([CANDIDATE], mock_analyzer):
            client.get("/api/v1/results/query_001")
            client.get("/api/v1/results/query_001")
        assert mock_analyzer.analyze_candidates.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__])
