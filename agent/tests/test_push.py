"""
测试飞书推送（mock requests）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from unittest.mock import patch, MagicMock

from agent.push import FeishuPusher


class TestFeishuPusher:
    def test_no_webhook_prints_console(self, capsys):
        """未配置 webhook：控制台输出，返回成功"""
        pusher = FeishuPusher("")
        ok = pusher.push_candidates([{"name": "张三", "phone": "138", "skills": ["Python"]}])
        assert ok is True
        out = capsys.readouterr().out
        assert "张三" in out

    def test_push_success(self):
        """webhook 推送成功"""
        pusher = FeishuPusher("https://open.feishu.cn/bot/hook/test")
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(ok=True, json=lambda: {"code": 0})
            ok = pusher.push_candidates([{"name": "张三", "phone": "138", "skills": ["Python"]}])
        assert ok is True
        # 校验请求体包含候选人信息
        payload = mock_post.call_args.kwargs["json"]
        assert "张三" in payload["content"]["text"]
        assert "138" in payload["content"]["text"]

    def test_push_failure_retries(self):
        """失败重试 1 次后仍失败"""
        pusher = FeishuPusher("https://open.feishu.cn/bot/hook/test")
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(ok=False, json=lambda: {"code": 19001})
            ok = pusher.push_candidates([{"name": "张三"}])
        assert ok is False
        assert mock_post.call_count == 2  # 重试 1 次

    def test_empty_candidates_no_call(self):
        pusher = FeishuPusher("https://x")
        with patch("requests.post") as mock_post:
            ok = pusher.push_candidates([])
        assert ok is True
        mock_post.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
