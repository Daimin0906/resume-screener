"""
候选人处理工作台（对齐 Codeex 邮箱标签流程）

- 处理状态跟随候选人（resume_id）：pending（待处理）/ interview（约面试）
  / review（待核实）/ archived（归档淘汰）
- 聚合：从自动筛选结果（auto_screen_results.json）收集全部候选人，
  按 resume_id 去重（保留最新分类），合并处理状态
- 导出：值得面试候选人名单 CSV
"""
import csv
import io
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

# 处理状态枚举
STATUS_PENDING = "pending"
STATUS_INTERVIEW = "interview"
STATUS_REVIEW = "review"
STATUS_ARCHIVED = "archived"

VALID_STATUSES = {STATUS_PENDING, STATUS_INTERVIEW, STATUS_REVIEW, STATUS_ARCHIVED}

# 聚合时最多回溯的筛选次数
MAX_AGGREGATE_RUNS = 50


class Workbench:
    """候选人处理工作台：状态管理 + 聚合 + 导出。"""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.status_path = self.data_dir / "candidate_status.json"
        self._lock = threading.RLock()
        logger.info(f"Initialized Workbench with data dir: {self.data_dir}")

    # ------------------------------------------------------------------
    # 处理状态
    # ------------------------------------------------------------------

    def _load_status(self) -> Dict[str, Any]:
        if not self.status_path.exists():
            return {"schema_version": 1, "candidates": {}}
        try:
            data = json.loads(self.status_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"schema_version": 1, "candidates": {}}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Corrupted candidate status, resetting: {e}")
            return {"schema_version": 1, "candidates": {}}

    def _save_status(self, data: Dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.status_path)

    def set_status(self, resume_id: str, status: str) -> Dict[str, Any]:
        """设置候选人处理状态。"""
        if status not in VALID_STATUSES:
            raise ValueError(f"status 必须是 {sorted(VALID_STATUSES)} 之一，得到: {status!r}")
        with self._lock:
            data = self._load_status()
            candidates = data.setdefault("candidates", {})
            candidates[resume_id] = {
                "status": status,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._save_status(data)
        logger.info(f"Workbench: {resume_id} -> {status}")
        return candidates[resume_id]

    def get_status(self, resume_id: str) -> str:
        data = self._load_status()
        return data.get("candidates", {}).get(resume_id, {}).get("status", STATUS_PENDING)

    def status_map(self) -> Dict[str, str]:
        """{resume_id: status}。"""
        data = self._load_status()
        return {
            rid: info.get("status", STATUS_PENDING)
            for rid, info in data.get("candidates", {}).items()
        }

    # ------------------------------------------------------------------
    # 聚合
    # ------------------------------------------------------------------

    def aggregate(self, results_runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """聚合候选人：从历次自动筛选结果收集，按 resume_id 去重（保留最新）。

        Args:
            results_runs: auto_screen_results.json 的 runs（最新在前）

        Returns:
            [{"resume_id", "name", "classification", "classification_reason",
              "overall_score", "skills", "analysis", "work_status",
              "screened_at", "rules_version_used"}]
            按分类排序（interview > review > pending 分类）再按分数降序
        """
        by_resume: Dict[str, Dict[str, Any]] = {}
        for run in results_runs:
            for c in run.get("candidates", []):
                rid = c.get("id")
                if not rid:
                    continue
                by_resume[rid] = {
                    "resume_id": rid,
                    "name": c.get("name") or "",
                    "source": c.get("source", "manual"),
                    "classification": c.get("classification", "review"),
                    "classification_reason": c.get("classification_reason", ""),
                    "overall_score": c.get("overall_score", 0) or 0,
                    "skills": c.get("skills", []) or [],
                    "analysis": c.get("analysis", "") or "",
                    "corrected_by_human": c.get("corrected_by_human", False),
                    "screened_at": run.get("finished_at") or "",
                    "rules_version_used": run.get("rules_version_used") or 0,
                }

        statuses = self.status_map()
        order = {"interview": 0, "review": 1, "reject": 2}
        candidates = []
        for rid, c in by_resume.items():
            c["work_status"] = statuses.get(rid, STATUS_PENDING)
            candidates.append(c)
        candidates.sort(
            key=lambda x: (order.get(x["classification"], 3), -x["overall_score"])
        )
        return candidates

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------

    def export_interview_csv(self, candidates: List[Dict[str, Any]]) -> str:
        """导出「值得面试」候选人名单 CSV（interview 分类 或 已标记约面试）。"""
        selected = [
            c for c in candidates
            if c.get("classification") == "interview" or c.get("work_status") == STATUS_INTERVIEW
        ]
        if not selected:
            return ""

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["姓名", "分类", "综合得分", "技能", "期望薪资", "期望地点", "判定理由", "分析摘要"])
        for c in selected:
            reason = (c.get("classification_reason") or "").replace("\n", " ")[:80]
            analysis = (c.get("analysis") or "").replace("\n", " ")[:120]
            writer.writerow([
                c.get("name") or "",
                c.get("classification") or "",
                f"{c.get('overall_score', 0):.2f}",
                "、".join(c.get("skills") or [])[:100],
                "",
                "",
                reason,
                analysis,
            ])
        return output.getvalue()
