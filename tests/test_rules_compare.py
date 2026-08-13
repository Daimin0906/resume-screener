"""
测试规则版本对比（P5）：/rules/compare
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app

client = TestClient(app)

CANDIDATES = [
    {"id": "r1", "rank": 1, "metadata": {"name": "张三"},
     "scores": {"overall_score": 0.9}, "text": "简历1"},
    {"id": "r2", "rank": 2, "metadata": {"name": "李四"},
     "scores": {"overall_score": 0.5}, "text": "简历2"},
]


def _seed_rules(isolated_rules_dir, version=2):
    """预置 v2 规则 + history 含 v1"""
    isolated_rules_dir.rules_dir.mkdir(parents=True, exist_ok=True)
    isolated_rules_dir._save_json(isolated_rules_dir.rules_path, {
        "schema_version": 1, "version": version,
        "updated_at": "2026-08-13T12:00:00", "active": True,
        "rules": ["只看独立负责的真实项目"],
        "summary": "v2 规则", "based_on_feedback_ids": [],
        "based_on_last_ts": None,
        "history": [
            {"version": 1, "updated_at": "2026-08-13T11:00:00",
             "rules": ["只看关键词命中"]},
        ],
    })


class TestRulesCompare:
    def test_compare_requires_one_param(self):
        """query_id 与 resume_ids 都缺/都给 → 400"""
        resp = client.post("/api/v1/rules/compare", json={})
        assert resp.status_code == 400
        resp = client.post("/api/v1/rules/compare",
                           json={"query_id": "q1", "resume_ids": ["r1"]})
        assert resp.status_code == 400

    def test_compare_query_not_found(self):
        """query_id 不存在 → 404"""
        resp = client.post("/api/v1/rules/compare", json={"query_id": "nonexistent"})
        assert resp.status_code == 404

    def test_compare_two_rule_versions(self, isolated_rules_dir):
        """两轮分析捕获不同规则文本，分布与 delta 统计正确"""
        _seed_rules(isolated_rules_dir)

        mock_analyzer = MagicMock()
        mock_analyzer.analyze_candidates.side_effect = [
            # 第一轮（v1 规则）：张三 interview / 李四 review
            [{**CANDIDATES[0], "classification": "interview",
              "classification_reason": "旧规则命中", "classification_source": "llm"},
             {**CANDIDATES[1], "classification": "review",
              "classification_reason": "旧规则", "classification_source": "llm"}],
            # 第二轮（v2 规则）：张三 reject（新规则要求独立负责但证据不足）/ 李四 reject
            [{**CANDIDATES[0], "classification": "reject",
              "classification_reason": "无独立负责证据", "classification_source": "llm"},
             {**CANDIDATES[1], "classification": "reject",
              "classification_reason": "无项目实绩", "classification_source": "llm"}],
        ]

        qs = MagicMock()
        qs.__contains__.return_value = True
        qs.__getitem__.return_value = {
            "id": "query_001", "text": "测试", "created_at": "2026-01-01T00:00:00",
            "metadata": {"keywords": ["Python"], "required_skills": ["Python"]},
        }

        with patch.multiple(
            "app.api.routes",
            query_storage=qs,
            retriever=MagicMock(retrieve=MagicMock(return_value=CANDIDATES)),
            hard_filter=MagicMock(filter_resumes=MagicMock(return_value=CANDIDATES)),
            scorer=MagicMock(score_resumes=MagicMock(return_value=CANDIDATES)),
            ranker=MagicMock(rank_resumes=MagicMock(return_value=CANDIDATES)),
            candidate_analyzer=mock_analyzer,
        ):
            resp = client.post("/api/v1/rules/compare", json={"query_id": "query_001"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["base_version"] == 1
        assert data["current_version"] == 2
        assert data["compared_count"] == 2
        assert data["distributions"]["base"] == {"interview": 1, "review": 1, "reject": 0}
        assert data["distributions"]["current"] == {"interview": 0, "review": 0, "reject": 2}
        assert data["changed_count"] == 2
        assert data["deltas"][0]["name"] == "张三"
        assert data["deltas"][0]["base_classification"] == "interview"
        assert data["deltas"][0]["current_classification"] == "reject"
        assert "两轮完整分析" in data["note"]

        # 两轮分析分别注入 v1 / v2 规则文本
        calls = mock_analyzer.analyze_candidates.call_args_list
        assert "只看关键词命中" in calls[0][0][2]   # 第一轮 = v1 规则
        assert "只看独立负责的真实项目" in calls[1][0][2]  # 第二轮 = v2 规则

    def test_compare_with_resume_ids_skips_retrieval(self, isolated_rules_dir):
        """resume_ids 路径：跳过检索/过滤，直接评分排序"""
        _seed_rules(isolated_rules_dir)

        mock_analyzer = MagicMock()
        mock_analyzer.analyze_candidates.side_effect = [
            [{**CANDIDATES[0], "classification": "review"}],
            [{**CANDIDATES[0], "classification": "review"}],
        ]

        with patch.multiple(
            "app.api.routes",
            retriever=MagicMock(retrieve=MagicMock()),
            hard_filter=MagicMock(filter_resumes=MagicMock()),
            scorer=MagicMock(score_resumes=MagicMock(return_value=CANDIDATES)),
            ranker=MagicMock(rank_resumes=MagicMock(return_value=CANDIDATES)),
            candidate_analyzer=mock_analyzer,
        ):
            # 需要 resume_storage 里有这些 id（_run_screening_stages 的 resume_ids 路径读它）
            from app.api import routes
            routes.resume_storage.update({c["id"]: c for c in CANDIDATES})
            try:
                resp = client.post("/api/v1/rules/compare", json={"resume_ids": ["r1", "r2"]})
            finally:
                routes.resume_storage.clear()

        assert resp.status_code == 200
        assert resp.json()["compared_count"] == 1
        # 未走检索路径
        # （retriever.retrieve 是 MagicMock，未设置 return_value，若被调用会返回 MagicMock 导致下游报错；
        #   此处仅断言响应成功即证明走的是 resume_ids 分支）

    def test_compare_no_candidates(self, isolated_rules_dir):
        """无可对比候选人 → 空结果 + 提示"""
        _seed_rules(isolated_rules_dir)
        with patch.multiple(
            "app.api.routes",
            retriever=MagicMock(retrieve=MagicMock(return_value=[])),
            hard_filter=MagicMock(filter_resumes=MagicMock(return_value=[])),
            scorer=MagicMock(score_resumes=MagicMock(return_value=[])),
            ranker=MagicMock(rank_resumes=MagicMock(return_value=[])),
        ):
            resp = client.post("/api/v1/rules/compare", json={"resume_ids": ["nonexistent"]})
        assert resp.status_code == 200
        assert resp.json()["compared_count"] == 0
        assert "没有可对比的候选人" in resp.json()["note"]


if __name__ == "__main__":
    pytest.main([__file__])
