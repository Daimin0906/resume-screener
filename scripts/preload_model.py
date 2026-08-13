"""
预下载本地向量模型（Docker 构建时调用，也可本地手动执行）

用途：阿里云等国内服务器首次运行 fastembed 时需从 HuggingFace 下载模型（~100MB），
构建时预下载可避免运行时卡顿。若使用云端 embedding（EMBEDDING_BACKEND=openai）可跳过。
"""
import os
import sys
from pathlib import Path

# 确保可从任意位置导入 app 包（Docker 内 PYTHONPATH=/app 时自动跳过）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["EMBEDDING_BACKEND"] = "local"

from app.core.vector_store import LocalEmbeddings  # noqa: E402

LocalEmbeddings()
print("本地向量模型预下载完成")
