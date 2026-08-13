"""
API 路由
"""
import asyncio
import uuid
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool

from app.api.models import (
    UploadResumeResponse, QueryRequest, QueryResponse, ScreeningResult,
    FeedbackRequest, FeedbackResponse, RulesResponse,
    RulesSummarizeRequest, RulesSummaryResponse, ResumeStatusResponse,
    EmailFetchRequest, EmailFetchResponse, EmailFetchItem, EmailIngestedResume,
    RulesCompareRequest, RulesCompareResponse,
)
from app.core.cache_manager import CacheManager
from app.core.document_parser import DocumentParser
from app.core.extractor import MetadataExtractor
from app.core.llm_client import LLMClient
from app.core.query_parser import QueryParser
from app.core.rules_manager import RulesManager, InsufficientFeedbackError
from app.core.vector_store_factory import get_vector_store_manager
from app.core.retriever import Retriever
from app.core.filter import HardFilter
from app.core.scorer import Scorer
from app.core.ranker import Ranker
from app.core.analyzer import CandidateAnalyzer
from app.core.result_formatter import ResultFormatter
from app.models.metadata import ResumeMetadata, QueryMetadata
from app.models.classification import VALID_CLASSIFICATIONS
from app.core.metadata_utils import deserialize_metadata
from app.core.email_fetcher import EmailFetcher
from config.config import settings

router = APIRouter(prefix="/api/v1")

# 常量
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# 初始化核心组件
llm_client = LLMClient()
cache_manager = CacheManager()
document_parser = DocumentParser(cache_manager=cache_manager)
metadata_extractor = MetadataExtractor(llm_client, cache_manager=cache_manager)
query_parser = QueryParser(llm_client)
vector_store_manager = get_vector_store_manager()
retriever = Retriever(vector_store_manager)
hard_filter = HardFilter()
scorer = Scorer()
ranker = Ranker()
candidate_analyzer = CandidateAnalyzer(llm_client)
result_formatter = ResultFormatter()
rules_manager = RulesManager(llm_client)

# 存储简历和查询结果的内存字典（在实际应用中应使用数据库）
# 注意：内存 dict 在服务重启后清空；简历原始数据持久化在向量库（ChromaDB），
# 启动时通过 restore_resume_storage() 自动恢复。
resume_storage: Dict[str, Any] = {}
query_storage: Dict[str, Any] = {}

# 异步上传任务状态：resume_id -> {"status": "parsing"|"ready"|"error", "error": str|None}
# 仅内存态；重启后存量简历由 restore_resume_storage + reset_task_statuses_after_restart 置 ready
resume_tasks: Dict[str, Dict[str, Any]] = {}

# 后台解析线程池（上传/邮箱抓取共用）
upload_executor = ThreadPoolExecutor(max_workers=settings.UPLOAD_MAX_WORKERS)


def reset_task_statuses_after_restart() -> None:
    """服务重启后调用：已入库简历的任务状态统一置 ready（后台任务已随进程丢失）。"""
    for rid in resume_storage:
        resume_tasks[rid] = {"status": "ready", "error": None}


def shutdown_upload_executor() -> None:
    """服务关闭时调用：停止接受新任务，不等待进行中的任务。"""
    upload_executor.shutdown(wait=False)


def _process_resume_sync(resume_id: str, filename: str, content: bytes) -> None:
    """后台线程执行完整上传管线：解析→LLM抽取→存储→向量化→预分类。

    与 API 层共用模块级组件（document_parser/metadata_extractor/retriever/candidate_analyzer），
    供异步上传与邮箱抓取复用。状态转移：parsing -> ready / error。
    """
    try:
        if filename.lower().endswith('.pdf'):
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                resume_text = document_parser.parse_pdf(tmp_path)
            finally:
                os.remove(tmp_path)
        else:
            resume_text = content.decode('utf-8')

        # 统一过滤控制字符（PDF 提取的 \x00 等已在 document_parser 处理，txt 也可能混入）
        resume_text = _clean_control_chars(resume_text)

        metadata = metadata_extractor.extract_metadata(resume_text)

        resume_storage[resume_id] = {
            "id": resume_id,
            "filename": filename,
            "text": resume_text,
            "metadata": metadata.dict(),
            "created_at": datetime.now(),
        }
        retriever.add_resume(resume_id, resume_text, metadata.dict(), filename)
        logger.info(f"[upload] 简历已入库: {resume_id} ({filename})")

        # 入库即预分类（失败不影响入库结果）
        if settings.PRECLASSIFY_ON_INGEST:
            try:
                _preclassify_resume(resume_id)
            except Exception as e:
                logger.warning(f"[upload] 预分类失败 {resume_id}: {e}")

        resume_tasks[resume_id] = {"status": "ready", "error": None}
    except Exception as e:
        logger.exception(f"[upload] 简历解析失败: {resume_id}")
        resume_tasks[resume_id] = {"status": "error", "error": str(e)}


def restore_resume_storage() -> None:
    """服务启动时从向量库恢复内存简历索引（ChromaDB 持久化，内存 dict 重启即失）。"""
    try:
        data = vector_store_manager.get_all_documents("resumes")
        ids = data.get("ids") or []
        documents = data.get("documents") or []
        metadatas = data.get("metadatas") or []

        restored = 0
        for rid, text, raw_meta in zip(ids, documents, metadatas):
            meta = deserialize_metadata(raw_meta or {}) if raw_meta else {}
            filename = meta.pop("filename", None) or f"{meta.get('name', '未命名')}（已恢复）"
            # 预分类随元数据持久化，恢复时取回（内存与向量库双写）
            preclassification = meta.pop("preclassification", None) or None
            data = {
                "id": rid,
                "filename": filename,
                "text": text or "",
                "metadata": meta,
                "created_at": datetime.now(),
            }
            if preclassification:
                data["preclassification"] = preclassification
            resume_storage[rid] = data
            restored += 1

        if restored:
            logger.info(f"Restored {restored} resumes from vector store on startup")
    except Exception as e:
        logger.warning(f"Failed to restore resume storage from vector store: {e}")


# ------------------------------------------------------------------
# 入库即预分类（无岗位需求通用分类，仅列表/详情展示；查询筛选仍以查询分类为准）
# ------------------------------------------------------------------

def _preclassify_resume(resume_id: str) -> None:
    """对单份简历做一次通用评估分类，结果写 resume_storage["preclassification"]。

    预分类仅存内存（不持久化到向量元数据），重启后由定时任务 preclassify_pending 补跑。
    """
    try:
        data = resume_storage.get(resume_id)
        if not data or not data.get("text"):
            return
        result = candidate_analyzer.analyze_candidate(
            {"id": resume_id, "text": data.get("text", ""), "metadata": data.get("metadata", {})},
            None,
            rules_text=rules_manager.active_rules_text(),
        )
        preclassification = {
            "classification": result.get("classification", "review"),
            "reason": result.get("classification_reason", ""),
            "rule_version": rules_manager.get_active_rules().get("version") or 0,
            "source": result.get("classification_source", "llm"),
            "analyzed_at": datetime.now().isoformat(timespec="seconds"),
        }
        data["preclassification"] = preclassification
        # 持久化到向量库元数据：服务重启后 restore 时可恢复，不丢失
        retriever.update_resume_preclassification(resume_id, preclassification)
    except Exception as e:
        logger.warning(f"Preclassification failed for resume {resume_id}: {e}")


def preclassify_pending() -> Dict[str, int]:
    """批量补跑：对"无预分类 且 状态 ready"的简历做通用分类（定时任务/手动触发共用）。"""
    pending = [
        resume_storage[rid] for rid in sorted(resume_storage)
        if "preclassification" not in resume_storage[rid]
        and resume_tasks.get(rid, {}).get("status", "ready") == "ready"
    ]
    if not pending:
        return {"processed": 0}

    results = candidate_analyzer.analyze_candidates(
        pending, None, rules_text=rules_manager.active_rules_text())
    processed = 0
    for r in results:
        rid = r.get("id")
        if rid in resume_storage:
            preclassification = {
                "classification": r.get("classification", "review"),
                "reason": r.get("classification_reason", ""),
                "rule_version": rules_manager.get_active_rules().get("version") or 0,
                "source": r.get("classification_source", "llm"),
                "analyzed_at": datetime.now().isoformat(timespec="seconds"),
            }
            resume_storage[rid]["preclassification"] = preclassification
            # 与单份路径一致：持久化到向量库元数据（重启后可恢复）
            retriever.update_resume_preclassification(rid, preclassification)
            processed += 1
    logger.info(f"Preclassified {processed} resumes")
    return {"processed": processed}


def _clean_control_chars(text: str) -> str:
    """过滤控制字符（\x00-\x08、\x0b-\x1f），防止破坏 LLM 调用与 JSON 解析。"""
    import re
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")


def _safe_json_loads(value: Any, default: Any = None) -> Any:
    """安全解析 JSON 字符串；若已是目标类型则直接返回。"""
    if default is None:
        default = []
    if isinstance(value, (list, dict)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"JSON parse failed for: {value}")
        return default


def _run_screening_stages(query_metadata: QueryMetadata,
                          resume_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """筛选管线：retrieve→filter→score→rank（同步）。

    供 /results 与 /rules/compare 共用。resume_ids 非空时跳过检索/硬过滤
    （指定批次直接评分排序），用于规则对比只看分类分布。
    """
    if resume_ids:
        batch = [resume_storage[rid] for rid in resume_ids if rid in resume_storage]
        scored = scorer.score_resumes(batch, query_metadata)
        return ranker.rank_resumes(scored, query_metadata)

    retrieved = retriever.retrieve(query_metadata)
    filtered = hard_filter.filter_resumes(retrieved, query_metadata)
    scored = scorer.score_resumes(filtered, query_metadata)
    return ranker.rank_resumes(scored, query_metadata)


def _calc_skill_scores(resume_skills: list, query_metadata: QueryMetadata, overall_skill_score: float) -> list:
    """根据查询要求计算每个技能的单项得分。"""
    if not resume_skills:
        return []

    required = [s.lower() for s in query_metadata.required_skills]
    preferred = [s.lower() for s in query_metadata.preferred_skills]

    scores = []
    for skill in resume_skills:
        sl = str(skill).lower()
        matched = False
        for q in required + preferred:
            if q in sl or sl in q:
                matched = True
                break
        scores.append({
            "name": skill,
            "score": 1.0 if matched else max(overall_skill_score - 0.3, 0.0)
        })
    return scores


@router.get("/health")
async def health_check():
    """健康检查接口"""
    return {"status": "ok"}


@router.get("/resumes")
async def list_resumes():
    """列出已上传的简历（摘要信息）。"""
    # 按上传时间倒序：新上传的简历排在列表最前面
    sorted_items = sorted(
        resume_storage.items(),
        key=lambda kv: kv[1].get("created_at") or datetime.min,
        reverse=True,
    )
    items = []
    for rid, data in sorted_items:
        meta = data.get("metadata", {}) or {}
        items.append({
            "resume_id": rid,
            "filename": data.get("filename", ""),
            "name": meta.get("name", ""),
            "skills": meta.get("skills", []) or [],
            "status": resume_tasks.get(rid, {}).get("status", "ready"),
            "preclassification": data.get("preclassification"),
            "created_at": data.get("created_at"),
        })
    return {"total": len(items), "resumes": items}


@router.post("/resumes", response_model=UploadResumeResponse)
async def upload_resume(file: UploadFile = File(...)):
    """
    上传简历接口
    """
    logger.info(f"[upload_resume] 开始处理文件: {file.filename}")

    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"文件大小超过限制 {MAX_FILE_SIZE / 1024 / 1024:.0f}MB")

    resume_id = str(uuid.uuid4())
    logger.info(f"[upload_resume] 文件读取完成, 大小: {len(content)} bytes, resume_id: {resume_id}")

    # 先占位（异步模式下 text/metadata 由后台线程填充）
    resume_storage[resume_id] = {
        "id": resume_id,
        "filename": file.filename,
        "text": "",
        "metadata": {},
        "created_at": datetime.now()
    }
    resume_tasks[resume_id] = {"status": "parsing", "error": None}

    try:
        if settings.UPLOAD_ASYNC:
            # 异步模式：立即返回，后台线程池解析
            upload_executor.submit(_process_resume_sync, resume_id, file.filename, content)
            return UploadResumeResponse(
                resume_id=resume_id,
                status="parsing",
                message=f"简历 '{file.filename}' 已提交，正在后台解析…"
            )

        # 同步模式（测试默认）：完整管线执行完再返回
        _process_resume_sync(resume_id, file.filename, content)
        return UploadResumeResponse(
            resume_id=resume_id,
            status=resume_tasks[resume_id]["status"],
            message=f"简历 '{file.filename}' 上传成功"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("上传简历失败")
        resume_tasks[resume_id] = {"status": "error", "error": str(e)}
        raise HTTPException(status_code=500, detail="上传简历失败，请稍后重试")


@router.post("/queries", response_model=QueryResponse)
async def submit_query(query_request: QueryRequest):
    """
    提交筛选查询接口
    """
    try:
        query_metadata = await run_in_threadpool(query_parser.parse_query, query_request.query_text)
        query_id = str(uuid.uuid4())

        query_storage[query_id] = {
            "id": query_id,
            "text": query_request.query_text,
            "metadata": query_metadata.dict(),
            "created_at": datetime.now()
        }

        return QueryResponse(
            query_id=query_id,
            message="查询提交成功"
        )

    except Exception as e:
        logger.exception("提交查询失败")
        raise HTTPException(status_code=500, detail="提交查询失败，请稍后重试")


@router.get("/results/{query_id}", response_model=ScreeningResult)
async def get_screening_results(query_id: str):
    """
    获取筛选结果接口
    """
    if query_id not in query_storage:
        raise HTTPException(status_code=404, detail="查询不存在")

    try:
        query_data = query_storage[query_id]
        query_metadata = QueryMetadata(**query_data["metadata"])

        # 注入生效的筛选规则（来自 HR 反馈总结），无规则时为空字符串
        rules_text = rules_manager.active_rules_text()
        rules_info = rules_manager.get_active_rules()
        rules_version = rules_info.get("version") or 0

        # 结果缓存：键含规则版本（规则更新自动失效）；人工反馈提交时按 query 前缀失效
        cache_key = f"results:{query_id}:v{rules_version}"
        if settings.RESULTS_CACHE_ENABLED:
            cached = cache_manager.get(cache_key)
            if cached is not None:
                return cached

        ranked_resumes = await run_in_threadpool(_run_screening_stages, query_metadata)
        analyzed_candidates = await run_in_threadpool(
            candidate_analyzer.analyze_candidates, ranked_resumes, query_metadata, rules_text)
        formatted_results = await run_in_threadpool(result_formatter.format_results, analyzed_candidates, query_metadata)

        # 人工纠正反馈：该 query 下被纠正过的候选人，以人工分类覆盖 AI 分类显示
        feedback_map = rules_manager.get_feedback_map(query_id)

        candidates = []
        for candidate_data in formatted_results["candidates"]:
            basic_info = candidate_data.get("basic_info", {}) or {}
            scores = candidate_data.get("scores", {}) or {}
            overall_skill_score = scores.get("skill_score", 0)

            resume_skills = _safe_json_loads(basic_info.get("skills", []), [])
            skill_scores = _calc_skill_scores(resume_skills, query_metadata, overall_skill_score)

            work_experience = _safe_json_loads(basic_info.get("work_experience", []), [])
            education = _safe_json_loads(basic_info.get("education", []), [])

            candidate_id = candidate_data.get("id", "")
            feedback_entry = feedback_map.get(candidate_id)

            classification = candidate_data.get("classification", "review")
            classification_reason = candidate_data.get("classification_reason", "")
            classification_source = candidate_data.get("classification_source", "llm")
            corrected_by_human = False
            if feedback_entry:
                # 人工纠正过：以人工分类覆盖显示
                classification = feedback_entry.get("human_classification", classification)
                classification_reason = feedback_entry.get("human_reason", "") or classification_reason
                classification_source = "human"
                corrected_by_human = True

            candidate = {
                "id": candidate_id,
                "rank": candidate_data.get("rank", 0),
                "name": candidate_data.get("name", ""),
                "email": candidate_data.get("contact_info", {}).get("email"),
                "phone": candidate_data.get("contact_info", {}).get("phone"),
                "overall_score": scores.get("overall_score", 0),
                "work_experience": work_experience,
                "education": education,
                "skill_scores": skill_scores,
                "skills": resume_skills,
                "expected_salary": basic_info.get("expected_salary"),
                "preferred_locations": basic_info.get("preferred_locations", []),
                "analysis": candidate_data.get("analysis", ""),
                "classification": classification,
                "classification_reason": classification_reason,
                "classification_source": classification_source,
                "assessment": candidate_data.get("assessment", {}) or {},
                "corrected_by_human": corrected_by_human,
                "strengths": candidate_data.get("strengths", []) or [],
                "risks": candidate_data.get("risks", []) or [],
            }
            candidates.append(candidate)

        result = ScreeningResult(
            query_id=query_id,
            query_text=query_data["text"],
            total_candidates=formatted_results["total_candidates"],
            candidates=candidates,
            created_at=query_data["created_at"],
            rules_version_used=rules_version,
        )
        if settings.RESULTS_CACHE_ENABLED:
            cache_manager.set(cache_key, result.model_dump(), expire=settings.RESULTS_CACHE_TTL_SECONDS)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取筛选结果失败")
        raise HTTPException(status_code=500, detail="获取筛选结果失败，请稍后重试")


@router.get("/resumes/{resume_id}/status", response_model=ResumeStatusResponse)
async def get_resume_status(resume_id: str):
    """获取简历解析状态（前端轮询用）。"""
    if resume_id not in resume_storage:
        raise HTTPException(status_code=404, detail="简历不存在")
    task = resume_tasks.get(resume_id, {"status": "ready", "error": None})
    return ResumeStatusResponse(
        resume_id=resume_id,
        status=task.get("status", "ready"),
        error=task.get("error"),
    )


@router.get("/resumes/{resume_id}")
async def get_resume(resume_id: str):
    """
    获取简历详情接口
    """
    if resume_id not in resume_storage:
        raise HTTPException(status_code=404, detail="简历不存在")

    try:
        data = dict(resume_storage[resume_id])
        data["status"] = resume_tasks.get(resume_id, {}).get("status", "ready")
        return data
    except Exception:
        logger.exception("获取简历详情失败")
        raise HTTPException(status_code=500, detail="获取简历详情失败，请稍后重试")


@router.delete("/resumes/{resume_id}")
async def delete_resume(resume_id: str):
    """
    删除简历接口：从内存索引与向量库同时删除
    """
    if resume_id not in resume_storage:
        raise HTTPException(status_code=404, detail="简历不存在")

    try:
        del resume_storage[resume_id]
        await run_in_threadpool(vector_store_manager.delete_documents, "resumes", [resume_id])
        logger.info(f"Deleted resume {resume_id}")
        return {"message": "简历已删除"}
    except Exception:
        logger.exception("删除简历失败")
        raise HTTPException(status_code=500, detail="删除简历失败，请稍后重试")


# ------------------------------------------------------------------
# 邮箱抓取（IMAP）
# ------------------------------------------------------------------

def fetch_emails_and_ingest(limit: int = 10) -> Dict[str, Any]:
    """抓取招聘邮箱未读简历并入库存档（手动 API 与定时任务共用入口）。

    流程：抓取未读附件 → 每份简历提交后台解析 → 一封邮件全部提交成功后标记已读
    （失败留未读，下轮重试，防止丢简历）。
    """
    if not settings.IMAP_ENABLED or not settings.IMAP_HOST:
        raise ValueError("IMAP 未配置（IMAP_ENABLED/IMAP_HOST）")

    fetcher = EmailFetcher(
        host=settings.IMAP_HOST,
        port=settings.IMAP_PORT,
        ssl=settings.IMAP_SSL,
        user=settings.IMAP_USER,
        password=settings.IMAP_PASSWORD,
        mailbox=settings.IMAP_MAILBOX,
        mark_read=settings.IMAP_MARK_READ,
        max_attachment_bytes=settings.IMAP_ATTACHMENT_MAX_MB * 1024 * 1024,
    )

    emails = fetcher.fetch_new(limit=limit)
    results = []
    for mail in emails:
        ingested = []
        for att in mail.get("attachments", []):
            resume_id = str(uuid.uuid4())
            resume_storage[resume_id] = {
                "id": resume_id,
                "filename": att["filename"],
                "text": "",
                "metadata": {},
                "created_at": datetime.now(),
            }
            resume_tasks[resume_id] = {"status": "parsing", "error": None}
            upload_executor.submit(_process_resume_sync, resume_id, att["filename"], att["content_bytes"])
            ingested.append({
                "resume_id": resume_id,
                "filename": att["filename"],
                "status": "parsing",
            })
        results.append({
            "email_id": mail["email_id"],
            "subject": mail.get("subject", ""),
            "sender": mail.get("sender", ""),
            "resumes": ingested,
        })

        # 一封邮件全部附件提交成功后才标记已读
        if settings.IMAP_MARK_READ and ingested:
            fetcher.mark_read([mail["email_id"].encode("utf-8")])

    logger.info(f"Email fetch: {len(results)} emails, "
                f"{sum(len(r['resumes']) for r in results)} resumes ingested")
    return {"fetched": len(results), "results": results}


@router.post("/emails/fetch", response_model=EmailFetchResponse)
async def fetch_emails(request: EmailFetchRequest):
    """手动触发邮箱抓取：抓取未读简历附件并入库（解析在后台进行）。"""
    if not settings.IMAP_ENABLED or not settings.IMAP_HOST:
        raise HTTPException(status_code=400, detail="IMAP 未配置，请先在 .env 中设置 IMAP_ENABLED/IMAP_HOST/IMAP_USER/IMAP_PASSWORD")

    try:
        data = await run_in_threadpool(fetch_emails_and_ingest, request.limit)
        return EmailFetchResponse(fetched=data["fetched"], results=data["results"])
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("邮箱抓取失败")
        raise HTTPException(status_code=500, detail=f"邮箱抓取失败: {e}")


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(feedback_request: FeedbackRequest):
    """
    提交人工纠正反馈接口

    HR 对 AI 的分类判定进行纠正并说明原因，反馈将用于后续总结筛选规则。
    """
    if feedback_request.human_classification not in VALID_CLASSIFICATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"human_classification 必须是 {sorted(VALID_CLASSIFICATIONS)} 之一"
        )

    try:
        entry = {
            "resume_id": feedback_request.resume_id,
            "query_id": feedback_request.query_id,
            "candidate_name": feedback_request.candidate_name or "",
            "ai_classification": feedback_request.ai_classification or "",
            "ai_reason": feedback_request.ai_reason or "",
            "overall_score": feedback_request.overall_score,
            "human_classification": feedback_request.human_classification,
            "human_reason": feedback_request.human_reason or "",
        }
        feedback_id = await run_in_threadpool(rules_manager.add_feedback, entry)
        # 人工反馈会改变结果展示，失效该 query 的结果缓存
        cache_manager.delete_prefix(f"results:{feedback_request.query_id}:")
        return FeedbackResponse(feedback_id=feedback_id, message="反馈提交成功")
    except Exception as e:
        logger.exception("提交反馈失败")
        raise HTTPException(status_code=500, detail=f"提交反馈失败，请稍后重试: {e}")


@router.get("/feedback")
async def list_feedback(limit: int = 100):
    """查询反馈日志（最新在前），用于审计与调试。"""
    entries = await run_in_threadpool(rules_manager.list_feedback, limit)
    total = await run_in_threadpool(rules_manager.feedback_total)
    return {"total": total, "entries": entries}


@router.post("/preclassify")
async def preclassify_all():
    """手动触发预分类补跑：对无预分类的已就绪简历做通用分类（定时任务每小时也会跑）。"""
    try:
        result = await run_in_threadpool(preclassify_pending)
        return result
    except Exception as e:
        logger.exception("预分类补跑失败")
        raise HTTPException(status_code=500, detail=f"预分类补跑失败: {e}")


@router.get("/rules", response_model=RulesResponse)
async def get_rules():
    """
    获取当前生效的筛选规则与待总结反馈数量

    pending_feedback_count > 0 表示有新的人工纠正待总结为规则。
    """
    rules_data = await run_in_threadpool(rules_manager.get_active_rules)
    pending = await run_in_threadpool(rules_manager.pending_feedback_count)
    total = await run_in_threadpool(rules_manager.feedback_total)
    return RulesResponse(
        version=rules_data.get("version") or 0,
        rules=rules_data.get("rules") or [],
        summary=rules_data.get("summary") or "",
        updated_at=rules_data.get("updated_at"),
        pending_feedback_count=pending,
        feedback_total=total,
    )


@router.post("/rules/compare", response_model=RulesCompareResponse)
async def compare_rules(request: RulesCompareRequest):
    """
    规则版本对比：同一批候选人分别用「上一版本」与「当前版本」规则跑分类，
    输出两版三分类分布差异。

    注意：对比需对同一批候选人跑两轮完整分析，LLM 调用数为 2×N，可能耗时较长。
    """
    if bool(request.query_id) == bool(request.resume_ids):
        raise HTTPException(status_code=400, detail="query_id 与 resume_ids 必须二选一")

    try:
        if request.query_id:
            if request.query_id not in query_storage:
                raise HTTPException(status_code=404, detail="查询不存在")
            query_metadata = QueryMetadata(**query_storage[request.query_id]["metadata"])
        else:
            query_metadata = QueryMetadata()

        # 限制对比规模
        resume_ids = request.resume_ids
        if resume_ids and len(resume_ids) > settings.RULES_COMPARE_MAX_RESUMES:
            resume_ids = resume_ids[: settings.RULES_COMPARE_MAX_RESUMES]

        ranked = await run_in_threadpool(_run_screening_stages, query_metadata, resume_ids)
        if not ranked:
            return RulesCompareResponse(
                base_version=0, current_version=0, compared_count=0,
                distributions={}, deltas=[], changed_count=0,
                note="没有可对比的候选人（批次为空或全部被过滤）",
            )

        base = rules_manager.get_previous_rules()
        current = rules_manager.get_active_rules()
        base_version = base.get("version") or 0
        current_version = current.get("version") or 0

        # 两轮分析：上一版本规则 vs 当前版本规则
        base_analyzed = await run_in_threadpool(
            candidate_analyzer.analyze_candidates,
            ranked, query_metadata, rules_manager.rules_text_of(base.get("rules") or []))
        current_analyzed = await run_in_threadpool(
            candidate_analyzer.analyze_candidates,
            ranked, query_metadata, rules_manager.active_rules_text())

        # 按 resume_id 对齐
        current_by_id = {c.get("id"): c for c in current_analyzed}
        distributions = {"base": {"interview": 0, "review": 0, "reject": 0},
                         "current": {"interview": 0, "review": 0, "reject": 0}}
        deltas = []
        for c in base_analyzed:
            rid = c.get("id")
            curr = current_by_id.get(rid, {})
            base_cls = c.get("classification", "review")
            curr_cls = curr.get("classification", "review")
            distributions["base"][base_cls] = distributions["base"].get(base_cls, 0) + 1
            distributions["current"][curr_cls] = distributions["current"].get(curr_cls, 0) + 1
            changed = base_cls != curr_cls
            if changed or True:
                deltas.append({
                    "resume_id": rid,
                    "name": c.get("metadata", {}).get("name") or curr.get("metadata", {}).get("name"),
                    "base_classification": base_cls,
                    "current_classification": curr_cls,
                    "changed": changed,
                })

        changed_count = sum(1 for d in deltas if d["changed"])
        return RulesCompareResponse(
            base_version=base_version,
            current_version=current_version,
            compared_count=len(deltas),
            distributions=distributions,
            deltas=deltas,
            changed_count=changed_count,
            note=f"对比基于 {len(deltas)} 位候选人、两轮完整分析（LLM 调用 {len(deltas) * 2} 次），"
                 f"耗时可能较长。",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("规则对比失败")
        raise HTTPException(status_code=500, detail=f"规则对比失败: {e}")


@router.post("/rules/summarize", response_model=RulesSummaryResponse)
async def summarize_rules(request: RulesSummarizeRequest):
    """
    用 LLM 总结人工纠正反馈的规律，生成新版筛选规则

    反馈不足时返回 400；LLM 输出无法解析时返回 502（版本不变，可重试）。
    """
    try:
        new_rules = await run_in_threadpool(rules_manager.summarize_rules, request.min_feedback)
        return RulesSummaryResponse(
            version=new_rules.get("version") or 0,
            rules=new_rules.get("rules") or [],
            summary=new_rules.get("summary") or "",
            based_on_feedback_count=len(new_rules.get("based_on_feedback_ids") or []),
        )
    except InsufficientFeedbackError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("规则总结失败")
        raise HTTPException(status_code=502, detail=f"规则总结失败，请稍后重试: {e}")
