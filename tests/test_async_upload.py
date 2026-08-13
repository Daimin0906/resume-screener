"""
测试异步上传（P2）：立即返回 parsing → 轮询到 ready/error
"""
import time

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from config.config import settings

client = TestClient(app)


def wait_until_status(rid, target="ready", timeout=5):
    """轮询状态直到目标值或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/v1/resumes/{rid}/status")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] == target:
            return data
        time.sleep(0.05)
    return client.get(f"/api/v1/resumes/{rid}/status").json()


class TestAsyncUpload:
    def test_async_upload_parsing_then_ready(self, monkeypatch):
        """异步模式：立即返回 parsing → 轮询到 ready"""
        monkeypatch.setattr(settings, "UPLOAD_ASYNC", True)

        # 用真实 _process_resume_sync，但 mock 慢速组件模拟后台处理。
        # 注意：轮询必须在 patch 上下文内完成——异步线程在 post 返回后仍在执行，
        # 若 with 块先退出，后台线程会用到真实组件（真实 LLM 调用）。
        with patch('app.api.routes.metadata_extractor') as mock_me, \
             patch('app.api.routes.retriever') as mock_ret, \
             patch('app.api.routes.candidate_analyzer') as mock_analyzer:
            def slow_extract(text):
                time.sleep(0.3)
                return MagicMock(dict=MagicMock(return_value={"name": "张三"}))

            mock_me.extract_metadata.side_effect = slow_extract
            mock_ret.add_resume.return_value = None
            mock_analyzer.analyze_candidate.return_value = {
                "classification": "interview", "classification_reason": "ok",
                "classification_source": "llm", "assessment": {}, "strengths": [], "risks": [],
            }

            resp = client.post(
                "/api/v1/resumes",
                files={"file": ("a.txt", b"resume text", "text/plain")},
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "parsing"  # 立即返回，不等待解析

            # 轮询到 ready（在 patch 上下文内完成）
            status = wait_until_status(data["resume_id"], "ready")
            assert status["status"] == "ready"
            assert status["error"] is None

            # 列表带 status
            lst = client.get("/api/v1/resumes").json()
            item = next(i for i in lst["resumes"] if i["resume_id"] == data["resume_id"])
            assert item["status"] == "ready"

    def test_async_upload_error_status(self, monkeypatch):
        """异步模式解析失败 → status=error 且带错误信息"""
        monkeypatch.setattr(settings, "UPLOAD_ASYNC", True)

        with patch('app.api.routes.metadata_extractor') as mock_me, \
             patch('app.api.routes.retriever') as mock_ret:
            mock_me.extract_metadata.side_effect = RuntimeError("解析引擎崩溃")
            mock_ret.add_resume.return_value = None

            resp = client.post(
                "/api/v1/resumes",
                files={"file": ("a.txt", b"resume text", "text/plain")},
            )

            assert resp.status_code == 200
            # 轮询必须在 patch 上下文内（异步线程仍在执行）
            status = wait_until_status(resp.json()["resume_id"], "error")
            assert "解析引擎崩溃" in (status["error"] or "")

    def test_sync_upload_status_ready(self, monkeypatch):
        """同步模式（测试默认）：返回时已 ready"""
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
        assert resp.json()["status"] == "ready"

    def test_status_not_found(self):
        """不存在的简历状态 → 404"""
        assert client.get("/api/v1/resumes/nonexistent/status").status_code == 404

    def test_reset_task_statuses_after_restart(self, monkeypatch):
        """重启后存量简历状态全部置 ready"""
        from app.api import routes
        monkeypatch.setattr(settings, "UPLOAD_ASYNC", True)
        with patch('app.api.routes.metadata_extractor') as mock_me, \
             patch('app.api.routes.retriever') as mock_ret, \
             patch('app.api.routes.candidate_analyzer') as mock_analyzer:
            mock_me.extract_metadata.return_value = MagicMock(
                dict=MagicMock(return_value={"name": "x"}))
            mock_ret.add_resume.return_value = None
            mock_analyzer.analyze_candidate.return_value = {
                "classification": "interview", "classification_reason": "ok",
                "classification_source": "llm", "assessment": {}, "strengths": [], "risks": [],
            }
            resp = client.post(
                "/api/v1/resumes",
                files={"file": ("a.txt", b"text", "text/plain")},
            )
            rid = resp.json()["resume_id"]
            wait_until_status(rid, "ready")  # 在 patch 上下文内等线程完成

            # 模拟重启：清空任务状态后调用恢复
            routes.resume_tasks.clear()
            routes.reset_task_statuses_after_restart()
            assert routes.resume_tasks[rid]["status"] == "ready"


if __name__ == "__main__":
    pytest.main([__file__])
