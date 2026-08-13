"""
测试定时任务（P4）：create_scheduler / register_jobs
"""
import pytest
from unittest.mock import MagicMock, patch

from app.core import scheduler as scheduler_mod
from config.config import settings


class TestScheduler:
    def test_create_scheduler_disabled(self, monkeypatch):
        """SCHEDULER_ENABLED=false → None"""
        monkeypatch.setattr(settings, "SCHEDULER_ENABLED", False)
        assert scheduler_mod.create_scheduler() is None

    def test_create_scheduler_enabled(self, monkeypatch):
        """开启 + 依赖已装 → BackgroundScheduler 实例"""
        monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
        sched = scheduler_mod.create_scheduler()
        assert sched is not None
        assert sched.__class__.__name__ == "BackgroundScheduler"
        sched.start()
        sched.shutdown(wait=False)

    def test_create_scheduler_import_error(self, monkeypatch):
        """apscheduler 导入失败 → None（优雅降级）"""
        monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("apscheduler"):
                raise ImportError("apscheduler not available")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert scheduler_mod.create_scheduler() is None

    def test_register_jobs(self, monkeypatch):
        """注册两个任务（邮箱开启时）+ 防重入配置"""
        monkeypatch.setattr(settings, "IMAP_ENABLED", True)
        monkeypatch.setattr(settings, "SCHEDULER_EMAIL_FETCH_INTERVAL_MINUTES", 30)
        monkeypatch.setattr(settings, "SCHEDULER_PRECLASSIFY_INTERVAL_MINUTES", 60)

        mock_sched = MagicMock()
        ids = scheduler_mod.register_jobs(
            mock_sched,
            lambda: None,
            lambda: {"processed": 0},
        )
        assert ids == ["email_fetch", "preclassify"]
        assert mock_sched.add_job.call_count == 2
        # 所有任务 max_instances=1（防重入）
        for call in mock_sched.add_job.call_args_list:
            assert call.kwargs.get("max_instances") == 1

    def test_register_jobs_without_email(self, monkeypatch):
        """邮箱未开启 → 只注册预分类任务"""
        monkeypatch.setattr(settings, "IMAP_ENABLED", False)
        mock_sched = MagicMock()
        ids = scheduler_mod.register_jobs(mock_sched, lambda: None, lambda: None)
        assert ids == ["preclassify"]
        assert mock_sched.add_job.call_count == 1


if __name__ == "__main__":
    pytest.main([__file__])
