"""
测试文档解析与元数据提取模块
"""
import pytest
import os
from unittest.mock import patch, MagicMock

from app.core.document_parser import DocumentParser
from app.core.extractor import MetadataExtractor
from app.core.llm_client import LLMClient
from app.models.metadata import ResumeMetadata


class TestDocumentParser:
    """测试文档解析器"""

    def test_init(self):
        """测试初始化"""
        parser = DocumentParser()
        assert parser is not None

    # Note: PDF解析测试需要实际的PDF文件，这里省略


class TestMetadataExtractor:
    """测试元数据提取器"""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    def test_init(self):
        """测试初始化"""
        llm_client = LLMClient()
        extractor = MetadataExtractor(llm_client)
        assert extractor.llm_client == llm_client

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"})
    @patch("app.core.extractor.LLMClient")
    def test_extract_metadata(self, mock_llm_client_class):
        """测试元数据提取"""
        # Mock LLM客户端实例
        mock_llm_client = MagicMock()
        mock_llm_client.generate_text.return_value = '{"name": "张三", "email": "zhangsan@example.com", "phone": "13800138000"}'
        mock_llm_client_class.return_value = mock_llm_client
        
        # 创建提取器
        extractor = MetadataExtractor(mock_llm_client)
        
        # 测试提取元数据
        resume_text = "张三的简历内容..."
        metadata = extractor.extract_metadata(resume_text)
        
        # 验证结果
        assert isinstance(metadata, ResumeMetadata)
        assert metadata.name == "张三"
        assert metadata.email == "zhangsan@example.com"
        assert metadata.phone == "13800138000"

    def test_parse_response(self):
        """测试解析响应"""
        # 创建真实的提取器实例（不依赖LLM客户端）
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"}):
            llm_client = LLMClient()
            extractor = MetadataExtractor(llm_client)
            
            # 测试直接JSON响应
            response = '{"name": "李四", "email": "lisi@example.com"}'
            result = extractor._parse_response(response)
            assert result["name"] == "李四"
            assert result["email"] == "lisi@example.com"
            
            # 测试带额外文本的JSON响应
            response = '这是解释文本\n{"name": "王五", "email": "wangwu@example.com"}\n这是更多解释文本'
            result = extractor._parse_response(response)
            assert result["name"] == "王五"
            assert result["email"] == "wangwu@example.com"

    def test_parse_response_invalid_json(self):
        """测试解析无效JSON响应"""
        # 创建真实的提取器实例（不依赖LLM客户端）
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"}):
            llm_client = LLMClient()
            extractor = MetadataExtractor(llm_client)

            # 测试无效JSON响应
            response = "这不是有效的JSON"
            with pytest.raises(ValueError):
                extractor._parse_response(response)

    def test_parse_response_code_fence(self):
        """代码围栏包裹的 JSON 也能解析（glm-4-flash 常见输出）"""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"}):
            extractor = MetadataExtractor(LLMClient())
            response = '```json\n{"name": "张三", "skills": ["Python"]}\n```'
            result = extractor._parse_response(response)
            assert result["name"] == "张三"
            assert result["skills"] == ["Python"]

    def test_parse_response_with_control_chars(self):
        """JSON 内含非法控制字符（\x00 等）时清理后仍能解析"""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://test.url/v1"}):
            extractor = MetadataExtractor(LLMClient())
            # 模拟 LLM 输出里混入空字节（乱码 PDF 喂给 LLM 的典型结果）
            response = '{"name": "代\x00敏", "email": "\x00\x00@.com"}'
            result = extractor._parse_response(response)
            assert result["name"] == "代敏"


class TestDocumentParserRobustness:
    """文档解析鲁棒性：控制字符过滤 + 扫描件检测"""

    def test_clean_control_chars(self):
        """控制字符被过滤，正常文本保留"""
        from app.core.document_parser import _clean_control_chars
        assert _clean_control_chars("你好\x00世界") == "你好世界"
        assert _clean_control_chars("正常文本\n换行\t保留") == "正常文本\n换行\t保留"

    def test_is_garbled_normal_text(self):
        """正常简历文本不算乱码"""
        from app.core.document_parser import _is_garbled
        assert not _is_garbled("姓名：张三\n工作经历：5年Python后端经验")

    def test_is_garbled_scanned_pdf(self):
        """扫描件乱码（控制字符占比高）被识别"""
        from app.core.document_parser import _is_garbled
        assert _is_garbled("")
        assert _is_garbled("\x00\x00\x00\x01\x00")

    def test_parse_pdf_rejects_garbled(self, tmp_path, monkeypatch):
        """扫描件乱码 PDF 抛明确错误而非返回乱码"""
        from app.core.document_parser import DocumentParser
        parser = DocumentParser(cache_manager=None)

        # mock pypdf：提取出"扫描件乱码"（大量空字节）
        class FakePage:
            def extract_text(self):
                return "\x00\x00\x00\x01\x00\x00"

        class FakeReader:
            pages = [FakePage()]

        monkeypatch.setattr("pypdf.PdfReader", lambda stream: FakeReader())

        pdf_path = tmp_path / "scanned.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        with pytest.raises(ValueError, match="扫描件"):
            parser.parse_pdf(str(pdf_path))

    def test_parse_pdf_cleans_control_chars(self, tmp_path, monkeypatch):
        """正常文本 PDF 中的零星控制字符被过滤"""
        from app.core.document_parser import DocumentParser
        parser = DocumentParser(cache_manager=None)

        class FakePage:
            def extract_text(self):
                return "姓名\x00：张三\n工作经历：\x00Python 后端"

        class FakeReader:
            pages = [FakePage()]

        monkeypatch.setattr("pypdf.PdfReader", lambda stream: FakeReader())

        pdf_path = tmp_path / "normal.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        text = parser.parse_pdf(str(pdf_path))
        assert "\x00" not in text
        assert "张三" in text


if __name__ == "__main__":
    pytest.main([__file__])