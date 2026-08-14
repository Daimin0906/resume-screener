"""
pytest 公共配置

在导入应用模块之前注入测试用的环境变量，避免 `app.api.routes` 在模块加载时
实例化 LLMClient / VectorStoreManager 因缺少 OPENAI_API_KEY 而报错。
这些是占位值，构造客户端时不会发起网络请求。
"""
import os
import hashlib

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://test.url/v1")
# 单元测试固定使用 ChromaDB 后端，避免误连真实 Milvus
os.environ["VECTOR_DB"] = "chroma"

# 新特性在测试环境默认关闭，保证现有测试行为与生产配置无关：
# 上传走同步管线、不注册定时任务、上传不触发预分类 LLM、结果不缓存
os.environ.setdefault("UPLOAD_ASYNC", "false")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("PRECLASSIFY_ON_INGEST", "false")
os.environ.setdefault("RESULTS_CACHE_ENABLED", "false")
# 测试固定走云端 embedding 分支（FakeEmbeddings 会替换 OpenAIEmbeddings），
# 避免加载本地模型或真实网络调用
os.environ.setdefault("EMBEDDING_BACKEND", "openai")
# 自动筛选 API 测试同步执行（确定性），数据目录由 isolated_auto_screen_dir 隔离
os.environ.setdefault("AUTO_SCREEN_ASYNC", "false")

import pytest


class FakeEmbeddings:
    """确定性假嵌入模型，避免测试发起真实网络请求。

    根据文本内容生成稳定的 8 维向量，相同文本得到相同向量。
    """

    DIM = 8

    def __init__(self, *args, **kwargs):
        # 兼容 OpenAIEmbeddings(model=..., openai_api_key=..., openai_api_base=...)
        pass

    def _embed(self, text: str):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # 取前 DIM 个字节归一化到 [0, 1)
        return [digest[i] / 255.0 for i in range(self.DIM)]

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    """全局替换 VectorStoreManager 使用的 OpenAIEmbeddings，避免真实网络调用。"""
    monkeypatch.setattr(
        "app.core.vector_store.OpenAIEmbeddings",
        FakeEmbeddings,
        raising=False,
    )
    # Milvus 后端（若被导入）同样替换为假嵌入
    monkeypatch.setattr(
        "app.core.milvus_store.OpenAIEmbeddings",
        FakeEmbeddings,
        raising=False,
    )
    yield


@pytest.fixture(autouse=True)
def isolated_rules_dir(monkeypatch, tmp_path):
    """所有测试将 rules 目录隔离到 tmp_path，避免污染真实 rules/ 目录。"""
    from app.api import routes
    from app.core.rules_manager import RulesManager
    from app.core.llm_client import LLMClient

    manager = RulesManager(LLMClient(), str(tmp_path / "rules"))
    monkeypatch.setattr(routes, "rules_manager", manager)
    return manager


@pytest.fixture(autouse=True)
def clean_upload_state():
    """每个测试后清空上传任务状态，防止跨用例污染。"""
    yield
    from app.api import routes
    routes.resume_tasks.clear()


@pytest.fixture(autouse=True)
def isolated_cache_dir(monkeypatch, tmp_path):
    """所有测试将缓存目录隔离到 tmp_path，避免跨用例缓存污染。"""
    from app.api import routes

    manager = routes.CacheManager(str(tmp_path / "cache"))
    monkeypatch.setattr(routes, "cache_manager", manager)
    return manager


@pytest.fixture(autouse=True)
def isolated_auto_screen_dir(monkeypatch, tmp_path):
    """所有测试将自动筛选数据目录隔离到 tmp_path，避免污染真实 data/。"""
    from app.api import routes
    from app.core.auto_screener import AutoScreener
    from app.core.workbench import Workbench

    screener = AutoScreener(
        str(tmp_path / "auto_data"),
        routes.query_parser,
        run_screening_cb=lambda qm, ids: {"total_candidates": 0, "candidates": []},
        rules_version_cb=lambda: 0,
    )
    monkeypatch.setattr(routes, "auto_screener", screener)
    # 工作台同样隔离（处理状态文件共用同一 data 目录）
    workbench = Workbench(str(tmp_path / "auto_data"))
    monkeypatch.setattr(routes, "workbench", workbench)
    return screener
