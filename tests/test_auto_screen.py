"""
测试全流程自动筛选 Agent
"""
import json

import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from app.core.auto_screener import AutoScreener, STATUS_COMPLETED, STATUS_SKIPPED_NO_NEW, STATUS_SKIPPED_NO_QUERY, STATUS_FAILED
from app.core.query_parser import QueryParser
from app.main import app

client = TestClient(app)


def make_screener(tmp_path, query_parser=None, run_cb=None, **kwargs):
    """构造 AutoScreener（默认 mock 管线）。"""
    qp = query_parser or MagicMock(spec=QueryParser)
    if run_cb is None:
        run_cb = lambda qm, ids: {"total_candidates": len(ids), "candidates": [
            {"id": rid, "classification": "review", "name": "x"} for rid in ids
        ]}
    return AutoScreener(
        str(tmp_path / "auto_data"), qp, run_screening_cb=run_cb,
        rules_version_cb=lambda: 1, **kwargs)


class TestDefaultQuery:
    def test_empty_initial(self, tmp_path):
        s = make_screener(tmp_path)
        assert s.get_default_query() == {"query_text": "", "updated_at": None}

    def test_set_and_get(self, tmp_path):
        s = make_screener(tmp_path)
        result = s.set_default_query("招聘 Python 后端工程师")
        assert result["query_text"] == "招聘 Python 后端工程师"
        assert s.get_default_query()["query_text"] == "招聘 Python 后端工程师"
        assert s.query_path.exists()

    def test_set_empty_rejected(self, tmp_path):
        s = make_screener(tmp_path)
        with pytest.raises(ValueError):
            s.set_default_query("   ")

    def test_persists_across_instances(self, tmp_path):
        s1 = make_screener(tmp_path)
        s1.set_default_query("默认岗位文本")
        s2 = make_screener(tmp_path)
        assert s2.get_default_query()["query_text"] == "默认岗位文本"


class TestState:
    def test_processed_mark_and_check(self, tmp_path):
        s = make_screener(tmp_path)
        assert not s.is_processed("r1")
        s.mark_processed(["r1", "r2"])
        assert s.is_processed("r1")
        assert s.is_processed("r2")

    def test_prune_removes_deleted(self, tmp_path):
        s = make_screener(tmp_path)
        s.mark_processed(["r1", "r2", "r3"])
        s.prune_processed_ids({"r2", "r3"})
        assert not s.is_processed("r1")
        assert s.is_processed("r2")

    def test_state_persists(self, tmp_path):
        s1 = make_screener(tmp_path)
        s1.mark_processed(["r1"])
        s2 = make_screener(tmp_path)
        assert s2.is_processed("r1")


class TestRun:
    def _ready_ids(self, *rids):
        return lambda: list(rids)

    def test_run_screens_new_resumes(self, tmp_path):
        qp = MagicMock()
        s = make_screener(tmp_path, query_parser=qp)
        s.set_default_query("Python 后端")

        record = s.run("manual", self._ready_ids("r1", "r2"))

        assert record["status"] == STATUS_COMPLETED
        assert record["screened_count"] == 2
        assert record["trigger"] == "manual"
        qp.parse_query.assert_called_once()
        # processed 标记
        assert s.is_processed("r1") and s.is_processed("r2")
        # 结果持久化
        assert s.latest_run()["run_id"] == record["run_id"]

    def test_run_skips_no_new(self, tmp_path):
        s = make_screener(tmp_path)
        s.set_default_query("JD")
        s.run("manual", self._ready_ids("r1"))
        record2 = s.run("manual", self._ready_ids("r1"))  # 同一批再跑
        assert record2["status"] == STATUS_SKIPPED_NO_NEW

    def test_run_screens_only_new(self, tmp_path):
        s = make_screener(tmp_path)
        s.set_default_query("JD")
        s.run("manual", self._ready_ids("r1"))
        record2 = s.run("manual", self._ready_ids("r1", "r2"))
        assert record2["status"] == STATUS_COMPLETED
        assert record2["screened_count"] == 1  # 只筛新增的 r2

    def test_run_no_query_skips(self, tmp_path):
        s = make_screener(tmp_path)
        record = s.run("manual", self._ready_ids("r1"))
        assert record["status"] == STATUS_SKIPPED_NO_QUERY
        assert record["screened_count"] == 0

    def test_run_batch_cap(self, tmp_path):
        s = make_screener(tmp_path, max_batch=2)
        s.set_default_query("JD")
        record = s.run("manual", self._ready_ids("r1", "r2", "r3"))
        assert record["screened_count"] == 2
        assert not s.is_processed("r3")  # 第 3 份留待下轮

    def test_run_failure_records_failed(self, tmp_path):
        def boom(qm, ids):
            raise RuntimeError("筛选管线崩溃")

        s = make_screener(tmp_path, run_cb=boom)
        s.set_default_query("JD")
        record = s.run("manual", self._ready_ids("r1"))
        assert record["status"] == STATUS_FAILED
        assert "崩溃" in (record["error"] or "")
        assert not s.is_processed("r1")  # 失败不标记 → 下轮重试

    def test_run_reentrancy_skips(self, tmp_path):
        """另一线程正在运行时，本次 run 跳过（防重入）"""
        import threading
        import time

        s = make_screener(tmp_path)
        s.set_default_query("JD")

        # 独立线程持有锁（模拟正在运行）
        def hold_lock():
            s._lock.acquire()
            time.sleep(0.5)
            s._lock.release()

        t = threading.Thread(target=hold_lock)
        t.start()
        time.sleep(0.1)  # 确保锁已被持有

        record = s.run("manual", self._ready_ids("r1"))
        assert record["status"] == "skipped_running"
        t.join()

    def test_distributions_counted(self, tmp_path):
        def fake_payload(qm, ids):
            return {"total_candidates": 3, "candidates": [
                {"id": "a", "classification": "interview"},
                {"id": "b", "classification": "review"},
                {"id": "c", "classification": "interview"},
            ]}
        s = make_screener(tmp_path, run_cb=fake_payload)
        s.set_default_query("JD")
        record = s.run("manual", self._ready_ids("a", "b", "c"))
        assert record["distributions"] == {"interview": 2, "review": 1}


class TestPersistence:
    def test_results_persist_across_instances(self, tmp_path):
        s1 = make_screener(tmp_path)
        s1.set_default_query("JD")
        s1.run("manual", lambda: ["r1"])
        s2 = make_screener(tmp_path)
        runs = s2.list_runs()
        assert len(runs) == 1
        assert runs[0]["status"] == STATUS_COMPLETED

    def test_results_keep_recent_n(self, tmp_path):
        s = make_screener(tmp_path, max_runs=3)
        s.set_default_query("JD")
        # 触发 5 次（每次新增 1 份简历）
        for i in range(5):
            s.run("manual", lambda i=i: [f"r{i}"])
        runs = s.list_runs()
        assert len(runs) == 3  # 只保留最近 3 次

    def test_skipped_running_not_appended(self, tmp_path):
        """跳过（运行中）的记录不追加到结果文件"""
        import threading
        import time

        s = make_screener(tmp_path)
        s.set_default_query("JD")

        def hold_lock():
            s._lock.acquire()
            time.sleep(0.5)
            s._lock.release()

        t = threading.Thread(target=hold_lock)
        t.start()
        time.sleep(0.1)

        record = s.run("manual", lambda: ["r1"])
        assert record["status"] == "skipped_running"
        t.join()
        assert s.list_runs() == []  # skipped_running 不追加


class TestManualScreenAPI:
    """手动筛选工作流（独立于邮箱线）"""

    def test_manual_screen_screens_only_manual(self, isolated_auto_screen_dir, monkeypatch):
        """手动筛选只处理 source=manual 的简历"""
        from app.api import routes
        routes.resume_storage.clear()
        # 手动简历 2 份 + 邮箱简历 1 份
        for rid, src in [("m1", "manual"), ("m2", "manual"), ("e1", "email")]:
            routes.resume_storage[rid] = {"id": rid, "text": "x", "metadata": {},
                                          "filename": f"{rid}.txt", "source": src}
            routes.resume_tasks[rid] = {"status": "ready", "error": None}

        # mock 查询解析 + 筛选回调（记录收到的简历 id）
        mock_qp = MagicMock()
        mock_qp.parse_query.return_value = MagicMock()
        monkeypatch.setattr(routes, "query_parser", mock_qp)

        received = []
        def fake_screen(qm, ids):
            received.extend(ids)
            return {"total_candidates": len(ids), "candidates": [
                {"id": i, "classification": "review"} for i in ids]}

        monkeypatch.setattr(isolated_auto_screen_dir, "run_screening_cb", fake_screen)
        isolated_auto_screen_dir.set_default_query("JD")

        resp = client.post("/api/v1/manual-screen/run")
        assert resp.status_code == 200
        assert set(received) == {"m1", "m2"}  # 只筛手动简历，e1 不参与
        assert resp.json()["screened_count"] == 2

        # 手动筛选结果带 trigger=manual_screen，工作台可见
        runs = isolated_auto_screen_dir.list_runs()
        assert runs[0]["trigger"] == "manual_screen"


class TestAutoScreenAPI:
    """自动筛选 API（同步模式，query_parser 用 mock 避免真实 LLM）"""

    def _mock_query_parser(self, monkeypatch):
        from app.api import routes
        mock_qp = MagicMock()
        mock_qp.parse_query.return_value = MagicMock()
        monkeypatch.setattr(routes, "query_parser", mock_qp)
        return mock_qp

    def test_query_endpoints_roundtrip(self, isolated_auto_screen_dir, monkeypatch):
        # PUT
        resp = client.put("/api/v1/auto-screen/query", json={"query_text": "Python 后端工程师"})
        assert resp.status_code == 200
        assert resp.json()["query_text"] == "Python 后端工程师"
        # GET
        resp2 = client.get("/api/v1/auto-screen/query")
        assert resp2.json()["query_text"] == "Python 后端工程师"

    def test_put_empty_rejected(self, isolated_auto_screen_dir):
        resp = client.put("/api/v1/auto-screen/query", json={"query_text": "  "})
        assert resp.status_code == 400

    def test_manual_run_endpoint(self, isolated_auto_screen_dir, monkeypatch):
        from app.api import routes
        routes.resume_storage.clear()  # 清掉其他测试残留，保证待筛集合干净
        self._mock_query_parser(monkeypatch)
        # 注入 mock 筛选回调（避免真实 LLM；替换实例回调而非模块函数）
        def fake_screen(qm, ids):
            return {"total_candidates": 1, "candidates": [
                {"id": ids[0], "classification": "interview", "name": "张三"}]}
        monkeypatch.setattr(isolated_auto_screen_dir, "run_screening_cb", fake_screen)

        client.put("/api/v1/auto-screen/query", json={"query_text": "JD"})
        # 构造 1 份简历
        rid = "r1"
        routes.resume_storage[rid] = {"id": rid, "text": "x", "metadata": {}, "filename": "a.txt"}
        routes.resume_tasks[rid] = {"status": "ready", "error": None}
        try:
            resp = client.post("/api/v1/auto-screen/run")
            assert resp.status_code == 200
            assert resp.json()["status"] == "completed"
            # 结果查询
            results = client.get("/api/v1/auto-screen/results").json()
            assert len(results["runs"]) == 1
            assert results["runs"][0]["screened_count"] == 1
            assert results["runs"][0]["distributions"] == {"interview": 1}
        finally:
            routes.resume_storage.pop(rid, None)
            routes.resume_tasks.pop(rid, None)

    def test_results_empty_initial(self, isolated_auto_screen_dir):
        resp = client.get("/api/v1/auto-screen/results")
        assert resp.json()["runs"] == []

    def test_status_endpoint(self, isolated_auto_screen_dir):
        resp = client.get("/api/v1/auto-screen/status")
        data = resp.json()
        assert "enabled" in data
        assert "running" in data
        assert "default_query_set" in data
        assert data["default_query_set"] is False


if __name__ == "__main__":
    pytest.main([__file__])
