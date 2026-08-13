"""
测试API模块
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import json

from app.main import app

client = TestClient(app)


class TestAPI:
    """测试API接口"""

    def test_health_check(self):
        """测试健康检查接口"""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    @patch('app.api.routes.document_parser')
    @patch('app.api.routes.metadata_extractor')
    @patch('app.api.routes.retriever')
    def test_upload_resume(self, mock_retriever, mock_metadata_extractor, mock_document_parser):
        """测试上传简历接口"""
        # Mock各个组件
        mock_document_parser.parse_pdf.return_value = "这是一份简历文本"
        mock_metadata_extractor.extract_metadata.return_value = MagicMock(
            dict=MagicMock(return_value={
                "name": "张三",
                "email": "zhangsan@example.com"
            })
        )
        mock_retriever.add_resume.return_value = None

        # 创建测试文件
        test_content = b"This is a test resume file"
        
        # 发送上传请求
        response = client.post(
            "/api/v1/resumes",
            files={"file": ("test_resume.pdf", test_content, "application/pdf")}
        )
        
        # 验证响应
        assert response.status_code == 200
        data = response.json()
        assert "resume_id" in data
        assert "message" in data

    @patch('app.api.routes.query_parser')
    def test_submit_query(self, mock_query_parser):
        """测试提交筛选查询接口"""
        # Mock查询解析器
        mock_query_parser.parse_query.return_value = MagicMock(
            dict=MagicMock(return_value={
                "keywords": ["Python"],
                "required_skills": ["Python"]
            })
        )

        # 发送查询请求
        query_data = {"query_text": "寻找Python开发者"}
        response = client.post("/api/v1/queries", json=query_data)
        
        # 验证响应
        assert response.status_code == 200
        data = response.json()
        assert "query_id" in data
        assert "message" in data

    @patch('app.api.routes.query_storage')
    @patch('app.api.routes.retriever')
    @patch('app.api.routes.hard_filter')
    @patch('app.api.routes.scorer')
    @patch('app.api.routes.ranker')
    @patch('app.api.routes.candidate_analyzer')
    @patch('app.api.routes.result_formatter')
    def test_get_screening_results(self, mock_result_formatter, mock_candidate_analyzer, 
                                   mock_ranker, mock_scorer, mock_hard_filter, 
                                   mock_retriever, mock_query_storage):
        """测试获取筛选结果接口"""
        # Mock各个组件
        mock_query_storage.__contains__.return_value = True
        mock_query_storage.__getitem__.return_value = {
            "id": "test_query_id",
            "text": "寻找Python开发者",
            "metadata": {
                "keywords": ["Python"],
                "required_skills": ["Python"]
            },
            "created_at": "2025-01-01T00:00:00"
        }
        
        mock_retriever.retrieve.return_value = [
            {
                "id": "candidate_001",
                "text": "这是一份简历文本",
                "metadata": {
                    "name": "张三",
                    "email": "zhangsan@example.com",
                    "skills": ["Python", "Django"],
                    "work_experience": [],
                    "education": []
                }
            }
        ]
        
        mock_hard_filter.filter_resumes.return_value = [
            {
                "id": "candidate_001",
                "text": "这是一份简历文本",
                "metadata": {
                    "name": "张三",
                    "email": "zhangsan@example.com",
                    "skills": ["Python", "Django"],
                    "work_experience": [],
                    "education": []
                }
            }
        ]
        
        mock_scorer.score_resumes.return_value = [
            {
                "id": "candidate_001",
                "text": "这是一份简历文本",
                "metadata": {
                    "name": "张三",
                    "email": "zhangsan@example.com",
                    "skills": ["Python", "Django"],
                    "work_experience": [],
                    "education": []
                },
                "scores": {
                    "overall_score": 0.95,
                    "skill_score": 0.9
                }
            }
        ]
        
        mock_ranker.rank_resumes.return_value = [
            {
                "id": "candidate_001",
                "rank": 1,
                "text": "这是一份简历文本",
                "metadata": {
                    "name": "张三",
                    "email": "zhangsan@example.com",
                    "skills": ["Python", "Django"],
                    "work_experience": [],
                    "education": []
                },
                "scores": {
                    "overall_score": 0.95,
                    "skill_score": 0.9
                }
            }
        ]
        
        mock_candidate_analyzer.analyze_candidates.return_value = [
            {
                "id": "candidate_001",
                "rank": 1,
                "text": "这是一份简历文本",
                "metadata": {
                    "name": "张三",
                    "email": "zhangsan@example.com",
                    "skills": ["Python", "Django"],
                    "work_experience": [],
                    "education": []
                },
                "scores": {
                    "overall_score": 0.95,
                    "skill_score": 0.9
                },
                "analysis": "这是一份详细的候选人评价报告..."
            }
        ]
        
        mock_result_formatter.format_results.return_value = {
            "total_candidates": 1,
            "candidates": [
                {
                    "id": "candidate_001",
                    "rank": 1,
                    "name": "张三",
                    "contact_info": {
                        "email": "zhangsan@example.com",
                        "phone": "13800138000"
                    },
                    "scores": {
                        "overall_score": 0.95,
                        "skill_score": 0.9
                    },
                    "basic_info": {
                        "skills": ["Python", "Django"],
                        "expected_salary": "20K-30K",
                        "preferred_locations": ["北京"]
                    },
                    "analysis": "这是一份详细的候选人评价报告...",
                    "metadata": {
                        "work_experience": [],
                        "education": []
                    }
                }
            ],
            "summary": {
                "average_score": 0.95
            }
        }

        # 发送获取结果请求
        response = client.get("/api/v1/results/test_query_id")
        
        # 验证响应
        assert response.status_code == 200
        data = response.json()
        assert data["query_id"] == "test_query_id"
        assert data["total_candidates"] == 1
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["name"] == "张三"

    def test_get_resume_not_found(self):
        """测试获取不存在的简历"""
        response = client.get("/api/v1/resumes/nonexistent_id")
        assert response.status_code == 404

    def test_delete_resume_not_found(self):
        """删除不存在的简历 → 404"""
        response = client.delete("/api/v1/resumes/nonexistent_id")
        assert response.status_code == 404

    @patch('app.api.routes.vector_store_manager')
    def test_delete_resume(self, mock_vsm):
        """删除简历：内存索引移除 + 向量库同步删除"""
        import uuid
        from app.api import routes
        rid = str(uuid.uuid4())
        routes.resume_storage[rid] = {
            "id": rid, "filename": "test.txt", "text": "x",
            "metadata": {}, "created_at": None,
        }
        try:
            response = client.delete(f"/api/v1/resumes/{rid}")
            assert response.status_code == 200
            assert rid not in routes.resume_storage
            mock_vsm.delete_documents.assert_called_once_with("resumes", [rid])
        finally:
            routes.resume_storage.pop(rid, None)

    def test_get_screening_results_not_found(self):
        """测试获取不存在的筛选结果"""
        with patch('app.api.routes.query_storage') as mock_query_storage:
            mock_query_storage.__contains__.return_value = False
            
            response = client.get("/api/v1/results/nonexistent_id")
            assert response.status_code == 404


class TestFeedbackAndRulesAPI:
    """测试人工反馈与筛选规则接口"""

    FEEDBACK_BODY = {
        "resume_id": "candidate_001",
        "query_id": "query_001",
        "candidate_name": "张三",
        "ai_classification": "interview",
        "human_classification": "reject",
        "human_reason": "只是关键词堆砌",
        "overall_score": 0.95,
    }

    def test_submit_feedback_valid(self):
        """提交合法反馈 → 200 + feedback_id"""
        response = client.post("/api/v1/feedback", json=self.FEEDBACK_BODY)
        assert response.status_code == 200
        data = response.json()
        assert "feedback_id" in data
        assert data["message"] == "反馈提交成功"

    def test_submit_feedback_invalid_classification(self):
        """非法分类 → 400"""
        response = client.post("/api/v1/feedback", json={**self.FEEDBACK_BODY, "human_classification": "unknown"})
        assert response.status_code == 400

    def test_get_rules_initial_state(self):
        """初始状态：version 0、无规则、无待总结"""
        response = client.get("/api/v1/rules")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 0
        assert data["rules"] == []
        assert data["pending_feedback_count"] == 0

    def test_summarize_rules_insufficient(self):
        """反馈不足时总结 → 400"""
        response = client.post("/api/v1/rules/summarize", json={})
        assert response.status_code == 400

    @patch('app.api.routes.rules_manager')
    def test_summarize_rules_success(self, mock_rules_manager):
        """反馈足量 + 总结成功 → 返回新版本"""
        mock_rules_manager.summarize_rules.return_value = {
            "version": 2,
            "rules": ["只看独立负责的真实项目"],
            "summary": "规律总结",
            "based_on_feedback_ids": ["fb1", "fb2", "fb3"],
        }
        response = client.post("/api/v1/rules/summarize", json={"min_feedback": 3})
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == 2
        assert len(data["rules"]) == 1
        assert data["based_on_feedback_count"] == 3

    @patch('app.api.routes.query_storage')
    @patch('app.api.routes.retriever')
    @patch('app.api.routes.hard_filter')
    @patch('app.api.routes.scorer')
    @patch('app.api.routes.ranker')
    @patch('app.api.routes.candidate_analyzer')
    @patch('app.api.routes.result_formatter')
    def test_feedback_overrides_result_display(self, mock_result_formatter, mock_candidate_analyzer,
                                               mock_ranker, mock_scorer, mock_hard_filter,
                                               mock_retriever, mock_query_storage):
        """提交人工纠正后，重新拉取结果时该候选人分类被覆盖并标记"""
        # 提交反馈
        fb_resp = client.post("/api/v1/feedback", json=self.FEEDBACK_BODY)
        assert fb_resp.status_code == 200

        # Mock 筛选管线
        mock_query_storage.__contains__.return_value = True
        mock_query_storage.__getitem__.return_value = {
            "id": "query_001",
            "text": "寻找Python开发者",
            "metadata": {"keywords": ["Python"], "required_skills": ["Python"]},
            "created_at": "2025-01-01T00:00:00",
        }
        base_candidate = {
            "id": "candidate_001",
            "rank": 1,
            "name": "张三",
            "metadata": {"name": "张三"},
            "scores": {"overall_score": 0.95, "skill_score": 0.9},
        }
        mock_retriever.retrieve.return_value = [base_candidate]
        mock_hard_filter.filter_resumes.return_value = [base_candidate]
        mock_scorer.score_resumes.return_value = [base_candidate]
        mock_ranker.rank_resumes.return_value = [base_candidate]
        mock_candidate_analyzer.analyze_candidates.return_value = [
            {**base_candidate, "analysis": "报告", "classification": "interview"}
        ]
        mock_result_formatter.format_results.return_value = {
            "total_candidates": 1,
            "candidates": [
                {
                    "id": "candidate_001",
                    "rank": 1,
                    "name": "张三",
                    "contact_info": {},
                    "scores": {"overall_score": 0.95, "skill_score": 0.9},
                    "basic_info": {"skills": ["Python"], "work_experience": [], "education": []},
                    "analysis": "报告",
                    "classification": "interview",
                }
            ],
            "summary": {"average_score": 0.95},
        }

        response = client.get("/api/v1/results/query_001")
        assert response.status_code == 200
        data = response.json()
        candidate = data["candidates"][0]
        assert candidate["classification"] == "reject"          # 人工分类覆盖
        assert candidate["classification_source"] == "human"
        assert candidate["corrected_by_human"] is True
        assert data["rules_version_used"] == 0

    def test_feedback_list(self):
        """反馈日志查询"""
        client.post("/api/v1/feedback", json=self.FEEDBACK_BODY)
        response = client.get("/api/v1/feedback?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert data["entries"][0]["human_classification"] == "reject"


if __name__ == "__main__":
    pytest.main([__file__])