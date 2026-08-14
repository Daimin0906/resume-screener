"""
全流程自动筛选 Agent

职责：
- 默认岗位要求（JD）读写（data/default_query.txt）
- 新简历状态管理（data/auto_screen_state.json，processed_ids 集合）
- 自动筛选执行（run_screening_cb 回调，由 routes 注入完整管线）
- 结果持久化（data/auto_screen_results.json，保留最近 N 次）

设计要点：
- 防重入：threading.Lock 非阻塞获取（正在运行时跳过，未处理简历留待下轮自愈）
- 原子写：临时文件 + os.replace（与 RulesManager 一致）
- 新简历判定用 processed_ids 而非时间戳：异步解析乱序完成也安全、失败自动重试
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from app.core.query_parser import QueryParser

# processed_ids 集合上限（防止无限增长）
MAX_PROCESSED_IDS = 5000

# 运行状态枚举
STATUS_COMPLETED = "completed"
STATUS_SKIPPED_NO_QUERY = "skipped_no_query"
STATUS_SKIPPED_NO_NEW = "skipped_no_new"
STATUS_SKIPPED_RUNNING = "skipped_running"
STATUS_FAILED = "failed"


class AutoScreener:
    """全流程自动筛选：默认岗位 + 状态 + 执行 + 结果持久化。"""

    def __init__(self, data_dir: str, query_parser: QueryParser,
                 run_screening_cb: Callable[[Any, List[str]], Dict[str, Any]],
                 rules_version_cb: Callable[[], int],
                 max_runs: int = 20, max_batch: int = 50):
        self.data_dir = Path(data_dir)
        self.query_path = self.data_dir / "default_query.txt"
        self.state_path = self.data_dir / "auto_screen_state.json"
        self.results_path = self.data_dir / "auto_screen_results.json"
        self.query_parser = query_parser
        # routes 注入：接收 (QueryMetadata, resume_ids) 返回 ScreeningResult 同构 dict
        self.run_screening_cb = run_screening_cb
        self.rules_version_cb = rules_version_cb
        self.max_runs = max_runs
        self.max_batch = max_batch
        # RLock：run() 持锁期间会调用 mark_processed/prune/_append_run（内部同样加锁）
        self._lock = threading.RLock()
        self._running = False
        logger.info(f"Initialized AutoScreener with data dir: {self.data_dir}")

    # ------------------------------------------------------------------
    # 默认岗位要求
    # ------------------------------------------------------------------

    def get_default_query(self) -> Dict[str, Any]:
        """读取默认岗位要求。"""
        if not self.query_path.exists():
            return {"query_text": "", "updated_at": None}
        try:
            text = self.query_path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning(f"Failed to read default query: {e}")
            return {"query_text": "", "updated_at": None}
        return {
            "query_text": text,
            "updated_at": datetime.fromtimestamp(self.query_path.stat().st_mtime)
            .isoformat(timespec="seconds") if text else None,
        }

    def set_default_query(self, text: str) -> Dict[str, Any]:
        """保存默认岗位要求（原子写）。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("默认岗位要求不能为空")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.query_path.with_suffix(".txt.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.query_path)
        result = self.get_default_query()
        logger.info(f"Default query saved ({len(text)} chars)")
        return result

    # ------------------------------------------------------------------
    # 状态管理
    # ------------------------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {"schema_version": 1, "processed_ids": [], "last_fetch_at": None, "last_run_at": None}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Corrupted auto screen state, resetting: {e}")
            return {"schema_version": 1, "processed_ids": [], "last_fetch_at": None, "last_run_at": None}

    def _save_state(self, data: Dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _processed_ids(self) -> set:
        return set(self._load_state().get("processed_ids", []))

    def is_processed(self, resume_id: str) -> bool:
        return resume_id in self._processed_ids()

    def mark_processed(self, resume_ids: List[str]) -> None:
        with self._lock:
            data = self._load_state()
            ids = set(data.get("processed_ids", []))
            ids.update(resume_ids)
            # 截断上限：保留最近的（按插入顺序近似）
            if len(ids) > MAX_PROCESSED_IDS:
                ids = set(list(ids)[-MAX_PROCESSED_IDS:])
            data["processed_ids"] = list(ids)
            data["last_run_at"] = datetime.now().isoformat(timespec="seconds")
            self._save_state(data)

    def prune_processed_ids(self, valid_ids: set) -> None:
        """清理已不存在的简历 id（简历被删除后）。"""
        with self._lock:
            data = self._load_state()
            ids = set(data.get("processed_ids", []))
            pruned = ids - valid_ids
            if pruned:
                data["processed_ids"] = list(ids & valid_ids)
                self._save_state(data)
                logger.info(f"Pruned {len(pruned)} processed ids (resumes deleted)")

    def record_fetch(self) -> None:
        with self._lock:
            data = self._load_state()
            data["last_fetch_at"] = datetime.now().isoformat(timespec="seconds")
            self._save_state(data)

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def run(self, trigger: str,
            ready_resume_ids: Callable[[], List[str]]) -> Dict[str, Any]:
        """执行一轮自动筛选。

        Args:
            trigger: "after_fetch"（抓取后自动）| "manual"（手动触发）| "manual_screen"（手动筛选）
            ready_resume_ids: 返回待筛选简历 id 列表的回调（按来源过滤由调用方负责）

        Returns:
            本次 run 记录 dict（含 status）
        """
        # 防重入：正在运行时跳过（未处理简历状态已持久化，下轮自动补）
        if not self._lock.acquire(blocking=False):
            logger.info("[auto-screen] skipped: already running")
            return {"status": STATUS_SKIPPED_RUNNING}

        self._running = True
        run_id = str(uuid.uuid4())
        run_record: Dict[str, Any] = {
            "run_id": run_id,
            "trigger": trigger,
            "status": STATUS_FAILED,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "query_text": "",
            "rules_version_used": 0,
            "screened_count": 0,
            "distributions": {},
            "candidates": [],
            "error": None,
        }

        try:
            # 1. 默认岗位要求
            query = self.get_default_query()
            query_text = query.get("query_text", "")
            if not query_text:
                run_record["status"] = STATUS_SKIPPED_NO_QUERY
                run_record["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self._append_run(run_record)
                logger.info("[auto-screen] skipped: no default query configured")
                return run_record

            # 2. 解析查询（复用截断/占位词兜底）
            query_metadata = self.query_parser.parse_query(query_text)

            # 3. 待筛选的新简历（已就绪 且 未处理过；来源过滤由调用方在回调中完成）
            all_ready = ready_resume_ids()
            pending = [rid for rid in all_ready if not self.is_processed(rid)]

            # 清理已删除简历的 processed 记录
            self.prune_processed_ids(set(all_ready))

            if not pending:
                run_record["status"] = STATUS_SKIPPED_NO_NEW
                run_record["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self._append_run(run_record)
                logger.info("[auto-screen] skipped: no new resumes")
                return run_record

            # 4. 批次上限
            batch = pending[: self.max_batch]
            if len(pending) > self.max_batch:
                logger.info(f"[auto-screen] batch capped: {len(pending)} pending, "
                            f"screening {len(batch)}, rest next round")

            # 5. 执行完整筛选（routes 注入的回调：score/rank/analyze/format/feedback 覆盖）
            logger.info(f"[auto-screen] screening {len(batch)} new resumes")
            payload = self.run_screening_cb(query_metadata, batch)

            # 6. 统计三分类分布
            distributions: Dict[str, int] = {}
            for c in payload.get("candidates", []):
                cls = c.get("classification", "review")
                distributions[cls] = distributions.get(cls, 0) + 1

            # 7. 标记已处理
            self.mark_processed(batch)

            run_record.update({
                "status": STATUS_COMPLETED,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "query_text": query_text,
                "rules_version_used": self.rules_version_cb(),
                "screened_count": len(batch),
                "distributions": distributions,
                "candidates": payload.get("candidates", []),
            })
            self._append_run(run_record)
            logger.info(f"[auto-screen] completed: {len(batch)} resumes, "
                        f"distributions={distributions}")
            return run_record

        except Exception as e:
            logger.exception("[auto-screen] run failed")
            run_record["status"] = STATUS_FAILED
            run_record["finished_at"] = datetime.now().isoformat(timespec="seconds")
            run_record["error"] = str(e)
            self._append_run(run_record)
            return run_record

        finally:
            self._running = False
            self._lock.release()

    # ------------------------------------------------------------------
    # 结果/状态
    # ------------------------------------------------------------------

    def _load_results(self) -> Dict[str, Any]:
        if not self.results_path.exists():
            return {"schema_version": 1, "runs": []}
        try:
            data = json.loads(self.results_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"schema_version": 1, "runs": []}
        except (json.JSONDecodeError, OSError):
            return {"schema_version": 1, "runs": []}

    def _append_run(self, run_record: Dict[str, Any]) -> None:
        with self._lock:
            data = self._load_results()
            # skipped_running 不追加（避免运行中重复触发刷屏）
            if run_record.get("status") != STATUS_SKIPPED_RUNNING:
                runs = data.get("runs", [])
                runs.append(run_record)
                data["runs"] = runs[-self.max_runs:]
                self._save_results(data)

    def _save_results(self, data: Dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.results_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.results_path)

    def list_runs(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """最新在前。"""
        runs = list(reversed(self._load_results().get("runs", [])))
        if limit:
            runs = runs[:limit]
        return runs

    def latest_run(self) -> Optional[Dict[str, Any]]:
        runs = self.list_runs(limit=1)
        return runs[0] if runs else None

    def get_status(self) -> Dict[str, Any]:
        """面板状态摘要（不含候选人明细）。"""
        latest = self.latest_run()
        summary = None
        if latest:
            summary = {
                k: latest.get(k) for k in (
                    "run_id", "trigger", "status", "started_at", "finished_at",
                    "query_text", "rules_version_used", "screened_count",
                    "distributions", "error",
                )
            }
        state = self._load_state()
        return {
            "enabled": True,  # 由 routes 层按 settings 覆盖
            "running": self._running,
            "default_query_set": bool(self.get_default_query().get("query_text")),
            "last_fetch_at": state.get("last_fetch_at"),
            "last_run": summary,
        }
