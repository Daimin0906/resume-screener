# 使用官方Python运行时作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    # 国内镜像：HuggingFace 模型下载加速（阿里云服务器访问 HF 慢/失败）
    HF_ENDPOINT=https://hf-mirror.com

# 安装系统依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 复制requirements.txt文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 创建数据目录（运行时挂载 volume 持久化：chroma_db / cache / rules）
RUN mkdir -p data cache chroma_db rules

# 构建时预下载本地向量模型（BGE-small-zh ~100MB），避免运行时首次下载卡顿
# 若使用云端 embedding（EMBEDDING_BACKEND=openai），此步可跳过
RUN python scripts/preload_model.py || echo "（模型预下载跳过：可忽略或检查 HF_ENDPOINT 网络）"

# 暴露端口
EXPOSE 8000

# 启动应用
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
