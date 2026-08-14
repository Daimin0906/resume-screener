"""
测试候选人处理工作台（Workbench）
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from app.core.workbench import Workbench, STATUS_PENDING, STATUS_INTERVIEW, STATUS_REVIEW, STATUS_ARCHIVED
from app.main import app

client = TestClient(app)


def make_workbench(tmp_path):
    return Workbench(str(tmp_path / "wb_data"))


SAMPLE_RUNS = [
    {
        "finished_at": "2026-08-14T10:00:00",
        "rules_version_used": 2,
        "candidates": [
            {"id": "r1", "name": "张三", "classification": "interview",
             "classification_reason": "独立负责", "overall_score": 0.9,
             "skills": ["Python"], "analysis": "报告1"},
            {"id": "r2", "name": "李四", "classification": "review",
             "classification_reason": "待核实", "overall_score": 0.7,
             "skills": ["Java"], "analysis": "报告2"},
        ],
    },
    {
        "finished_at": "2026-08-14T11:00:00",
        "rules_version_used": 2,
        "candidates": [
            # r1 再次出现（更新分类）→ 去重保留最新
            {"id": "r1", "name": "张三", "classification": "reject",
             "classification_reason": "更新判定", "overall_score": 0.5,
             "skills": ["Python"], "analysis": "报告1v2"},
            {"id": "r3", "name": "王五", "classification": "interview",
             "classification_reason": "优秀", "overall_score": 0.95,
             "skills": ["Go"], "analysis": "报告3"},
        ],
    },
]


class TestWorkbenchStatus:
    def test_set_and_get(self, tmp_path):
        wb = make_workbench(tmp_path)
        assert wb.get_status("r1") == STATUS_PENDING  # 默认待处理
        wb.set_status("r1", STATUS_INTERVIEW)
        assert wb.get_status("r1") == STATUS_INTERVIEW
        assert wb.status_map() == {"r1": STATUS_INTERVIEW}

    def test_invalid_status_rejected(self, tmp_path):
        wb = make_workbench(tmp_path)
        with pytest.raises(ValueError):
            wb.set_status("r1", "unknown")

    def test_persists(self, tmp_path):
        wb1 = make_workbench(tmp_path)
        wb1.set_status("r1", STATUS_ARCHIVED)
        wb2 = make_workbench(tmp_path)
        assert wb2.get_status("r1") == STATUS_ARCHIVED


class TestWorkbenchAggregate:
    def test_aggregate_dedup_keeps_latest(self, tmp_path):
        wb = make_workbench(tmp_path)
        candidates = wb.aggregate(SAMPLE_RUNS)
        ids = [c["resume_id"] for c in candidates]
        assert ids == ["r3", "r2", "r1"]  # 排序：interview 优先 + 分数降序
        r1 = next(c for c in candidates if c["resume_id"] == "r1")
        assert r1["classification"] == "reject"  # 保留最新判定
        assert r1["screened_at"] == "2026-08-14T11:00:00"

    def test_aggregate_work_status(self, tmp_path):
        wb = make_workbench(tmp_path)
        wb.set_status("r2", STATUS_REVIEW)
        candidates = wb.aggregate(SAMPLE_RUNS)
        r2 = next(c for c in candidates if c["resume_id"] == "r2")
        assert r2["work_status"] == STATUS_REVIEW
        r3 = next(c for c in candidates if c["resume_id"] == "r3")
        assert r3["work_status"] == STATUS_PENDING

    def test_export_interview_csv(self, tmp_path):
        wb = make_workbench(tmp_path)
        candidates = wb.aggregate(SAMPLE_RUNS)
        csv_text = wb.export_interview_csv(candidates)
        assert "王五" in csv_text  # interview 分类
        assert "张三" not in csv_text  # 已改为 reject，不导出

    def test_export_with_manual_interview(self, tmp_path):
        wb = make_workbench(tmp_path)
        wb.set_status("r2", STATUS_INTERVIEW)  # 人工标约面试
        candidates = wb.aggregate(SAMPLE_RUNS)
        csv_text = wb.export_interview_csv(candidates)
        assert "李四" in csv_text  # 人工标记的也导出


class TestWorkbenchAPI:
    def test_workbench_empty(self, isolated_auto_screen_dir):
        resp = client.get("/api/v1/workbench/candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["pending_count"] == 0

    def test_workbench_with_runs(self, isolated_auto_screen_dir, monkeypatch):
        from app.api import routes
        # 注入 mock run 数据
        monkeypatch.setattr(routes.auto_screener, "list_runs",
                            lambda limit=50: SAMPLE_RUNS)
        resp = client.get("/api/v1/workbench/candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["pending_count"] == 3  # 未设置处理状态

    def test_update_status(self, isolated_auto_screen_dir):
        resp = client.post("/api/v1/workbench/candidates/r1/status",
                           json={"status": "interview"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "interview"
        # 非法状态
        resp2 = client.post("/api/v1/workbench/candidates/r1/status",
                            json={"status": "bad"})
        assert resp2.status_code == 400

    def test_export_endpoint(self, isolated_auto_screen_dir, monkeypatch):
        from app.api import routes
        monkeypatch.setattr(routes.auto_screener, "list_runs",
                            lambda limit=50: SAMPLE_RUNS)
        resp = client.get("/api/v1/workbench/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        assert "王五" in resp.text

    def test_export_empty_404(self, isolated_auto_screen_dir):
        resp = client.get("/api/v1/workbench/export")
        assert resp.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__])
