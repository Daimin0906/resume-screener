"""
测试入库即预分类（P1）
"""
import json

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from config.config import settings

client = TestClient(app)


def _mock_analyzer_classification(cls="interview", reason="独立负责"):
    """构造 analyzer 返回结构化分类的 mock"""
    mock_analyzer = MagicMock()
    mock_analyzer.analyze_candidate.return_value = {
        "id": "resume_001",
        "analysis": "## 报告",
        "classification": cls,
        "classification_reason": reason,
        "classification_source": "llm",
        "assessment": {"ownership": 0.9},
        "strengths": [],
        "risks": [],
    }
    return mock_analyzer


class TestPreclassification:
    def test_upload_with_preclassify_enabled(self, monkeypatch, isolated_rules_dir):
        """开启预分类时：上传后详情/列表带 preclassification 字段"""
        monkeypatch.setattr(settings, "PRECLASSIFY_ON_INGEST", True)
        mock_analyzer = _mock_analyzer_classification("reject", "无真实项目证据")

        with patch('app.api.routes.document_parser') as mock_dp, \
             patch('app.api.routes.metadata_extractor') as mock_me, \
             patch('app.api.routes.retriever') as mock_ret, \
             patch('app.api.routes.candidate_analyzer', mock_analyzer):
            mock_dp.parse_pdf.return_value = "这是一份简历文本"
            mock_me.extract_metadata.return_value = MagicMock(
                dict=MagicMock(return_value={"name": "张三", "skills": ["Python"]}))
            mock_ret.add_resume.return_value = None

            resp = client.post(
                "/api/v1/resumes",
                files={"file": ("t.txt", b"resume text", "text/plain")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        rid = data["resume_id"]

        # analyzer 收到通用模式调用（query_metadata 为 None）
        call_args = mock_analyzer.analyze_candidate.call_args
        assert call_args[0][1] is None  # query_metadata=None

        # 详情带 preclassification
        detail = client.get(f"/api/v1/resumes/{rid}").json()
        pre = detail["preclassification"]
        assert pre["classification"] == "reject"
        assert pre["reason"] == "无真实项目证据"
        assert pre["rule_version"] == 0

        # 列表带 preclassification
        lst = client.get("/api/v1/resumes").json()
        item = next(i for i in lst["resumes"] if i["resume_id"] == rid)
        assert item["preclassification"]["classification"] == "reject"

    def test_upload_preclassify_disabled(self, monkeypatch):
        """关闭预分类时：无 preclassification 字段"""
        monkeypatch.setattr(settings, "PRECLASSIFY_ON_INGEST", False)
        with patch('app.api.routes.document_parser') as mock_dp, \
             patch('app.api.routes.metadata_extractor') as mock_me, \
             patch('app.api.routes.retriever') as mock_ret:
            mock_dp.parse_pdf.return_value = "文本"
            mock_me.extract_metadata.return_value = MagicMock(
                dict=MagicMock(return_value={"name": "张三"}))
            mock_ret.add_resume.return_value = None

            resp = client.post(
                "/api/v1/resumes",
                files={"file": ("t.txt", b"resume text", "text/plain")},
            )
        assert resp.status_code == 200
        detail = client.get(f"/api/v1/resumes/{resp.json()['resume_id']}").json()
        assert "preclassification" not in detail

    def test_preclassify_failure_does_not_fail_upload(self, monkeypatch):
        """预分类失败不影响上传结果"""
        monkeypatch.setattr(settings, "PRECLASSIFY_ON_INGEST", True)
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_candidate.side_effect = Exception("LLM 超时")

        with patch('app.api.routes.document_parser') as mock_dp, \
             patch('app.api.routes.metadata_extractor') as mock_me, \
             patch('app.api.routes.retriever') as mock_ret, \
             patch('app.api.routes.candidate_analyzer', mock_analyzer):
            mock_dp.parse_pdf.return_value = "文本"
            mock_me.extract_metadata.return_value = MagicMock(
                dict=MagicMock(return_value={"name": "张三"}))
            mock_ret.add_resume.return_value = None

            resp = client.post(
                "/api/v1/resumes",
                files={"file": ("t.txt", b"resume text", "text/plain")},
            )
        assert resp.status_code == 200  # 上传不受预分类失败影响
        assert "preclassification" not in client.get(
            f"/api/v1/resumes/{resp.json()['resume_id']}").json()

    def test_preclassify_pending_only_missing(self, monkeypatch, isolated_rules_dir):
        """preclassify_pending 只补缺：无预分类的才处理，已有的跳过"""
        from app.api import routes
        routes.resume_storage.clear()  # 清掉其他用例的数据，保证待补集合干净
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_candidates.return_value = [
            {"id": "r1", "classification": "review", "classification_reason": "待核实",
             "classification_source": "llm"},
            {"id": "r2", "classification": "interview", "classification_reason": "ok",
             "classification_source": "llm"},
        ]
        monkeypatch.setattr(routes, "candidate_analyzer", mock_analyzer)

        # 构造：r1 无预分类、r2 无预分类、r3 已有预分类
        routes.resume_storage["r1"] = {"id": "r1", "text": "文本1", "metadata": {}, "filename": "a.txt"}
        routes.resume_storage["r2"] = {"id": "r2", "text": "文本2", "metadata": {}, "filename": "b.txt"}
        routes.resume_storage["r3"] = {"id": "r3", "text": "文本3", "metadata": {}, "filename": "c.txt",
                                       "preclassification": {"classification": "interview"}}
        routes.resume_tasks.clear()

        result = routes.preclassify_pending()
        assert result["processed"] == 2
        # analyze_candidates 收到的批里只有 r1/r2（不含已有预分类的 r3）
        call_args = mock_analyzer.analyze_candidates.call_args
        batch = call_args[0][0]
        assert [r["id"] for r in batch] == ["r1", "r2"]
        assert routes.resume_storage["r1"]["preclassification"]["classification"] == "review"
        # r3 的已有预分类未被覆盖
        assert routes.resume_storage["r3"]["preclassification"]["classification"] == "interview"

    def test_preclassify_pending_none(self, monkeypatch):
        """无待补简历时 processed=0 且不调 LLM"""
        from app.api import routes
        mock_analyzer = MagicMock()
        monkeypatch.setattr(routes, "candidate_analyzer", mock_analyzer)
        routes.resume_storage.clear()
        assert routes.preclassify_pending() == {"processed": 0}
        mock_analyzer.analyze_candidates.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
