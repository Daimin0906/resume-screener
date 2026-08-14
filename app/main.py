"""
应用入口点
"""
import os
from contextlib import asynccontextmanager

from loguru import logger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes
from app.core.scheduler import create_scheduler, register_jobs
from config.config import settings


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


class NoCacheStaticFiles(StaticFiles):
    """静态文件响应带 no-cache：浏览器每次刷新都会重新验证，
    避免开发期前端代码改动后仍命中旧缓存。"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动钩子：
    1. 从向量库恢复内存简历索引（重启不丢列表）
    2. 存量任务状态置 ready（后台解析任务已随进程丢失）
    3. 注册定时任务（邮箱抓取 + 预分类补跑）
    """
    routes.restore_resume_storage()
    routes.reset_task_statuses_after_restart()

    scheduler = create_scheduler()
    if scheduler:
        try:
            ids = register_jobs(
                scheduler,
                lambda: routes.fetch_emails_and_ingest(),
                lambda: routes.preclassify_pending(),
            )
            scheduler.start()
            logger.info(f"Scheduler started with jobs: {ids}")
        except Exception as e:
            logger.warning(f"Scheduler failed to start: {e}")
            scheduler = None

    yield

    if scheduler:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
    routes.shutdown_upload_executor()
    routes.shutdown_auto_screen_executor()


app = FastAPI(
    title="Resume Screener API",
    description="基于LLM的智能简历筛选系统 API",
    default_response_class=UTF8JSONResponse,
    lifespan=lifespan,
)

# 跨域支持（前端单独部署时需要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.SERVER_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 包含API路由
app.include_router(routes.router)

# 挂载内置静态前端页面（/ui），no-cache 保证前端改动即时可见
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/ui", NoCacheStaticFiles(directory=_STATIC_DIR, html=True), name="ui")


@app.middleware("http")
async def add_charset_middleware(request: Request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type and "charset" not in content_type.lower():
        if (
            content_type.startswith("text/")
            or content_type in ("application/json", "application/javascript")
        ):
            response.headers["content-type"] = content_type + "; charset=utf-8"
    return response


@app.get("/")
async def root():
    """根路径重定向到内置前端页面。"""
    if os.path.isdir(_STATIC_DIR):
        return RedirectResponse(url="/ui/")
    return {"message": "Welcome to the Resume Screener API"}
