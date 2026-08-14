"""
测试查询理解模块
"""
import pytest
import os
import json
from unittest.mock import patch, MagicMock

from app.core.query_parser import QueryParser
from app.core.llm_client import LLMClient
from app.models.metadata import QueryMetadata


class TestQueryParser:
    """测试查询解析器"""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    def test_init(self):
        """测试初始化"""
        llm_client = LLMClient()
        parser = QueryParser(llm_client)
        assert parser.llm_client == llm_client

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    @patch("app.core.query_parser.LLMClient")
    def test_parse_query(self, mock_llm_client_class):
        """测试查询解析"""
        # Mock LLM客户端实例
        mock_llm_client = MagicMock()
        mock_response = '{"keywords": ["Python", "后端"], "required_skills": ["Python", "Django"], "min_experience_years": 3}'
        mock_llm_client.generate_text.return_value = mock_response
        mock_llm_client_class.return_value = mock_llm_client

        # 创建查询解析器
        parser = QueryParser(mock_llm_client)

        # 测试解析查询
        query_text = "寻找3年以上经验的Python后端工程师，熟悉Django框架"
        query_metadata = parser.parse_query(query_text)

        # 验证结果
        assert isinstance(query_metadata, QueryMetadata)
        assert "Python" in query_metadata.keywords
        assert "Python" in query_metadata.required_skills
        assert "Django" in query_metadata.required_skills
        assert query_metadata.min_experience_years == 3

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    def test_parsing_prompt_limits_required_skills(self):
        """提示词约束必需技能数量（≤5）且排除职责描述词"""
        parser = QueryParser(LLMClient())
        prompt = parser._create_parsing_prompt("测试岗位")
        assert "最多5个" in prompt
        assert "脚本编写" in prompt          # 职责描述词示例被明确排除
        assert "除核心必需技能外" in prompt  # 其余技能归入优先

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    @patch("app.core.query_parser.LLMClient")
    def test_parse_query_truncates_excess_required_skills(self, mock_llm_client_class):
        """模型不遵守约束时，代码层强制截断必需技能（>5 降级为优先技能）"""
        mock_llm = MagicMock()
        mock_llm.generate_text.return_value = json.dumps({
            "required_skills": ["S1", "S2", "S3", "S4", "S5", "S6", "S7"],
            "preferred_skills": ["P1"],
        }, ensure_ascii=False)
        mock_llm_client_class.return_value = mock_llm
        parser = QueryParser(mock_llm)

        md = parser.parse_query("测试")
        assert md.required_skills == ["S1", "S2", "S3", "S4", "S5"]
        assert set(md.preferred_skills) == {"P1", "S6", "S7"}

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    @patch("app.core.query_parser.LLMClient")
    def test_parse_query_keeps_within_limit(self, mock_llm_client_class):
        """必需技能未超上限时不做截断"""
        mock_llm = MagicMock()
        mock_llm.generate_text.return_value = json.dumps({
            "required_skills": ["Python", "FastAPI", "RAG"],
        }, ensure_ascii=False)
        mock_llm_client_class.return_value = mock_llm
        parser = QueryParser(mock_llm)

        md = parser.parse_query("测试")
        assert md.required_skills == ["Python", "FastAPI", "RAG"]

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    @patch("app.core.query_parser.LLMClient")
    def test_parse_query_filters_placeholder_values(self, mock_llm_client_class):
        """过滤模型编造的占位词（未提及等），防止污染硬性过滤字段"""
        mock_llm = MagicMock()
        mock_llm.generate_text.return_value = json.dumps({
            "locations": ["未提及"],
            "required_languages": ["中文", "英语", "未提及"],
            "required_certifications": ["未提及", "N/A"],
            "required_skills": ["Python"],
        }, ensure_ascii=False)
        mock_llm_client_class.return_value = mock_llm
        parser = QueryParser(mock_llm)

        md = parser.parse_query("测试")
        assert md.locations == []                # 未提及被过滤
        assert md.required_languages == ["中文", "英语"]  # 真实语言保留
        assert md.required_certifications == []  # 占位词全部过滤

    def test_parse_response(self):
        """测试解析响应"""
        # 创建真实的查询解析器实例（不依赖LLM客户端）
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"}):
            llm_client = LLMClient()
            parser = QueryParser(llm_client)
            
            # 测试直接JSON响应
            response = '{"keywords": ["Java", "后端"], "min_experience_years": 5}'
            result = parser._parse_response(response)
            assert "Java" in result["keywords"]
            assert result["min_experience_years"] == 5
            
            # 测试带额外文本的JSON响应
            response = '这是解释文本\\n{"keywords": ["Python", "前端"], "min_experience_years": 2}\\n这是更多解释文本'
            result = parser._parse_response(response)
            assert "Python" in result["keywords"]
            assert result["min_experience_years"] == 2

    def test_parse_response_invalid_json(self):
        """测试解析无效JSON响应"""
        # 创建真实的查询解析器实例（不依赖LLM客户端）
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"}):
            llm_client = LLMClient()
            parser = QueryParser(llm_client)
            
            # 测试无效JSON响应
            response = "这不是有效的JSON"
            with pytest.raises(ValueError):
                parser._parse_response(response)


if __name__ == "__main__":
    pytest.main([__file__])