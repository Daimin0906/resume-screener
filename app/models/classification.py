"""
候选人三分类模型

分类由 LLM 判定为主（analyzer），人工可通过反馈接口纠正。
"""
from enum import Enum
from typing import Dict


class CandidateCategory(str, Enum):
    """候选人分类（三分类）"""
    INTERVIEW = "interview"  # 值得面试
    REVIEW = "review"        # HR审核
    REJECT = "reject"        # 直接淘汰


# 合法分类值集合（用于校验）
VALID_CLASSIFICATIONS = {c.value for c in CandidateCategory}

# 中文标签
CATEGORY_LABELS: Dict[str, str] = {
    CandidateCategory.INTERVIEW.value: "值得面试",
    CandidateCategory.REVIEW.value: "HR审核",
    CandidateCategory.REJECT.value: "直接淘汰",
}

# 前端徽章配色（复用 style.css 的 --ok/--warn/--err 语义）
CATEGORY_COLORS: Dict[str, str] = {
    CandidateCategory.INTERVIEW.value: "ok",   # 绿
    CandidateCategory.REVIEW.value: "warn",    # 橙
    CandidateCategory.REJECT.value: "err",     # 红
}

# LLM 输出无法解析时，按综合得分启发式判定的阈值
# overall_score >= 0.75 -> interview；>= 0.5 -> review；否则 reject
HEURISTIC_INTERVIEW_THRESHOLD = 0.75
HEURISTIC_REVIEW_THRESHOLD = 0.5
