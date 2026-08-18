from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
from app.core.llm_client import LLMClient
from app.models.metadata import ResumeMetadata, QueryMetadata
from app.models.classification import (
    VALID_CLASSIFICATIONS,
    HEURISTIC_INTERVIEW_THRESHOLD,
    HEURISTIC_REVIEW_THRESHOLD,
)
from config.config import settings
from loguru import logger
import json
import re


class _AnalysisCancelled(Exception):
    """内部信号：用户请求停止筛选，中断批量分析（由 analyze_candidates 捕获）。"""


class CandidateAnalyzer:
    """
    候选人分析器，用于生成候选人综合评价与三分类判定

    单次 LLM 调用同时产出：
    - classification: interview（值得面试）/ review（HR审核）/ reject（直接淘汰）
    - classification_reason: 判定理由
    - assessment: 6 维评估（含独立负责/真实用户/可量化结果三个质量维度）
    - recommendation: 综合报告（Markdown，兼容旧前端 analysis 展示）
    """

    def __init__(self, llm_client: LLMClient):
        """
        初始化候选人分析器

        Args:
            llm_client (LLMClient): LLM客户端实例
        """
        self.llm_client = llm_client
        logger.info("Initialized CandidateAnalyzer")

    def analyze_candidate(self, resume: Dict[str, Any],
                          query_metadata: Optional[QueryMetadata] = None,
                          rules_text: str = "") -> Dict[str, Any]:
        """
        生成候选人的综合评价与三分类判定

        Args:
            resume (Dict[str, Any]): 简历数据
            query_metadata (Optional[QueryMetadata]): 查询元数据；
                为 None 时进入通用评估模式（无岗位需求，按当前规则 + 通用标准分类，用于入库预分类）
            rules_text (str): 生效的筛选规则文本（来自 HR 反馈总结），为空则不注入

        Returns:
            Dict[str, Any]: 包含综合评价与分类的候选人数据
        """
        try:
            # 检查resume是否为字典类型
            if not isinstance(resume, dict):
                logger.warning(f"Skipping non-dict resume in analysis: {type(resume)}")
                # 创建一个包含错误信息的候选人数据
                candidate = {}
                candidate["id"] = "unknown"
                candidate["analysis"] = "分析失败：简历格式无效"
                candidate["classification"] = "review"
                candidate["classification_reason"] = "简历格式无效，无法分析"
                candidate["classification_source"] = "heuristic"
                candidate["assessment"] = {}
                candidate["strengths"] = []
                candidate["risks"] = []
                return candidate

            # 通用评估模式（无岗位需求）：query_metadata 为 None
            generic = query_metadata is None
            effective_qm = query_metadata if query_metadata is not None else QueryMetadata()

            # 构造分析提示词
            prompt = self._create_analysis_prompt(resume, effective_qm, rules_text, generic=generic)

            # 使用LLM生成分析结果
            raw_response = self.llm_client.generate_text(prompt)

            # 解析结构化分类结果（多级兜底）
            parsed = self._parse_classification_response(raw_response)
            classification = parsed.get("classification", "")

            candidate = resume.copy()

            if classification in VALID_CLASSIFICATIONS:
                candidate["analysis"] = parsed.get("recommendation") or raw_response
                candidate["classification"] = classification
                candidate["classification_reason"] = parsed.get("classification_reason", "")
                candidate["classification_source"] = "llm"
                candidate["assessment"] = parsed.get("dimension_scores", {}) or {}
                candidate["strengths"] = parsed.get("strengths", []) or []
                candidate["risks"] = parsed.get("risks", []) or []
            else:
                # LLM 未按 JSON 返回（旧格式自由文本），保留原文并走启发式兜底
                candidate["analysis"] = raw_response
                heuristic_cls = self._heuristic_classification(resume)
                candidate["classification"] = heuristic_cls
                candidate["classification_reason"] = (
                    "（LLM输出无法解析，按综合得分启发式判定）"
                )
                candidate["classification_source"] = "heuristic"
                candidate["assessment"] = {}
                candidate["strengths"] = []
                candidate["risks"] = []
                logger.warning(
                    f"Candidate {resume.get('id', 'unknown')}: LLM output not JSON, "
                    f"fallback to heuristic classification={heuristic_cls}"
                )

            logger.info(f"Analyzed candidate: {resume.get('id', 'unknown')} "
                        f"-> {candidate['classification']}")
            return candidate

        except Exception as e:
            logger.error(f"Failed to analyze candidate: {e}")
            raise

    def analyze_candidates(self, resumes: List[Dict[str, Any]],
                           query_metadata: Optional[QueryMetadata] = None,
                           rules_text: str = "",
                           cancel_check=None) -> List[Dict[str, Any]]:
        """
        批量生成候选人的综合评价与三分类判定

        Args:
            resumes (List[Dict[str, Any]]): 简历列表
            query_metadata (Optional[QueryMetadata]): 查询元数据；None 为通用评估模式
            rules_text (str): 生效的筛选规则文本，为空则不注入
            cancel_check (Callable[[], bool], optional): 取消回调；每份简历分析前
                调用，返回 True 时提前中止（用户点了"停止筛选"）

        Returns:
            List[Dict[str, Any]]: 包含综合评价与分类的候选人列表；
                被取消时返回空列表（调用方据此判断中止）
        """
        def _analyze_safe(resume: Dict[str, Any]) -> Dict[str, Any]:
            """逐人分析 + 兜底（原串行逻辑原样搬入，pool.map 保序）"""
            if cancel_check and cancel_check():
                # 用户点了停止：抛出特殊异常中断 pool.map
                raise _AnalysisCancelled()
            try:
                return self.analyze_candidate(resume, query_metadata, rules_text)
            except Exception as e:
                logger.error(f"Failed to analyze candidate {resume.get('id', 'unknown')}: {e}")
                # 即使某个候选人分析失败，也继续处理其他候选人
                fallback = resume.copy()
                fallback["analysis"] = "分析失败"
                fallback["classification"] = "review"
                fallback["classification_reason"] = "分析失败，待人工核实"
                fallback["classification_source"] = "heuristic"
                fallback["assessment"] = {}
                fallback["strengths"] = []
                fallback["risks"] = []
                return fallback

        try:
            workers = max(1, settings.ANALYZER_MAX_WORKERS)
            if len(resumes) <= 1 or workers <= 1:
                return [_analyze_safe(r) for r in resumes]

            # 并发分析：submit + as_completed，支持取消——
            # 每 0.5 秒检查一次取消标志，取消后立即返回已完成的（可能为空）
            from concurrent.futures import as_completed
            pool = ThreadPoolExecutor(max_workers=workers)
            try:
                futures = [pool.submit(_analyze_safe, r) for r in resumes]
                results = []
                while futures:
                    if cancel_check and cancel_check():
                        logger.info("Analysis cancelled by user request")
                        break
                    try:
                        for fut in as_completed(futures, timeout=0.5):
                            futures.remove(fut)
                            try:
                                results.append(fut.result())
                            except _AnalysisCancelled:
                                logger.info("Analysis cancelled by user request")
                                futures = []
                                break
                            except Exception:
                                continue
                    except TimeoutError:
                        continue  # 超时：回到循环顶部再查取消标志
                return results
            finally:
                # 不等待未完成的任务（取消时立即释放，未跑的任务直接丢弃）
                pool.shutdown(wait=False, cancel_futures=True)
        except _AnalysisCancelled:
            logger.info("Analysis cancelled by user request")
            return []

    # ------------------------------------------------------------------
    # 解析与兜底
    # ------------------------------------------------------------------

    def _parse_classification_response(self, response: str) -> Dict[str, Any]:
        """解析 LLM 分类响应，三级兜底：整段JSON -> 剥代码围栏 -> 截首{尾}。

        Returns:
            解析出的字典；全部失败返回空字典（由调用方走启发式兜底）。
        """
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
        return {}

    @staticmethod
    def _heuristic_classification(resume: Dict[str, Any]) -> str:
        """LLM 输出无法解析时，按综合得分启发式判定分类。"""
        score = resume.get("scores", {}).get("overall_score", 0) or 0
        if score >= HEURISTIC_INTERVIEW_THRESHOLD:
            return "interview"
        if score >= HEURISTIC_REVIEW_THRESHOLD:
            return "review"
        return "reject"

    # ------------------------------------------------------------------
    # 提示词构造
    # ------------------------------------------------------------------

    def _create_analysis_prompt(self, resume: Dict[str, Any], query_metadata: QueryMetadata,
                                rules_text: str = "", generic: bool = False) -> str:
        """
        创建候选人分析提示词

        强化三个质量维度（参考 Codeex 简历筛选最佳实践）：
        - 独立负责度：候选人是否独立负责/主导过项目，而非仅"参与/协助"
        - 真实用户/客户：是否有真实用户、上线运行、服务规模
        - 可量化结果：是否有可量化成果（百分比/数字），而非只有形容词
        判定必须以简历中的实际证据为依据，而不是只看关键词是否命中。

        Args:
            generic (bool): True 为通用评估模式（无岗位需求，入库预分类用）：
                职位要求段替换为通用判定标准，其余段落与普通模式完全一致。
        """
        # 提取简历关键信息
        metadata = resume.get("metadata", {})
        # 检查metadata是否为字典类型
        if not isinstance(metadata, dict):
            logger.warning(f"Invalid metadata type in resume: {type(metadata)}")
            metadata = {}

        name = metadata.get("name", "未知")
        skills = metadata.get("skills", [])
        work_experience = metadata.get("work_experience", [])
        education = metadata.get("education", [])
        projects = metadata.get("projects", [])
        summary = metadata.get("summary", "")

        # 提取查询关键信息
        required_skills = query_metadata.required_skills
        preferred_skills = query_metadata.preferred_skills
        min_experience_years = query_metadata.min_experience_years
        required_education = query_metadata.required_education

        # 现有评分体系的综合得分（仅作参考，不作为判定依据）
        overall_score = resume.get("scores", {}).get("overall_score", 0) or 0

        # 职位要求段：普通模式为查询条件；通用模式（无岗位需求）为通用判定标准
        if generic:
            job_requirement_section = (
                "无特定岗位需求（通用评估模式）。\n"
                "判定标准：interview=候选人独立负责/主导过项目，且有真实用户/上线运行与可量化结果，项目经验扎实；\n"
                "review=部分满足或证据不足，需人工核实；reject=仅关键词堆砌、无项目实绩或明显不符。"
            )
        else:
            job_requirement_section = (
                f"1. 必需技能: {', '.join(required_skills) if required_skills else '无'}\n"
                f"2. 优先技能: {', '.join(preferred_skills) if preferred_skills else '无'}\n"
                f"3. 最少经验年限: {min_experience_years if min_experience_years else '无要求'}\n"
                f"4. 学历要求: {required_education if required_education else '无要求'}"
            )

        # 通用评估参考段：简历入库时做过一次无岗位素质评估，
        # 注入作为参考（AI 应以岗位匹配为准，但通用评估可佐证候选人素质）
        preclassify_section = ""
        pre = resume.get("preclassification")
        if pre and isinstance(pre, dict) and pre.get("classification"):
            pre_label = {
                "interview": "值得面试", "review": "HR审核", "reject": "直接淘汰",
            }.get(pre.get("classification"), pre.get("classification"))
            preclassify_section = f"""
## 通用评估参考（简历入库时的素质评估，仅供参考，请以岗位匹配度为准）
- 通用评估分类: {pre_label}
- 判定理由: {pre.get('reason', '')}
"""

        # 筛选规则注入段（来自 HR 历史纠正反馈，判定时必须优先遵守）
        rules_section = ""
        if rules_text:
            rules_section = f"""
## 筛选规则（来自HR历史纠正反馈，判定时必须优先遵守）
{rules_text}
"""

        prompt = f"""
你是一个专业的HR顾问，请根据以下简历信息和职位要求，对候选人进行综合评价，
并给出三分类判定。判定必须以简历中的"实际证据"为依据，而不是只看关键词是否命中。

候选人姓名: {name}

简历信息:
1. 技能: {', '.join(skills) if skills else '无'}
2. 工作经历:
   {self._format_work_experience(work_experience)}
3. 项目经历:
   {self._format_projects(projects)}
4. 教育背景:
   {self._format_education(education)}
5. 个人简介: {summary if summary else '无'}

职位要求:
{job_requirement_section}
现有评分体系综合得分（仅作参考，不作为判定依据）: {overall_score:.2f}

评估维度（逐项核查证据，每个维度给出 0~1 的得分）:
1. 技能匹配度 skill_match: 候选人的技能与职位要求的匹配程度
2. 经验匹配度 experience_match: 候选人的工作经验与职位要求的匹配程度
3. 教育背景匹配度 education_match: 候选人的教育背景与职位要求的匹配程度
4. 独立负责度 ownership: 候选人是否【独立负责/主导/全权负责】过项目、模块或业务线，
   而非仅"参与"或"协助"；依据：工作经历与项目描述中的主语与职责表述
5. 真实用户/客户 real_users: 项目是否有【真实用户/客户】、是否上线运行、服务规模
   （如服务N个客户、月活N万、覆盖N家门店）；仅有课程设计/练习项目不得视为真实
6. 可量化结果 quantified_results: 是否有【可量化成果】（降低XX%、提升XX%、节省成本N、
   增长N倍）；只有形容词没有数字不视为可量化

注意: 不要因为关键词命中率高就给高分，也不要因为没有命中关键词就判淘汰——
必须以工作经历/项目描述中的实际责任与结果为准。
{rules_section}
{preclassify_section}
分类标准（必须严格对照"职位要求"逐条核查，宁严勿松）:
- interview（值得面试）: 必须【同时满足】以下三点——①技能与岗位要求高度匹配（核心技能直接对应岗位职责）；
  ②有与岗位同领域的实际项目/工作经验（非泛泛的"参与过项目"）；③证据扎实（独立负责+真实用户+可量化结果）。
  若简历经验领域与岗位明显不同（如岗位要求 AI/LLM/Agent，简历只有传统后端/运维/测试经验），即使项目再漂亮也不能给 interview。
- review（HR审核）: 部分满足但证据不足、领域部分相关、或个别硬性条件（学历/年限/技能）存疑，需要人工核实。
- reject（直接淘汰）: 明显不满足岗位要求（领域不符、核心技能缺失、经验年限严重不足、无相关项目证据），
  或仅有关键词堆砌、无实际项目证据。

判定前请先做一次"领域匹配自检"：
1. 列出岗位的核心技术栈（如 Python/JavaScript、LLM、RAG、Agent、Prompt Engineering、Function Calling）；
2. 对照简历技能/项目经历，逐一标记：直接相关 / 部分相关 / 完全不相关；
3. 若核心技能【多数不相关】或【完全没有 AI/LLM/Agent 相关经历】，直接判 reject；
   若【部分相关但证据不足】，判 review；只有【核心技能与项目经历都直接对应岗位】才可判 interview。
注意：三分类不应集中在同一档，请按简历实际质量拉开差距。宁可多判 reject/review，也不要让不合格的简历进入面试名单。

请严格按照以下JSON格式返回结果，不要包含其他文本：
{{
  "classification": "interview | review | reject",
  "classification_reason": "一句话判定依据（中文）",
  "dimension_scores": {{
    "skill_match": 0.8,
    "experience_match": 0.7,
    "education_match": 0.9,
    "ownership": 0.9,
    "real_users": 0.6,
    "quantified_results": 0.7
  }},
  "strengths": ["优势1", "优势2"],
  "risks": ["风险1"],
  "recommendation": "中文综合报告（Markdown格式，包含技能/经验/教育匹配度、综合优势、潜在风险、面试建议）"
}}

只返回JSON，不要包含其他解释文本。
"""
        return prompt

    def _format_work_experience(self, work_experience: List[Dict[str, Any]]) -> str:
        """
        格式化工作经历

        Args:
            work_experience (List[Dict[str, Any]]): 工作经历列表

        Returns:
            str: 格式化后的工作经历
        """
        # 检查work_experience是否为列表类型
        if not isinstance(work_experience, list):
            logger.warning(f"Invalid work_experience type: {type(work_experience)}")
            return "工作经历格式无效"

        if not work_experience:
            return "无工作经历"

        formatted = []
        for exp in work_experience:
            # 检查每个经历是否为字典类型
            if not isinstance(exp, dict):
                logger.warning(f"Invalid work experience entry type: {type(exp)}")
                formatted.append("   - 无效的工作经历条目")
                continue

            company = exp.get("company", "未知公司")
            title = exp.get("title", "未知职位")
            start_date = exp.get("start_date", "未知开始时间")
            end_date = exp.get("end_date", "未知结束时间")
            description = exp.get("description", "")

            exp_str = f"   - {company} ({title}) {start_date} - {end_date}"
            if description:
                exp_str += f"\n     工作描述: {description}"
            formatted.append(exp_str)

        return "\n".join(formatted)

    def _format_projects(self, projects: List[Dict[str, Any]]) -> str:
        """
        格式化项目经历（判断独立负责/真实用户/可量化结果的关键证据）

        Args:
            projects (List[Dict[str, Any]]): 项目经历列表

        Returns:
            str: 格式化后的项目经历
        """
        if not isinstance(projects, list) or not projects:
            return "无项目经历"

        formatted = []
        for proj in projects:
            if not isinstance(proj, dict):
                formatted.append("   - 无效的项目条目")
                continue

            name = proj.get("name", "未知项目")
            period = proj.get("period", "")
            description = proj.get("description", "")

            proj_str = f"   - {name} {period}"
            if description:
                proj_str += f"\n     项目描述: {description}"
            formatted.append(proj_str)

        return "\n".join(formatted)

    def _format_education(self, education: List[Dict[str, Any]]) -> str:
        """
        格式化教育背景

        Args:
            education (List[Dict[str, Any]]): 教育背景列表

        Returns:
            str: 格式化后的教育背景
        """
        # 检查education是否为列表类型
        if not isinstance(education, list):
            logger.warning(f"Invalid education type: {type(education)}")
            return "教育背景格式无效"

        if not education:
            return "无教育背景"

        formatted = []
        for edu in education:
            # 检查每个教育背景是否为字典类型
            if not isinstance(edu, dict):
                logger.warning(f"Invalid education entry type: {type(edu)}")
                formatted.append("   - 无效的教育背景条目")
                continue

            institution = edu.get("institution", "未知院校")
            major = edu.get("major", "未知专业")
            degree = edu.get("degree", "未知学位")
            start_date = edu.get("start_date", "未知开始时间")
            end_date = edu.get("end_date", "未知结束时间")

            edu_str = f"   - {institution} ({major}) {degree} {start_date} - {end_date}"
            formatted.append(edu_str)

        return "\n".join(formatted)
