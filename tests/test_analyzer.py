"""
测试候选人分析器模块
"""
import pytest
import os
import json
from unittest.mock import patch, MagicMock

from app.core.analyzer import CandidateAnalyzer
from app.core.llm_client import LLMClient
from app.models.metadata import QueryMetadata


class TestCandidateAnalyzer:
    """测试候选人分析器"""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    def test_init(self):
        """测试初始化"""
        llm_client = LLMClient()
        analyzer = CandidateAnalyzer(llm_client)
        assert analyzer.llm_client == llm_client

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    @patch("app.core.analyzer.LLMClient")
    def test_analyze_candidate(self, mock_llm_client_class):
        """测试分析单个候选人"""
        # Mock LLM客户端
        mock_llm_client = MagicMock()
        mock_llm_client.generate_text.return_value = "这是一份详细的候选人评价报告..."
        mock_llm_client_class.return_value = mock_llm_client
        
        # 创建分析器
        analyzer = CandidateAnalyzer(mock_llm_client)
        
        # 创建模拟简历数据
        resume = {
            "id": "resume_001",
            "metadata": {
                "name": "张三",
                "skills": ["Python", "Django"],
                "work_experience": [
                    {
                        "company": "互联网公司",
                        "title": "软件工程师",
                        "start_date": "2020-01",
                        "end_date": "2023-12",
                        "description": "负责后端开发工作"
                    }
                ],
                "education": [
                    {
                        "institution": "清华大学",
                        "major": "计算机科学",
                        "degree": "本科",
                        "start_date": "2016-09",
                        "end_date": "2020-06"
                    }
                ]
            }
        }
        
        # 创建查询元数据
        query_metadata = QueryMetadata(
            required_skills=["Python"],
            min_experience_years=3
        )
        
        # 分析候选人
        analyzed_candidate = analyzer.analyze_candidate(resume, query_metadata)
        
        # 验证结果
        assert analyzed_candidate["id"] == "resume_001"
        assert "analysis" in analyzed_candidate
        assert analyzed_candidate["analysis"] == "这是一份详细的候选人评价报告..."
        
        # 验证LLM客户端被调用
        mock_llm_client.generate_text.assert_called_once()

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    @patch("app.core.analyzer.LLMClient")
    def test_analyze_candidates(self, mock_llm_client_class):
        """测试批量分析候选人"""
        # Mock LLM客户端
        mock_llm_client = MagicMock()
        mock_llm_client.generate_text.return_value = "这是一份详细的候选人评价报告..."
        mock_llm_client_class.return_value = mock_llm_client
        
        # 创建分析器
        analyzer = CandidateAnalyzer(mock_llm_client)
        
        # 创建模拟简历数据
        resumes = [
            {
                "id": "resume_001",
                "metadata": {"name": "张三"}
            },
            {
                "id": "resume_002",
                "metadata": {"name": "李四"}
            }
        ]
        
        # 创建查询元数据
        query_metadata = QueryMetadata()
        
        # 批量分析候选人
        analyzed_candidates = analyzer.analyze_candidates(resumes, query_metadata)
        
        # 验证结果
        assert len(analyzed_candidates) == 2
        assert analyzed_candidates[0]["id"] == "resume_001"
        assert analyzed_candidates[1]["id"] == "resume_002"
        assert "analysis" in analyzed_candidates[0]
        assert "analysis" in analyzed_candidates[1]
        
        # 验证LLM客户端被调用两次
        assert mock_llm_client.generate_text.call_count == 2

    def test_format_work_experience(self):
        """测试格式化工作经历"""
        # 创建分析器实例（不依赖LLM客户端）
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"}):
            llm_client = LLMClient()
            analyzer = CandidateAnalyzer(llm_client)
        
        # 创建模拟工作经历数据
        work_experience = [
            {
                "company": "互联网公司",
                "title": "软件工程师",
                "start_date": "2020-01",
                "end_date": "2023-12",
                "description": "负责后端开发工作"
            }
        ]
        
        # 格式化工作经历
        formatted = analyzer._format_work_experience(work_experience)
        
        # 验证结果
        assert "互联网公司" in formatted
        assert "软件工程师" in formatted
        assert "2020-01" in formatted
        assert "2023-12" in formatted
        assert "负责后端开发工作" in formatted

    def test_format_education(self):
        """测试格式化教育背景"""
        # 创建分析器实例（不依赖LLM客户端）
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"}):
            llm_client = LLMClient()
            analyzer = CandidateAnalyzer(llm_client)
        
        # 创建模拟教育背景数据
        education = [
            {
                "institution": "清华大学",
                "major": "计算机科学",
                "degree": "本科",
                "start_date": "2016-09",
                "end_date": "2020-06"
            }
        ]
        
        # 格式化教育背景
        formatted = analyzer._format_education(education)
        
        # 验证结果
        assert "清华大学" in formatted
        assert "计算机科学" in formatted
        assert "本科" in formatted
        assert "2016-09" in formatted
        assert "2020-06" in formatted


class TestCandidateAnalyzerClassification:
    """测试三分类判定与结构化 JSON 解析"""

    SAMPLE_RESUME = {
        "id": "resume_001",
        "metadata": {
            "name": "张三",
            "skills": ["Python", "FastAPI"],
            "work_experience": [
                {
                    "company": "互联网公司",
                    "title": "后端负责人",
                    "start_date": "2020-01",
                    "end_date": "2023-12",
                    "description": "独立负责支付系统，服务 50 万用户，延迟降低 40%"
                }
            ],
            "projects": [
                {"name": "支付中台", "period": "2021-2023",
                 "description": "独立负责架构设计，服务 200 家商户"}
            ],
            "education": [{"institution": "清华大学", "degree": "本科"}],
            "summary": "5 年 Python 后端经验"
        },
        "scores": {"overall_score": 0.9},
    }

    def _analyzer(self, mock_response):
        mock_llm = MagicMock()
        mock_llm.generate_text.return_value = mock_response
        return CandidateAnalyzer(mock_llm), mock_llm

    def test_valid_json_classification(self):
        """LLM 返回合法完整 JSON → 分类解析成功"""
        response = json.dumps({
            "classification": "interview",
            "classification_reason": "独立负责支付系统且服务真实用户",
            "dimension_scores": {
                "skill_match": 0.9, "experience_match": 0.8, "education_match": 1.0,
                "ownership": 0.9, "real_users": 0.9, "quantified_results": 0.8,
            },
            "strengths": ["独立负责", "量化结果"],
            "risks": ["行业经验有限"],
            "recommendation": "## 综合评价\n该候选人能力突出",
        }, ensure_ascii=False)
        analyzer, _ = self._analyzer(response)

        result = analyzer.analyze_candidate(self.SAMPLE_RESUME, QueryMetadata())
        assert result["classification"] == "interview"
        assert result["classification_source"] == "llm"
        assert "支付系统" in result["classification_reason"]
        assert "综合评价" in result["analysis"]
        assert result["assessment"]["ownership"] == 0.9
        assert result["strengths"] == ["独立负责", "量化结果"]

    def test_code_fence_wrapped_json(self):
        """代码围栏包裹的 JSON 也能解析"""
        response = (
            "```json\n"
            + json.dumps({"classification": "reject", "classification_reason": "无真实项目",
                          "recommendation": "不推荐"}, ensure_ascii=False)
            + "\n```"
        )
        analyzer, _ = self._analyzer(response)
        result = analyzer.analyze_candidate(self.SAMPLE_RESUME, QueryMetadata())
        assert result["classification"] == "reject"
        assert result["classification_source"] == "llm"

    def test_free_text_fallback_to_heuristic(self):
        """自由文本（旧格式）→ 保留原文 + 启发式分类"""
        response = "这是一份详细的候选人评价报告...（自由文本）"
        analyzer, _ = self._analyzer(response)
        result = analyzer.analyze_candidate(self.SAMPLE_RESUME, QueryMetadata())
        assert result["analysis"] == response          # 原文保留
        assert result["classification"] == "interview"  # 0.9 >= 0.75
        assert result["classification_source"] == "heuristic"

    def test_json_with_surrounding_noise(self):
        """首尾带杂音的 JSON 能截取解析"""
        response = "好的，结果如下：" + json.dumps(
            {"classification": "review", "classification_reason": "证据不足",
             "recommendation": "建议人工核实"}, ensure_ascii=False) + "以上。"
        analyzer, _ = self._analyzer(response)
        result = analyzer.analyze_candidate(self.SAMPLE_RESUME, QueryMetadata())
        assert result["classification"] == "review"
        assert result["classification_source"] == "llm"

    def test_low_score_heuristic_reject(self):
        """低分简历启发式兜底 → reject"""
        analyzer, _ = self._analyzer("无 JSON 输出")
        resume = {**self.SAMPLE_RESUME, "scores": {"overall_score": 0.3}}
        result = analyzer.analyze_candidate(resume, QueryMetadata())
        assert result["classification"] == "reject"

    def test_rules_text_injected_into_prompt(self):
        """rules_text 注入 prompt，无规则时不注入"""
        analyzer, mock_llm = self._analyzer(json.dumps(
            {"classification": "interview", "classification_reason": "ok",
             "recommendation": "ok"}, ensure_ascii=False))

        analyzer.analyze_candidate(self.SAMPLE_RESUME, QueryMetadata(), rules_text="- 只看真实用户")
        prompt_with_rules = mock_llm.generate_text.call_args[0][0]
        assert "筛选规则（来自HR历史纠正反馈" in prompt_with_rules
        assert "只看真实用户" in prompt_with_rules

        analyzer.analyze_candidate(self.SAMPLE_RESUME, QueryMetadata())
        prompt_without = mock_llm.generate_text.call_args[0][0]
        assert "筛选规则（来自HR历史纠正反馈" not in prompt_without

    def test_prompt_contains_projects_and_quality_dimensions(self):
        """prompt 包含项目经历与三个质量维度"""
        analyzer, mock_llm = self._analyzer(json.dumps(
            {"classification": "interview", "classification_reason": "ok",
             "recommendation": "ok"}, ensure_ascii=False))
        analyzer.analyze_candidate(self.SAMPLE_RESUME, QueryMetadata())
        prompt = mock_llm.generate_text.call_args[0][0]
        assert "支付中台" in prompt            # 项目经历字段
        assert "独立负责度" in prompt          # 质量维度
        assert "真实用户/客户" in prompt
        assert "可量化结果" in prompt


if __name__ == "__main__":
    pytest.main([__file__])