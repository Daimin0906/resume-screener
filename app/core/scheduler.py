"""
定时任务（APScheduler 封装）

- 延迟导入 apscheduler：未安装或 SCHEDULER_ENABLED=false 时返回 None 并告警，
  保证依赖缺失或测试环境下应用仍可正常运行。
- 任务在 FastAPI lifespan 启动时注册（内存 job store），重启后从配置重建，
  无需持久化 job store。
- 单进程单 worker 部署；多 worker 场景应在 README 注明仅一个实例开启
  SCHEDULER_ENABLED（或未来加分布式锁）。
"""
from typing import Any, Callable, List, Optional

from loguru import logger

from config.config import settings


def create_scheduler():
    """创建后台调度器；未安装依赖或配置禁用时返回 None。"""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("apscheduler 未安装，定时任务禁用")
        return None

    if not settings.SCHEDULER_ENABLED:
        logger.info("SCHEDULER_ENABLED=false，定时任务禁用")
        return None

    return BackgroundScheduler(timezone="Asia/Shanghai")


def register_jobs(scheduler: Any,
                  job_fetch_emails: Callable[[], Any],
                  job_preclassify_pending: Callable[[], Any]) -> List[str]:
    """注册定时任务：邮箱抓取 + 预分类补跑。返回注册的 job id 列表。

    Args:
        scheduler: create_scheduler() 返回的 BackgroundScheduler
        job_fetch_emails: 邮箱抓取任务（routes.fetch_emails_and_ingest 包装，避免循环导入）
        job_preclassify_pending: 预分类补跑任务（routes.preclassify_pending 包装）
    """
    from apscheduler.triggers.interval import IntervalTrigger

    ids = []
    if settings.IMAP_ENABLED:
        scheduler.add_job(
            job_fetch_emails,
            IntervalTrigger(minutes=settings.SCHEDULER_EMAIL_FETCH_INTERVAL_MINUTES),
            id="email_fetch",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        ids.append("email_fetch")

    scheduler.add_job(
        job_preclassify_pending,
        IntervalTrigger(minutes=settings.SCHEDULER_PRECLASSIFY_INTERVAL_MINUTES),
        id="preclassify",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    ids.append("preclassify")
    return ids
