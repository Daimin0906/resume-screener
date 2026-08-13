"""
三分类 + 反馈自迭代的端到端流程测试

覆盖：分类字段全链路透传、规则注入 analyzer、旧格式兼容（启发式兜底）。
"""
import json

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app

client = TestClient(app)


def _base_candidate(cid="candidate_001", score=0.9, cls=None):
    c = {
        "id": cid,
        "rank": 1,
        "name": "张三",
        "metadata": {"name": "张三", "skills": ["Python"], "work_experience": [], "education": []},
        "scores": {"overall_score": score, "skill_score": 0.9},
    }
    if cls is not None:
        c["classification"] = cls
    return c


def _mock_pipeline(analyzed_candidates, format_candidates=None):
    """Mock 查询解析与筛选管线，返回 (patch 上下文, 组件 mock 字典)。

    注意：patch.multiple 的返回值只包含用 DEFAULT 创建的 mock，
    自定义对象需通过本函数返回的 mock 字典引用。
    """
    query_metadata = {"keywords": ["Python"], "required_skills": ["Python"]}
    formatted = {
        "total_candidates": len(analyzed_candidates),
        "candidates": format_candidates if format_candidates is not None else [
            {
                "id": c["id"],
                "rank": c.get("rank", 1),
                "name": c.get("name", "未知"),
                "contact_info": {"email": None, "phone": None},
                "scores": c.get("scores", {}),
                "basic_info": {"skills": ["Python"], "work_experience": [], "education": []},
                "analysis": c.get("analysis", ""),
                "classification": c.get("classification", "review"),
                "classification_reason": c.get("classification_reason", ""),
                "classification_source": c.get("classification_source", "llm"),
                "assessment": c.get("assessment", {}),
                "corrected_by_human": c.get("corrected_by_human", False),
                "strengths": c.get("strengths", []),
                "risks": c.get("risks", []),
            }
            for c in analyzed_candidates
        ],
        "summary": {"average_score": 0.9},
    }
    mocks = {
        "retriever": MagicMock(retrieve=MagicMock(return_value=analyzed_candidates)),
        "hard_filter": MagicMock(filter_resumes=MagicMock(return_value=analyzed_candidates)),
        "scorer": MagicMock(score_resumes=MagicMock(return_value=analyzed_candidates)),
        "ranker": MagicMock(rank_resumes=MagicMock(return_value=analyzed_candidates)),
        "candidate_analyzer": MagicMock(
            analyze_candidates=MagicMock(return_value=analyzed_candidates)),
        "result_formatter": MagicMock(format_results=MagicMock(return_value=formatted)),
    }
    query_storage_mock = MagicMock()
    query_storage_mock.__contains__.return_value = True
    query_storage_mock.__getitem__.return_value = {
        "id": "query_001",
        "text": "Python开发",
        "metadata": query_metadata,
        "created_at": "2025-01-01T00:00:00",
    }
    context = patch.multiple(
        "app.api.routes",
        query_storage=query_storage_mock,
        retriever=mocks["retriever"],
        hard_filter=mocks["hard_filter"],
        scorer=mocks["scorer"],
        ranker=mocks["ranker"],
        candidate_analyzer=mocks["candidate_analyzer"],
        result_formatter=mocks["result_formatter"],
    )
    return context, mocks


class TestClassificationFlow:
    def test_full_classification_fields(self):
        """结构化分类全字段透传到 API 响应"""
        analyzed = [
            {
                **_base_candidate(),
                "analysis": "## 报告",
                "classification": "interview",
                "classification_reason": "独立负责且结果可量化",
                "classification_source": "llm",
                "assessment": {"skill_match": 0.9, "ownership": 0.9,
                               "real_users": 0.8, "quantified_results": 0.8},
                "strengths": ["独立负责"],
                "risks": ["行业有限"],
            }
        ]
        ctx, _ = _mock_pipeline(analyzed)
        with ctx:
            response = client.get("/api/v1/results/query_001")

        assert response.status_code == 200
        data = response.json()
        assert data["rules_version_used"] == 0
        c = data["candidates"][0]
        assert c["classification"] == "interview"
        assert c["classification_source"] == "llm"
        assert c["corrected_by_human"] is False
        assert c["assessment"]["ownership"] == 0.9
        assert c["strengths"] == ["独立负责"]
        assert c["risks"] == ["行业有限"]

    def test_rules_text_injected_into_analyzer(self, isolated_rules_dir):
        """预置筛选规则后，analyzer 收到规则文本，结果携带规则版本"""
        # 预置规则（直接写入规则文件）
        isolated_rules_dir.rules_dir.mkdir(parents=True, exist_ok=True)
        isolated_rules_dir._save_json(isolated_rules_dir.rules_path, {
            "schema_version": 1,
            "version": 3,
            "updated_at": "2026-08-13T10:00:00",
            "active": True,
            "rules": ["只看独立负责的真实项目"],
            "summary": "测试规则",
            "based_on_feedback_ids": [],
            "based_on_last_ts": None,
            "history": [],
        })

        analyzed = [_base_candidate()]
        ctx, mocks = _mock_pipeline(analyzed)
        with ctx:
            response = client.get("/api/v1/results/query_001")

        assert response.status_code == 200
        data = response.json()
        assert data["rules_version_used"] == 3

        # 断言 analyzer 收到 rules_text（第三个位置参数）
        call_args = mocks["candidate_analyzer"].analyze_candidates.call_args
        args, kwargs = call_args
        rules_text = args[2] if len(args) > 2 else kwargs.get("rules_text", "")
        assert "只看独立负责的真实项目" in rules_text

    def test_legacy_free_text_fallback(self):
        """旧格式（无分类字段）→ 响应带兜底分类，不报错"""
        analyzed = [_base_candidate(cls=None)]  # 无 classification 字段
        ctx, _ = _mock_pipeline(analyzed)
        with ctx:
            response = client.get("/api/v1/results/query_001")

        assert response.status_code == 200
        data = response.json()
        c = data["candidates"][0]
        assert c["classification"] == "review"   # 模型默认兜底
        assert c["classification_source"] == "llm"


if __name__ == "__main__":
    pytest.main([__file__])
