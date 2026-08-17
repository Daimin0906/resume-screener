"""
测试硬指标筛选器（确定性规则）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.screen import HardScreener, ScreenConfig, extract_resume

CONFIG = {"screen": {
    "required_skills": ["Python", "FastAPI"],
    "skill_hit_ratio": 0.6,
    "min_experience_years": 3,
    "min_education": "本科",
    "locations": ["北京"],
}}

RESUME_PASS = """
姓名：张三
电话：13800138000
性别：男
求职意向：Python 后端工程师
工作经历：某公司（2020-01 至 至今） 后端工程师
技能：Python、FastAPI、MySQL、Redis、Docker
教育：清华大学 本科
期望地点：北京
"""

RESUME_FAIL_EDU = """
姓名：李四
电话：13900139000
工作经历：某公司（2018-01 至 至今） 工程师
技能：Python、FastAPI
教育：某职业学院 大专
期望地点：北京
"""

RESUME_FAIL_SKILL = """
姓名：王五
电话：13700137000
工作经历：某公司（2020-01 至 至今） 前端工程师
技能：JavaScript、Vue、React
教育：某大学 本科
期望地点：北京
"""

RESUME_FAIL_EXP = """
姓名：赵六
电话：13600136000
工作经历：某公司（2024-01 至 至今） 工程师
技能：Python、FastAPI
教育：某大学 本科
期望地点：北京
"""


class TestExtract:
    def test_extract_basic_fields(self):
        r = extract_resume(RESUME_PASS)
        assert r.name == "张三"
        assert r.phone == "13800138000"
        assert r.education == "本科"
        assert r.experience_years >= 3
        assert "python" in r.skills
        assert "北京" in r.locations
        assert r.gender == "男"


class TestScreen:
    def setup_method(self):
        self.screener = HardScreener(ScreenConfig(CONFIG))

    def test_pass(self):
        result = self.screener.screen(extract_resume(RESUME_PASS))
        assert result["passed"] is True
        assert result["failed_reasons"] == []
        assert result["passed_reasons"]

    def test_fail_education(self):
        result = self.screener.screen(extract_resume(RESUME_FAIL_EDU))
        assert result["passed"] is False
        assert any("学历" in r for r in result["failed_reasons"])

    def test_fail_skill(self):
        result = self.screener.screen(extract_resume(RESUME_FAIL_SKILL))
        assert result["passed"] is False
        assert any("技能" in r for r in result["failed_reasons"])

    def test_fail_experience(self):
        result = self.screener.screen(extract_resume(RESUME_FAIL_EXP))
        assert result["passed"] is False
        assert any("经验" in r for r in result["failed_reasons"])

    def test_empty_skills_config(self):
        """无技能要求时，技能维度不淘汰（其余维度仍正常判定）"""
        cfg = dict(CONFIG)
        cfg["screen"] = dict(CONFIG["screen"])
        cfg["screen"]["required_skills"] = []
        screener = HardScreener(ScreenConfig(cfg))
        # RESUME_FAIL_SKILL：学历本科、经验足够、地点北京 → 移除技能维度后应通过
        result = screener.screen(extract_resume(RESUME_FAIL_SKILL))
        assert result["passed"] is True
        assert not any("技能" in r for r in result["failed_reasons"])


if __name__ == "__main__":
    pytest.main([__file__])
