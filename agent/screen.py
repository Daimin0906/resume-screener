"""
轻量简历智能体 - 硬指标筛选器（纯确定性规则，无 LLM）

规则（全部可配置，稳定可复现）：
- 技能：required_skills 命中数 ≥ 阈值（默认 60%）
- 经验：min_experience_years（从"X年经验"/工作经历日期估算）
- 学历：min_education 等级（大专<本科<硕士<博士）
- 地点：locations 任一命中（子串匹配，如"北京"命中"北京市"）
- 性别/年龄：简历有标注时可配置（可选）

输出：{resume, passed, passed_reasons[], failed_reasons[]}
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

EDUCATION_LEVELS = {"大专": 1, "本科": 2, "硕士": 3, "博士": 4}

# 常见技能词（用于从全文识别候选人技能；可在 config 覆盖）
DEFAULT_SKILL_WORDS = [
    "python", "java", "go", "c++", "javascript", "typescript", "php", "ruby",
    "fastapi", "django", "flask", "spring", "springboot", "spring boot", "vue", "react",
    "mysql", "postgresql", "mongodb", "redis", "kafka", "elasticsearch", "sql",
    "docker", "kubernetes", "k8s", "aws", "linux", "git",
    "rag", "llm", "langchain", "agent", "机器学习", "深度学习",
]


@dataclass
class ExtractedResume:
    """从简历文本提取的确定性信息"""
    text: str
    name: str = ""
    phone: str = ""
    email: str = ""
    education: str = ""                 # 最高学历（大专/本科/硕士/博士）
    experience_years: float = 0.0       # 估算经验年限
    skills: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    gender: str = ""


class ScreenConfig:
    """硬指标筛选规则配置"""

    def __init__(self, cfg: dict):
        c = cfg.get("screen", {}) if isinstance(cfg, dict) else {}
        self.required_skills: List[str] = [s.lower() for s in c.get("required_skills", [])]
        self.skill_hit_ratio: float = float(c.get("skill_hit_ratio", 0.6))
        self.min_experience_years: float = float(c.get("min_experience_years", 0))
        self.min_education: str = c.get("min_education", "")
        self.locations: List[str] = c.get("locations", [])
        self.required_gender: str = c.get("required_gender", "")
        self.min_age: int = int(c.get("min_age", 0) or 0)
        self.max_age: int = int(c.get("max_age", 0) or 0)
        self.skill_words: List[str] = [w.lower() for w in c.get("skill_words", DEFAULT_SKILL_WORDS)]


def extract_resume(text: str) -> ExtractedResume:
    """从原始文本提取结构化信息（确定性正则/关键词，无 LLM）。"""
    r = ExtractedResume(text=text)
    low = text.lower()

    # 姓名：优先"姓名："格式，否则取首行（去掉联系方式行）
    m = re.search(r"姓名[:：]\s*([一-龥]{2,4})", text)
    if m:
        r.name = m.group(1)
    else:
        first_line = text.strip().split("\n")[0].strip()
        if re.fullmatch(r"[一-龥]{2,4}", first_line):
            r.name = first_line

    # 电话 / 邮箱
    m = re.search(r"1[3-9]\d{9}", text)
    if m:
        r.phone = m.group(0)
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    if m:
        r.email = m.group(0)

    # 性别
    if "男" in text[:200] and "女" not in text[:200]:
        r.gender = "男"
    elif "女" in text[:200] and "男" not in text[:200]:
        r.gender = "女"

    # 学历：取最高等级
    max_level, r.education = 0, ""
    for edu, level in EDUCATION_LEVELS.items():
        if edu in text and level > max_level:
            max_level, r.education = level, edu

    # 经验年限：优先 "X年经验"（"5年经验"/"5年工作经验"/"5年 Python 后端经验" 均可）
    for m in re.finditer(r"(\d{1,2})\s*年[^，。;\n]{0,10}?经验", text):
        r.experience_years = max(r.experience_years, float(m.group(1)))
    if not r.experience_years:
        # 从「工作经历」区域估算日期跨度（教育背景不算经验）
        work_zone = _extract_work_zone(text)
        total = 0.0
        for sm, em in re.findall(r"(\d{4})\s*[.\-/年]\s*\d{1,2}\s*[-–至到]\s*(\d{4}|至今|现在|今)", work_zone):
            end = 2026 if em in ("至今", "现在", "今") else int(em)
            years = max(end - int(sm), 0)
            if years <= 20:
                total += years
        if 0 < total <= 40:
            r.experience_years = total

    # 技能：全文子串匹配技能词表
    for word in DEFAULT_SKILL_WORDS:
        if word in low and word not in r.skills:
            r.skills.append(word)
    # 地点：期望地点关键词
    for city in ["北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安"]:
        if city in text and city not in r.locations:
            r.locations.append(city)

    return r


def _extract_work_zone(text: str) -> str:
    """截取「工作经历」区域（到 项目经历/教育背景 之前），用于经验年限估算。"""
    start = -1
    for marker in ("工作经历", "工作经验", "工作履历", "工作背景"):
        idx = text.find(marker)
        if idx != -1 and (start == -1 or idx < start):
            start = idx
    if start == -1:
        return text
    zone = text[start:]
    for end_marker in ("项目经历", "教育背景", "教育经历", "个人简介"):
        idx = zone.find(end_marker)
        if idx != -1:
            zone = zone[:idx]
            break
    return zone


def _fuzzy_match(target: str, candidates: List[str]) -> bool:
    """大小写不敏感双向子串匹配（"北京"命中"北京市"）。"""
    t = target.lower().strip()
    if not t:
        return True
    return any(t in c.lower() or c.lower() in t for c in candidates)


class HardScreener:
    """硬指标筛选器：逐条规则判定，输出通过/淘汰 + 原因。"""

    def __init__(self, config: ScreenConfig):
        self.config = config

    def screen(self, resume: ExtractedResume) -> Dict:
        passed_reasons: List[str] = []
        failed_reasons: List[str] = []

        # 技能（命中比例）
        if self.config.required_skills:
            hit = sum(
                1 for s in self.config.required_skills
                if any(s in sk or sk in s for sk in resume.skills) or s in resume.text.lower()
            )
            ratio = hit / len(self.config.required_skills)
            if ratio >= self.config.skill_hit_ratio:
                passed_reasons.append(f"技能命中 {hit}/{len(self.config.required_skills)}")
            else:
                failed_reasons.append(
                    f"技能命中 {hit}/{len(self.config.required_skills)} 低于阈值 {self.config.skill_hit_ratio:.0%}"
                )

        # 经验年限
        if self.config.min_experience_years > 0:
            if resume.experience_years >= self.config.min_experience_years:
                passed_reasons.append(f"经验 {resume.experience_years:.0f} 年 ≥ {self.config.min_experience_years:.0f} 年")
            else:
                failed_reasons.append(
                    f"经验 {resume.experience_years:.0f} 年不足 {self.config.min_experience_years:.0f} 年"
                )

        # 学历
        if self.config.min_education:
            required_level = EDUCATION_LEVELS.get(self.config.min_education, 0)
            actual_level = EDUCATION_LEVELS.get(resume.education, 0)
            if actual_level >= required_level:
                passed_reasons.append(f"学历 {resume.education or '未识别'} 达标")
            else:
                failed_reasons.append(f"学历 {resume.education or '未识别'} 未达 {self.config.min_education}")

        # 地点
        if self.config.locations:
            if any(_fuzzy_match(loc, resume.locations + [resume.text]) for loc in self.config.locations):
                passed_reasons.append(f"地点匹配 {self.config.locations}")
            else:
                failed_reasons.append(f"地点不匹配 {self.config.locations}")

        # 性别（可选硬性）
        if self.config.required_gender and resume.gender and resume.gender != self.config.required_gender:
            failed_reasons.append(f"性别 {resume.gender} 不符合要求 {self.config.required_gender}")

        return {
            "resume": resume,
            "passed": not failed_reasons,
            "passed_reasons": passed_reasons,
            "failed_reasons": failed_reasons,
        }
