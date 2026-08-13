import io
import os
import re
import hashlib
from typing import List, Dict, Any

import pypdf
from app.core.cache_manager import CacheManager
from loguru import logger


def _clean_control_chars(text: str) -> str:
    """过滤控制字符（\x00-\x08、\x0b-\x1f 等），保留换行/制表等常用空白。

    PDF 文本提取常混入空字节（\x00），会破坏 LLM 调用与 JSON 解析。
    """
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def _is_garbled(text: str) -> bool:
    """检测文本是否几乎不可用（扫描件/图片型 PDF 提取出的乱码）。

    可打印字符占比低于 50% 视为乱码（正常简历文本占比应接近 100%）。
    """
    if not text:
        return True
    printable = sum(1 for ch in text if ch.isprintable())
    return printable / len(text) < 0.5


class DocumentParser:
    """
    文档解析器接口 (基础PDF转文本)
    """

    def __init__(self, cache_manager: CacheManager = None):
        """
        初始化文档解析器

        Args:
            cache_manager (CacheManager, optional): 缓存管理器实例
        """
        self.cache_manager = cache_manager
        logger.info("Initialized DocumentParser")

    def parse_pdf(self, file_path: str) -> str:
        """
        解析PDF文件为文本

        Args:
            file_path (str): PDF文件路径

        Returns:
            str: 提取的文本内容

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件不是PDF格式
            Exception: 解析失败
        """
        try:
            if not os.path.exists(file_path):
                logger.error(f"File not found: {file_path}")
                raise FileNotFoundError(f"File not found: {file_path}")

            if not file_path.lower().endswith('.pdf'):
                logger.error(f"File is not a PDF: {file_path}")
                raise ValueError(f"File is not a PDF: {file_path}")

            # 读取文件内容并生成内容哈希作为缓存键，避免路径相同但内容变化时返回旧结果
            with open(file_path, 'rb') as f:
                file_bytes = f.read()
            cache_key = f"pdf_text_{hashlib.md5(file_bytes).hexdigest()}"

            if self.cache_manager:
                cached_result = self.cache_manager.get(cache_key)
                if cached_result:
                    logger.info(f"Retrieved PDF text from cache for file: {file_path}")
                    return cached_result

            # 解析PDF
            text = ""
            pdf_reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            for page_num, page in enumerate(pdf_reader.pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text
                    logger.debug(f"Extracted text from page {page_num + 1}")
                except Exception as e:
                    logger.warning(f"Failed to extract text from page {page_num + 1}: {e}")
                    continue

            # 过滤控制字符（PDF 提取常混入 \x00 等空字节，会破坏 LLM 调用与 JSON 解析）
            text = _clean_control_chars(text)

            # 扫描件/图片型 PDF 检测：提取的文本几乎不可用（可打印字符占比过低）
            # 此时明确报错，而不是把乱码喂给 LLM 导致下游解析失败
            if _is_garbled(text):
                raise ValueError(
                    "PDF 无法提取有效文本，可能是扫描件/图片型 PDF，请使用文本型 PDF 或先 OCR"
                )

            if self.cache_manager:
                self.cache_manager.set(cache_key, text, expire=3600)
                logger.info(f"Cached PDF text for file: {file_path}")

            logger.info(f"Parsed PDF file: {file_path}, extracted {len(text)} characters")
            return text

        except FileNotFoundError:
            raise
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to parse PDF file {file_path}: {e}")
            raise Exception(f"Failed to parse PDF file {file_path}: {e}")

    def parse_multiple_pdfs(self, file_paths: List[str]) -> Dict[str, str]:
        """
        解析多个PDF文件

        Args:
            file_paths (List[str]): PDF文件路径列表

        Returns:
            Dict[str, str]: 文件路径到提取文本的映射
        """
        results = {}
        for file_path in file_paths:
            try:
                text = self.parse_pdf(file_path)
                results[file_path] = text
            except Exception as e:
                logger.error(f"Failed to parse PDF file {file_path}: {e}")
                results[file_path] = f"ERROR: {str(e)}"
        return results
