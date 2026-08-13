"""
集中式配置模块

所有运行时配置统一从环境变量读取（支持 .env 文件）。其他模块可通过
`from config.config import settings` 获取配置默认值。

支持为 LLM（对话）与 Embedding（向量化）分别配置 key / base_url / model，
并向后兼容统一的 OPENAI_API_KEY / OPENAI_BASE_URL。
"""
import os

# 加载 .env 文件（如果安装了 python-dotenv 且存在 .env）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - python-dotenv 未安装时静默跳过
    pass


def _first_env(*names: str, default: str = "") -> str:
    """按顺序返回第一个非空环境变量值，全部为空时返回 default。"""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


class Settings:
    """应用配置项"""

    # 兼容旧的统一配置（作为 LLM / Embedding 的回退）
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

    # ---- LLM（对话）----
    # 优先 LLM_*，回退到 OPENAI_*
    LLM_API_KEY: str = _first_env("LLM_API_KEY", "OPENAI_API_KEY")
    LLM_BASE_URL = _first_env("LLM_BASE_URL", "OPENAI_BASE_URL") or None
    # 兼容用户可能写成 LL_MODEL 的拼写
    LLM_MODEL: str = _first_env("LLM_MODEL", "LL_MODEL", default="gpt-4o")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))

    # ---- Embedding（向量化）----
    # 优先 EMBEDDING_*，回退到 LLM_* / OPENAI_*
    EMBEDDING_API_KEY: str = _first_env("EMBEDDING_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY")
    EMBEDDING_BASE_URL = _first_env("EMBEDDING_BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL") or None
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    # 嵌入向量维度（智谱 embedding-3 支持自定义，默认 2048；留空则用服务端默认）
    EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS")) if os.getenv("EMBEDDING_DIMENSIONS") else 2048
    # 嵌入后端：local（本地 fastembed，免费无限）/ openai（云端 OpenAI 兼容 API）
    EMBEDDING_BACKEND: str = os.getenv("EMBEDDING_BACKEND", "openai").lower()
    # 本地嵌入模型（fastembed 支持 BGE 中文系列）
    LOCAL_EMBEDDING_MODEL: str = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    # 向量数据库
    # 选择向量库后端：chroma（默认，本地持久化）/ milvus（Milvus/Zilliz Cloud）
    VECTOR_DB: str = os.getenv("VECTOR_DB", "chroma").lower()
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")

    # ---- Milvus / Zilliz Cloud（当 VECTOR_DB=milvus 时生效）----
    # 优先使用完整 URI；若只提供 HOST/PORT 则自动拼接
    _MILVUS_HOST = os.getenv("MILVUS_HOST", "")
    _MILVUS_PORT = os.getenv("MILVUS_PORT", "")

    @staticmethod
    def _build_milvus_uri(uri: str, host: str, port: str) -> str:
        if uri:
            return uri
        if not host:
            return ""
        # host 已带协议（http/https）时按需补端口；否则按 host:port 处理
        if host.startswith("http://") or host.startswith("https://"):
            if port and port not in ("80", "443"):
                return f"{host}:{port}"
            return host
        if port:
            return f"http://{host}:{port}"
        return host

    MILVUS_URI: str = _build_milvus_uri(
        os.getenv("MILVUS_URI", ""), _MILVUS_HOST, _MILVUS_PORT
    )
    MILVUS_TOKEN: str = os.getenv("MILVUS_TOKEN", "")
    # 简历专用集合名（避免污染其他业务集合，如 customer_service_kb）
    MILVUS_COLLECTION: str = os.getenv("MILVUS_COLLECTION", "resume_screening")
    MILVUS_INDEX: str = os.getenv("MILVUS_INDEX") or os.getenv("INDEX") or "AUTOINDEX"

    # ---- 服务/跨域 ----
    # 允许的前端来源（逗号分隔）；为空时允许所有来源
    _ALLOWED_ORIGINS_RAW = os.getenv("SERVER_ALLOWED_ORIGINS", "")
    SERVER_ALLOWED_ORIGINS = (
        [o.strip() for o in _ALLOWED_ORIGINS_RAW.split(",") if o.strip()]
        if _ALLOWED_ORIGINS_RAW
        else ["*"]
    )

    # 缓存
    CACHE_DIR: str = os.getenv("CACHE_DIR", "./cache")

    # 必需技能的通过命中比例（0~1）。JD 常解析出大量"必需"技能，
    # 全部命中不现实，命中比例达到该阈值即视为通过硬性过滤。
    REQUIRED_SKILL_HIT_RATIO: float = float(os.getenv("REQUIRED_SKILL_HIT_RATIO", "0.7"))

    # ---- 邮箱抓取（IMAP）----
    IMAP_ENABLED: bool = os.getenv("IMAP_ENABLED", "false").lower() == "true"
    IMAP_HOST: str = os.getenv("IMAP_HOST", "")
    IMAP_PORT: int = int(os.getenv("IMAP_PORT", "993"))
    IMAP_USER: str = os.getenv("IMAP_USER", "")
    IMAP_PASSWORD: str = os.getenv("IMAP_PASSWORD", "")
    IMAP_SSL: bool = os.getenv("IMAP_SSL", "true").lower() == "true"
    IMAP_MAILBOX: str = os.getenv("IMAP_MAILBOX", "INBOX")
    IMAP_MARK_READ: bool = os.getenv("IMAP_MARK_READ", "true").lower() == "true"
    IMAP_ATTACHMENT_MAX_MB: int = int(os.getenv("IMAP_ATTACHMENT_MAX_MB", "10"))

    # ---- 定时任务 ----
    SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    SCHEDULER_EMAIL_FETCH_INTERVAL_MINUTES: int = int(os.getenv("SCHEDULER_EMAIL_FETCH_INTERVAL_MINUTES", "30"))
    SCHEDULER_PRECLASSIFY_INTERVAL_MINUTES: int = int(os.getenv("SCHEDULER_PRECLASSIFY_INTERVAL_MINUTES", "60"))

    # ---- 规则对比验证 ----
    RULES_COMPARE_MAX_RESUMES: int = int(os.getenv("RULES_COMPARE_MAX_RESUMES", "50"))

    # ---- 并发 / 缓存 ----
    # 上传是否异步处理（true=立即返回，后台线程池解析；false=同步等待，测试默认）
    UPLOAD_ASYNC: bool = os.getenv("UPLOAD_ASYNC", "true").lower() == "true"
    UPLOAD_MAX_WORKERS: int = int(os.getenv("UPLOAD_MAX_WORKERS", "8"))
    # 候选人分析并发路数
    ANALYZER_MAX_WORKERS: int = int(os.getenv("ANALYZER_MAX_WORKERS", "8"))
    # 筛选结果缓存（键含规则版本，规则更新自动失效）
    RESULTS_CACHE_ENABLED: bool = os.getenv("RESULTS_CACHE_ENABLED", "true").lower() == "true"
    RESULTS_CACHE_TTL_SECONDS: int = int(os.getenv("RESULTS_CACHE_TTL_SECONDS", "1800"))

    # ---- 入库即预分类 ----
    # 上传解析完成后，自动按当前规则做一次无岗位通用分类（仅列表/详情展示）
    PRECLASSIFY_ON_INGEST: bool = os.getenv("PRECLASSIFY_ON_INGEST", "true").lower() == "true"

    # ---- 筛选规则 / 反馈自迭代 ----
    # 规则与反馈日志存储目录（JSON 文件）
    RULES_DIR: str = os.getenv("RULES_DIR", "rules")
    # 触发规则总结所需的最少新反馈条数
    RULES_MIN_FEEDBACK_FOR_SUMMARIZE: int = int(os.getenv("RULES_MIN_FEEDBACK_FOR_SUMMARIZE", "3"))
    # 反馈日志保留的最大条数（超出裁剪旧条目，total_count 仍累计）
    RULES_MAX_FEEDBACK_ENTRIES: int = int(os.getenv("RULES_MAX_FEEDBACK_ENTRIES", "1000"))
    # 单次总结生成规则的最大条数
    RULES_MAX_RULES: int = int(os.getenv("RULES_MAX_RULES", "5"))

    # 日志级别
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
