from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime


class UploadResumeResponse(BaseModel):
    """上传简历响应模型"""
    resume_id: str
    message: str
    # 解析状态：parsing（后台解析中）/ ready（完成）
    status: str = "parsing"
    # 文本质量警告（如扫描件 PDF 提取质量差）
    warning: Optional[str] = None


class QueryRequest(BaseModel):
    """筛选查询请求模型"""
    query_text: str


class QueryResponse(BaseModel):
    """筛选查询响应模型"""
    query_id: str
    message: str


class Skill(BaseModel):
    """技能模型"""
    name: str
    score: float


class WorkExperience(BaseModel):
    """工作经历模型"""
    company: Optional[str] = None
    title: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None


class Education(BaseModel):
    """教育背景模型"""
    institution: Optional[str] = None
    major: Optional[str] = None
    degree: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class Candidate(BaseModel):
    """候选人模型"""
    id: str
    rank: int
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    overall_score: float = 0.0
    skill_scores: List[Skill] = []
    work_experience: List[WorkExperience] = []
    education: List[Education] = []
    skills: List[str] = []
    expected_salary: Optional[str] = None
    preferred_locations: List[str] = []
    analysis: str = ""
    # ---- 三分类（interview / review / reject）----
    classification: str = "review"
    classification_reason: str = ""
    # llm（LLM判定） | heuristic（分数兜底） | human（人工纠正覆盖）
    classification_source: str = "llm"
    # 6 维评估：skill_match/experience_match/education_match/ownership/real_users/quantified_results
    assessment: Dict[str, Any] = {}
    corrected_by_human: bool = False
    strengths: List[str] = []
    risks: List[str] = []


class ScreeningResult(BaseModel):
    """筛选结果模型"""
    query_id: str
    query_text: str
    total_candidates: int
    candidates: List[Candidate]
    created_at: datetime
    # 本次筛选使用的规则版本（0 = 无规则）
    rules_version_used: Optional[int] = None


class FeedbackRequest(BaseModel):
    """人工纠正反馈请求模型"""
    resume_id: str
    query_id: str
    candidate_name: Optional[str] = None
    ai_classification: Optional[str] = None
    ai_reason: Optional[str] = None
    human_classification: str
    human_reason: Optional[str] = None
    overall_score: Optional[float] = None


class FeedbackResponse(BaseModel):
    """反馈提交响应模型"""
    feedback_id: str
    message: str


class RulesResponse(BaseModel):
    """筛选规则查看响应模型"""
    version: int
    rules: List[str] = []
    summary: str = ""
    updated_at: Optional[datetime] = None
    pending_feedback_count: int = 0
    feedback_total: int = 0


class RulesSummarizeRequest(BaseModel):
    """规则总结请求模型"""
    min_feedback: Optional[int] = None


class RulesSummaryResponse(BaseModel):
    """规则总结响应模型"""
    version: int
    rules: List[str] = []
    summary: str = ""
    based_on_feedback_count: int = 0


class ResumeStatusResponse(BaseModel):
    """简历解析状态响应模型（前端轮询用）"""
    resume_id: str
    status: str  # parsing / ready / error
    error: Optional[str] = None
    warning: Optional[str] = None


class EmailFetchRequest(BaseModel):
    """邮箱抓取请求模型"""
    limit: int = 10


class EmailIngestedResume(BaseModel):
    """邮箱抓取入库的简历摘要"""
    resume_id: str
    filename: str
    status: str


class EmailFetchItem(BaseModel):
    """单封邮件的抓取结果"""
    email_id: str
    subject: str = ""
    sender: str = ""
    resumes: List[EmailIngestedResume] = []


class EmailFetchResponse(BaseModel):
    """邮箱抓取响应模型"""
    fetched: int
    results: List[EmailFetchItem] = []


class RulesCompareRequest(BaseModel):
    """规则版本对比请求模型（query_id 与 resume_ids 二选一）"""
    query_id: Optional[str] = None
    resume_ids: Optional[List[str]] = None


class CompareCandidateDelta(BaseModel):
    """单个候选人两版规则分类差异"""
    resume_id: str
    name: Optional[str] = None
    base_classification: str
    current_classification: str
    changed: bool


class RulesCompareResponse(BaseModel):
    """规则版本对比响应模型"""
    base_version: int
    current_version: int
    compared_count: int
    distributions: Dict[str, Dict[str, int]] = {}
    deltas: List[CompareCandidateDelta] = []
    changed_count: int = 0
    note: str = ""


class ErrorResponse(BaseModel):
    """错误响应模型"""
    error: str
    message: str