"""
筛选规则与反馈日志管理器

参考 Codeex 简历筛选工作流的"人工纠正驱动自迭代"机制：
- HR 对 AI 的分类结果进行纠正（反馈日志持久化）
- AI 总结纠正规律，生成带版本号的筛选规则
- 规则注入下次筛选的分析 prompt，自动生效

所有 JSON 文件读写均使用线程锁 + 临时文件原子替换，防止并发损坏。
"""
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from app.core.llm_client import LLMClient
from app.models.classification import VALID_CLASSIFICATIONS
from config.config import settings


class InsufficientFeedbackError(Exception):
    """待总结的反馈条数不足时抛出。"""


class RulesManager:
    """反馈日志 + 筛选规则的持久化、总结与读取。"""

    FEEDBACK_SCHEMA_VERSION = 1
    RULES_SCHEMA_VERSION = 1
    DEFAULT_RULES_STRUCTURE = {
        "schema_version": 1,
        "version": 0,
        "updated_at": None,
        "active": True,
        "rules": [],
        "summary": "",
        "based_on_feedback_ids": [],
        "based_on_last_ts": None,
        "history": [],
    }

    def __init__(self, llm_client: LLMClient, rules_dir: Optional[str] = None,
                 max_feedback_entries: Optional[int] = None, max_rules: Optional[int] = None):
        self.llm_client = llm_client
        self.rules_dir = Path(rules_dir or settings.RULES_DIR)
        self.feedback_path = self.rules_dir / "feedback_log.json"
        self.rules_path = self.rules_dir / "screening_rules.json"
        self.max_feedback_entries = max_feedback_entries or settings.RULES_MAX_FEEDBACK_ENTRIES
        self.max_rules = max_rules or settings.RULES_MAX_RULES
        self._lock = threading.Lock()
        logger.info(f"Initialized RulesManager with directory: {self.rules_dir}")

    # ------------------------------------------------------------------
    # 反馈日志
    # ------------------------------------------------------------------

    def add_feedback(self, entry: Dict[str, Any]) -> str:
        """追加一条人工纠正反馈，返回 feedback_id。

        Args:
            entry: 至少包含 resume_id / query_id / human_classification；
                human_classification 必须是 interview|review|reject。
        """
        human_cls = entry.get("human_classification", "")
        if human_cls not in VALID_CLASSIFICATIONS:
            raise ValueError(
                f"human_classification 必须是 {sorted(VALID_CLASSIFICATIONS)} 之一，"
                f"得到: {human_cls!r}"
            )

        feedback_id = str(uuid.uuid4())
        record = {
            "feedback_id": feedback_id,
            "resume_id": entry.get("resume_id", ""),
            "query_id": entry.get("query_id", ""),
            "candidate_name": entry.get("candidate_name", ""),
            "ai_classification": entry.get("ai_classification", ""),
            "ai_reason": entry.get("ai_reason", ""),
            "overall_score": entry.get("overall_score"),
            "human_classification": human_cls,
            "human_reason": entry.get("human_reason", ""),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

        with self._lock:
            data = self._load_json(self.feedback_path, self._empty_feedback())
            data["entries"].append(record)
            data["total_count"] += 1
            # 超容量裁剪：只保留最新 N 条，但 total_count 继续累计
            if len(data["entries"]) > self.max_feedback_entries:
                data["entries"] = data["entries"][-self.max_feedback_entries:]
                logger.info(
                    f"Feedback log trimmed to {self.max_feedback_entries} entries "
                    f"(total_count={data['total_count']})"
                )
            self._save_json(self.feedback_path, data)

        logger.info(f"Feedback recorded: {feedback_id} "
                    f"(resume={record['resume_id']}, human={human_cls})")
        return feedback_id

    def list_feedback(self, limit: int = 100) -> List[Dict[str, Any]]:
        """按时间倒序返回反馈条目（最新在前）。"""
        data = self._load_json(self.feedback_path, self._empty_feedback())
        entries = list(reversed(data.get("entries", [])))
        return entries[:limit]

    def get_feedback_map(self, query_id: str) -> Dict[str, Dict[str, Any]]:
        """返回 {resume_id: feedback_entry}，用于结果展示时以人工分类覆盖 AI 分类。

        注意：纠正针对的是候选人（resume_id），而非某次查询——因此
        匹配时不以 query_id 过滤，跨查询生效（用户改判后，任何筛选都显示纠正结果）。
        """
        return self.get_feedback_map_for_resumes()

    def get_feedback_map_for_resumes(self) -> Dict[str, Dict[str, Any]]:
        """{resume_id: 该候选人最新一条反馈}，跨查询生效。"""
        data = self._load_json(self.feedback_path, self._empty_feedback())
        result: Dict[str, Dict[str, Any]] = {}
        for e in data.get("entries", []):
            rid = e.get("resume_id")
            if rid:
                result[rid] = e  # 后写入的覆盖前面的 → 保留最新
        return result

    def feedback_total(self) -> int:
        """累计反馈条数（含被裁剪的历史）。"""
        data = self._load_json(self.feedback_path, self._empty_feedback())
        return data.get("total_count", 0)

    # ------------------------------------------------------------------
    # 规则读取
    # ------------------------------------------------------------------

    def get_active_rules(self) -> Dict[str, Any]:
        """读取当前生效规则；文件不存在或损坏时返回默认空结构。"""
        return self._load_json(self.rules_path, dict(self.DEFAULT_RULES_STRUCTURE))

    def active_rules_text(self) -> str:
        """生成注入 analyzer prompt 的规则文本段；无生效规则时返回空字符串。"""
        data = self.get_active_rules()
        return self.rules_text_of(data.get("rules") or [])

    @staticmethod
    def rules_text_of(rules: List[str]) -> str:
        """将规则列表渲染为注入文本：空列表 → ""，否则 "- 规则1\\n- 规则2"。"""
        if not rules:
            return ""
        return "\n".join(f"- {r}" for r in rules)

    def get_previous_rules(self) -> Dict[str, Any]:
        """上一版本规则（用于对比验证基线）。

        当前版本 V 时，从 history 中找 version 最大且 < V 的条目；
        V=0 或找不到 → 返回 {"version": 0, "rules": [], "summary": ""}（无规则基线）。
        """
        data = self.get_active_rules()
        current_version = data.get("version") or 0
        if current_version <= 0:
            return {"version": 0, "rules": [], "summary": ""}

        history = data.get("history") or []
        candidates = [h for h in history if (h.get("version") or 0) < current_version]
        if not candidates:
            return {"version": 0, "rules": [], "summary": ""}
        prev = max(candidates, key=lambda h: h.get("version") or 0)
        return {
            "version": prev.get("version") or 0,
            "rules": prev.get("rules") or [],
            "summary": prev.get("summary", ""),
        }

    def set_rules(self, rules: List[str], summary: str = "") -> Dict[str, Any]:
        """人工编辑规则：直接保存新的规则列表（版本 +1，旧版本压入 history）。

        与 summarize_rules（LLM 总结）并存：人工修改后版本号递增，
        且不改变 based_on_feedback_ids（已消费的反馈不因人工编辑而重新待总结）。

        Args:
            rules: 规则列表（自动去空、去重）
            summary: 规则摘要说明（可空）

        Returns:
            更新后的规则字典
        """
        cleaned = []
        seen = set()
        for r in rules:
            s = str(r).strip()
            if s and s not in seen:
                cleaned.append(s)
                seen.add(s)

        with self._lock:
            rules_data = self.get_active_rules()
            history = list(rules_data.get("history") or [])
            history.append({
                "version": rules_data.get("version") or 0,
                "updated_at": rules_data.get("updated_at"),
                "rules": rules_data.get("rules") or [],
            })
            history = history[-10:]

            new_rules_data = {
                "schema_version": self.RULES_SCHEMA_VERSION,
                "version": (rules_data.get("version") or 0) + 1,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "active": True,
                "rules": cleaned[: self.max_rules],
                "summary": (summary or "").strip(),
                # 人工编辑不消费/不改变反馈状态：沿用原有已消费反馈 id
                "based_on_feedback_ids": rules_data.get("based_on_feedback_ids") or [],
                "based_on_last_ts": rules_data.get("based_on_last_ts"),
                "history": history,
            }
            self._save_json(self.rules_path, new_rules_data)

        logger.info(f"Rules manually set to version {new_rules_data['version']} "
                    f"({len(cleaned)} rules)")
        return new_rules_data

    def pending_feedback_count(self) -> int:
        """自上次规则总结以来新增的反馈条数。

        基于 based_on_feedback_ids 判断（不依赖时间戳，避免同秒精度问题）。
        """
        data = self._load_json(self.feedback_path, self._empty_feedback())
        consumed = self._consumed_feedback_ids()
        if not consumed:
            return len(data.get("entries", []))
        return sum(
            1 for e in data.get("entries", [])
            if e.get("feedback_id") not in consumed
        )

    def _consumed_feedback_ids(self) -> set:
        """已被最近一次规则总结消费的反馈 id 集合。"""
        data = self.get_active_rules()
        return set(data.get("based_on_feedback_ids") or [])

    # ------------------------------------------------------------------
    # 规则总结（LLM）
    # ------------------------------------------------------------------

    def summarize_rules(self, min_feedback: int | None = None) -> Dict[str, Any]:
        """用 LLM 总结待处理反馈的纠正规律，生成新版筛选规则。

        Returns:
            新版规则字典 {version, rules, summary, based_on_feedback_ids, ...}

        Raises:
            InsufficientFeedbackError: 待总结反馈不足
            ValueError: LLM 输出无法解析为合法规则 JSON
        """
        min_count = min_feedback if min_feedback is not None else settings.RULES_MIN_FEEDBACK_FOR_SUMMARIZE

        with self._lock:
            feedback_data = self._load_json(self.feedback_path, self._empty_feedback())
            rules_data = self.get_active_rules()

            consumed = self._consumed_feedback_ids()
            pending = [
                e for e in feedback_data.get("entries", [])
                if e.get("feedback_id") not in consumed
            ]

            if len(pending) < min_count:
                raise InsufficientFeedbackError(
                    f"待总结反馈不足：当前 {len(pending)} 条，需要至少 {min_count} 条"
                )

            prompt = self._generate_rules_prompt(pending, min_count)
            raw = self.llm_client.generate_text(prompt)
            parsed = self._parse_rules_response(raw)

            rules = [str(r).strip() for r in parsed.get("rules", []) if str(r).strip()]
            if not rules:
                raise ValueError(f"规则总结输出缺少 rules: {raw[:200]}")

            new_version = (rules_data.get("version") or 0) + 1
            now = datetime.now().isoformat(timespec="seconds")

            # 旧版本压入 history（上限 10 条）
            history = list(rules_data.get("history") or [])
            history.append({
                "version": rules_data.get("version") or 0,
                "updated_at": rules_data.get("updated_at"),
                "rules": rules_data.get("rules") or [],
            })
            history = history[-10:]

            new_rules_data = {
                "schema_version": self.RULES_SCHEMA_VERSION,
                "version": new_version,
                "updated_at": now,
                "active": True,
                "rules": rules[: self.max_rules],
                "summary": str(parsed.get("summary", "")).strip(),
                "based_on_feedback_ids": [e["feedback_id"] for e in pending],
                "based_on_last_ts": max(
                    e.get("created_at", "") for e in pending
                ) if pending else now,
                "history": history,
            }
            self._save_json(self.rules_path, new_rules_data)

        logger.info(f"Rules summarized to version {new_version} "
                    f"based on {len(pending)} feedback entries")
        return new_rules_data

    # ------------------------------------------------------------------
    # LLM 提示词与解析
    # ------------------------------------------------------------------

    def _generate_rules_prompt(self, pending: List[Dict[str, Any]], min_feedback: int) -> str:
        """构造规则总结提示词：输入 AI vs 人工判定不一致的反馈，输出规律规则。"""
        lines = []
        for i, e in enumerate(pending, 1):
            lines.append(
                f"{i}. 候选人「{e.get('candidate_name', '未知')}」："
                f"AI 判定={e.get('ai_classification', '未知')}"
                f"（理由: {e.get('ai_reason', '无')}），"
                f"综合得分={e.get('overall_score')}；"
                f"HR 纠正为={e.get('human_classification')}"
                f"（原因: {e.get('human_reason', '无')}）"
            )
        feedback_text = "\n".join(lines)

        prompt = f"""
你是资深招聘专家。以下是 HR 对 AI 简历筛选结果的人工纠正记录：
（AI 判定与 HR 判定的差异，反映 AI 筛选标准的偏差）

{feedback_text}

请分析这些纠正记录，总结出【AI 筛选时容易犯的错误规律】，提炼为不超过
{self.max_rules} 条可执行的筛选规则。规则必须：
- 具体、可操作，能指导下一次筛选（例如："不看关键词罗列，只看候选人是否独立负责过真实项目并有真实用户/客户"）
- 概括共性问题，而不是针对单个候选人的描述
- 用中文表达

请严格按照以下JSON格式返回结果，不要包含其他文本：
{{
  "rules": ["规则1", "规则2"],
  "summary": "对纠正规律的一句话总结"
}}

只返回JSON，不要包含其他解释文本。
"""
        return prompt

    def _parse_rules_response(self, response: str) -> Dict[str, Any]:
        """解析规则总结响应，三级兜底：整段JSON -> 剥代码围栏 -> 截首{尾}。"""
        candidates = [
            response,
            re.sub(r"```(?:json)?\s*|\s*```", "", response),
        ]
        start, end = response.find("{"), response.rfind("}")
        if start != -1 and end != -1 and start < end:
            candidates.append(response[start:end + 1])

        for text in candidates:
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
        raise ValueError(f"Failed to parse rules response as JSON: {response[:200]}")

    # ------------------------------------------------------------------
    # 持久化基础
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_feedback() -> Dict[str, Any]:
        return {"schema_version": RulesManager.FEEDBACK_SCHEMA_VERSION, "total_count": 0, "entries": []}

    def _load_json(self, path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        """读取 JSON 文件；不存在返回 default；损坏时备份后返回 default。"""
        if not path.exists():
            return dict(default)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else dict(default)
        except (json.JSONDecodeError, OSError) as e:
            backup = path.with_name(f"{path.name}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")
            try:
                os.replace(path, backup)
                logger.warning(f"Corrupted JSON file backed up: {path} -> {backup} ({e})")
            except OSError:
                logger.warning(f"Failed to backup corrupted file {path}: {e}")
            return dict(default)

    def _save_json(self, path: Path, data: Dict[str, Any]) -> None:
        """原子写入 JSON：临时文件 + os.replace。调用方需持有 _lock。"""
        self.rules_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
