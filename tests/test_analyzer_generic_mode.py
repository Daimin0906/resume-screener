"""
测试 analyzer 通用评估模式（无岗位需求，入库预分类用）
"""
import json

import pytest
from unittest.mock import MagicMock, patch

from app.core.analyzer import CandidateAnalyzer
from app.models.metadata import QueryMetadata

SAMPLE_RESUME = {
    "id": "resume_001",
    "text": "5年Python后端经验，独立负责支付系统，服务50万用户",
    "metadata": {
        "name": "张三",
        "skills": ["Python", "FastAPI"],
        "work_experience": [
            {"company": "互联网公司", "title": "后端负责人",
             "start_date": "2020-01", "end_date": "2023-12",
             "description": "独立负责支付系统，服务 50 万用户，延迟降低 40%"}
        ],
        "projects": [
            {"name": "支付中台", "description": "独立负责架构设计，服务 200 家商户"}
        ],
        "education": [{"institution": "清华大学", "degree": "本科"}],
        "summary": "5 年 Python 后端经验",
    },
    "scores": {"overall_score": 0.9},
}

VALID_RESPONSE = json.dumps({
    "classification": "interview",
    "classification_reason": "独立负责且服务真实用户",
    "dimension_scores": {"skill_match": 0.9, "ownership": 0.9,
                         "real_users": 0.9, "quantified_results": 0.8},
    "strengths": ["独立负责"],
    "risks": ["行业有限"],
    "recommendation": "## 综合评价\n能力突出",
}, ensure_ascii=False)


class TestAnalyzerGenericMode:
    def _analyzer(self, response=VALID_RESPONSE):
        mock_llm = MagicMock()
        mock_llm.generate_text.return_value = response
        return CandidateAnalyzer(mock_llm), mock_llm

    def test_analyze_with_none_query_metadata(self):
        """query_metadata=None 不崩溃，正常产出分类"""
        analyzer, _ = self._analyzer()
        result = analyzer.analyze_candidate(SAMPLE_RESUME, None)
        assert result["classification"] == "interview"
        assert result["classification_source"] == "llm"

    def test_generic_prompt_contains_generic_mode_text(self):
        """通用模式 prompt 包含通用判定标准文案"""
        analyzer, mock_llm = self._analyzer()
        analyzer.analyze_candidate(SAMPLE_RESUME, None)
        prompt = mock_llm.generate_text.call_args[0][0]
        assert "通用评估模式" in prompt
        assert "无特定岗位需求" in prompt
        # 通用标准关键词
        assert "独立负责/主导过项目" in prompt

    def test_generic_prompt_reuses_dimensions(self):
        """通用模式仍包含三个质量维度和 JSON 输出约束"""
        analyzer, mock_llm = self._analyzer()
        analyzer.analyze_candidate(SAMPLE_RESUME, None)
        prompt = mock_llm.generate_text.call_args[0][0]
        assert "独立负责度" in prompt
        assert "真实用户/客户" in prompt
        assert "可量化结果" in prompt
        assert '"classification"' in prompt

    def test_generic_mode_rules_text_injected(self):
        """通用模式仍注入规则文本"""
        analyzer, mock_llm = self._analyzer()
        analyzer.analyze_candidate(SAMPLE_RESUME, None, rules_text="- 只看真实用户")
        prompt = mock_llm.generate_text.call_args[0][0]
        assert "只看真实用户" in prompt

    def test_non_generic_prompt_unchanged(self):
        """非通用模式 prompt 与现状一致（保留原职位要求四段）"""
        analyzer, mock_llm = self._analyzer()
        qm = QueryMetadata(required_skills=["Python"], min_experience_years=3)
        analyzer.analyze_candidate(SAMPLE_RESUME, qm)
        prompt = mock_llm.generate_text.call_args[0][0]
        assert "通用评估模式" not in prompt
        assert "必需技能: Python" in prompt
        assert "最少经验年限: 3" in prompt
        assert "筛选规则（来自HR历史纠正反馈" not in prompt  # 无规则时不注入

    def test_analyze_candidates_batch_generic(self):
        """批量通用模式可用"""
        analyzer, mock_llm = self._analyzer()
        resumes = [SAMPLE_RESUME, {**SAMPLE_RESUME, "id": "resume_002"}]
        results = analyzer.analyze_candidates(resumes, None, rules_text="- 规则")
        assert len(results) == 2
        assert mock_llm.generate_text.call_count == 2

    def test_preclassification_injected_into_prompt(self):
        """简历带通用评估时，prompt 注入通用评估参考段"""
        analyzer, mock_llm = self._analyzer()
        resume = {
            **SAMPLE_RESUME,
            "preclassification": {
                "classification": "interview",
                "reason": "独立负责过千万级订单系统",
                "rule_version": 1,
            },
        }
        analyzer.analyze_candidate(resume, QueryMetadata())
        prompt = mock_llm.generate_text.call_args[0][0]
        assert "通用评估参考" in prompt
        assert "值得面试" in prompt
        assert "独立负责过千万级订单系统" in prompt

    def test_no_preclassification_no_section(self):
        """简历无通用评估时不注入参考段"""
        analyzer, mock_llm = self._analyzer()
        analyzer.analyze_candidate(SAMPLE_RESUME, QueryMetadata())
        prompt = mock_llm.generate_text.call_args[0][0]
        assert "通用评估参考" not in prompt


if __name__ == "__main__":
    pytest.main([__file__])
