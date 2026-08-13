"""
测试硬性条件过滤器模块
"""
import pytest
from app.core.filter import HardFilter
from app.models.metadata import QueryMetadata


class TestHardFilter:
    """测试硬性条件过滤器"""

    def test_init(self):
        """测试初始化"""
        filter_obj = HardFilter()
        assert filter_obj is not None

    def test_filter_by_experience(self):
        """测试根据经验年限过滤"""
        filter_obj = HardFilter()
        
        # 创建模拟简历数据
        resumes = [
            {
                "id": "resume_001",
                "metadata": {
                    "work_experience": [
                        {"start_date": "2020-01", "end_date": "2023-12"},
                        {"start_date": "2018-01", "end_date": "2019-12"}
                    ]
                }
            },
            {
                "id": "resume_002",
                "metadata": {
                    "work_experience": [
                        {"start_date": "2022-01", "end_date": "2023-12"}
                    ]
                }
            }
        ]
        
        # 过滤经验年限至少为3年的简历
        filtered_resumes = filter_obj._filter_by_experience(resumes, 3)
        
        # 验证结果
        assert len(filtered_resumes) == 1
        assert filtered_resumes[0]["id"] == "resume_001"

    def test_filter_by_education(self):
        """测试根据学历过滤"""
        filter_obj = HardFilter()
        
        # 创建模拟简历数据
        resumes = [
            {
                "id": "resume_001",
                "metadata": {
                    "education": [
                        {"degree": "本科"}
                    ]
                }
            },
            {
                "id": "resume_002",
                "metadata": {
                    "education": [
                        {"degree": "硕士"}
                    ]
                }
            },
            {
                "id": "resume_003",
                "metadata": {
                    "education": [
                        {"degree": "大专"}
                    ]
                }
            }
        ]
        
        # 过滤学历至少为本科的简历
        filtered_resumes = filter_obj._filter_by_education(resumes, "本科")
        
        # 验证结果
        assert len(filtered_resumes) == 2
        assert filtered_resumes[0]["id"] == "resume_001"
        assert filtered_resumes[1]["id"] == "resume_002"

    def test_filter_by_skills(self):
        """测试根据技能过滤"""
        filter_obj = HardFilter()
        
        # 创建模拟简历数据
        resumes = [
            {
                "id": "resume_001",
                "metadata": {
                    "skills": ["Python", "Java", "SQL"]
                }
            },
            {
                "id": "resume_002",
                "metadata": {
                    "skills": ["Java", "Spring"]
                }
            }
        ]
        
        # 过滤需要Python和SQL技能的简历
        required_skills = ["Python", "SQL"]
        filtered_resumes = filter_obj._filter_by_skills(resumes, required_skills)
        
        # 验证结果
        assert len(filtered_resumes) == 1
        assert filtered_resumes[0]["id"] == "resume_001"

    def test_filter_by_skills_partial_hit_ratio(self):
        """必需技能按命中比例判定（≥70% 通过），避免 JD 大量必需技能误杀"""
        filter_obj = HardFilter()

        resumes = [
            {
                "id": "resume_001",
                "metadata": {"skills": ["Python", "FastAPI", "RAG", "LangChain"]}
            },
            {
                "id": "resume_002",
                "metadata": {"skills": ["Python", "Java"]}
            }
        ]

        # 5 个必需技能：resume_001 命中 4 个（80% ≥ 70% → 通过）；
        # resume_002 命中 1 个（20% < 70% → 淘汰）
        required_skills = ["Python", "FastAPI", "RAG", "LangChain", "Milvus"]
        filtered = filter_obj._filter_by_skills(resumes, required_skills)

        assert len(filtered) == 1
        assert filtered[0]["id"] == "resume_001"

    def test_filter_by_skills_ratio_below_threshold(self):
        """命中比例低于阈值时淘汰"""
        filter_obj = HardFilter()
        resumes = [
            {"id": "resume_001", "metadata": {"skills": ["Python", "SQL"]}}
        ]
        # 2 个必需技能命中 1 个（50% < 70% → 淘汰）
        filtered = filter_obj._filter_by_skills(resumes, ["Python", "Django"])
        assert len(filtered) == 0

    def test_filter_by_skills_empty_required(self):
        """无必需技能时不淘汰"""
        filter_obj = HardFilter()
        resumes = [{"id": "resume_001", "metadata": {"skills": ["Python"]}}]
        filtered = filter_obj._filter_by_skills(resumes, [])
        assert len(filtered) == 1

    def test_filter_by_locations(self):
        """测试根据工作地点过滤"""
        filter_obj = HardFilter()
        
        # 创建模拟简历数据
        resumes = [
            {
                "id": "resume_001",
                "metadata": {
                    "preferred_locations": ["北京", "上海"]
                }
            },
            {
                "id": "resume_002",
                "metadata": {
                    "preferred_locations": ["广州", "深圳"]
                }
            }
        ]
        
        # 过滤期望工作地点包括北京的简历
        locations = ["北京"]
        filtered_resumes = filter_obj._filter_by_locations(resumes, locations)
        
        # 验证结果
        assert len(filtered_resumes) == 1
        assert filtered_resumes[0]["id"] == "resume_001"

    def test_filter_by_languages(self):
        """测试根据语言要求过滤"""
        filter_obj = HardFilter()
        
        # 创建模拟简历数据
        resumes = [
            {
                "id": "resume_001",
                "metadata": {
                    "languages": ["中文", "英语"]
                }
            },
            {
                "id": "resume_002",
                "metadata": {
                    "languages": ["中文"]
                }
            }
        ]
        
        # 过滤需要英语的简历
        required_languages = ["英语"]
        filtered_resumes = filter_obj._filter_by_languages(resumes, required_languages)
        
        # 验证结果
        assert len(filtered_resumes) == 1
        assert filtered_resumes[0]["id"] == "resume_001"

    def test_filter_by_certifications(self):
        """测试根据证书要求过滤"""
        filter_obj = HardFilter()
        
        # 创建模拟简历数据
        resumes = [
            {
                "id": "resume_001",
                "metadata": {
                    "certifications": ["软件设计师", "PMP"]
                }
            },
            {
                "id": "resume_002",
                "metadata": {
                    "certifications": ["软件设计师"]
                }
            }
        ]
        
        # 过滤需要PMP证书的简历
        required_certifications = ["PMP"]
        filtered_resumes = filter_obj._filter_by_certifications(resumes, required_certifications)
        
        # 验证结果
        assert len(filtered_resumes) == 1
        assert filtered_resumes[0]["id"] == "resume_001"

    def test_filter_resumes(self):
        """测试综合过滤功能"""
        filter_obj = HardFilter()
        
        # 创建模拟简历数据
        resumes = [
            {
                "id": "resume_001",
                "metadata": {
                    "work_experience": [
                        {"start_date": "2020-01", "end_date": "2023-12"}
                    ],
                    "education": [
                        {"degree": "本科"}
                    ],
                    "skills": ["Python", "Java"],
                    "preferred_locations": ["北京"],
                    "languages": ["中文", "英语"],
                    "certifications": ["软件设计师"]
                }
            },
            {
                "id": "resume_002",
                "metadata": {
                    "work_experience": [
                        {"start_date": "2022-01", "end_date": "2023-12"}
                    ],
                    "education": [
                        {"degree": "大专"}
                    ],
                    "skills": ["Java"],
                    "preferred_locations": ["上海"],
                    "languages": ["中文"],
                    "certifications": []
                }
            }
        ]
        
        # 创建查询元数据
        query_metadata = QueryMetadata(
            min_experience_years=2,
            required_education="本科",
            required_skills=["Python"],
            locations=["北京"],
            required_languages=["英语"],
            required_certifications=["软件设计师"]
        )
        
        # 执行过滤
        filtered_resumes = filter_obj.filter_resumes(resumes, query_metadata)
        
        # 验证结果
        assert len(filtered_resumes) == 1
        assert filtered_resumes[0]["id"] == "resume_001"

    def test_parse_year(self):
        """测试日期年份解析"""
        from datetime import datetime
        filter_obj = HardFilter()

        assert filter_obj._parse_year("2020-01") == (2020, False)
        assert filter_obj._parse_year("2020/06") == (2020, False)
        assert filter_obj._parse_year("2020年6月") == (2020, False)
        assert filter_obj._parse_year("2020") == (2020, False)
        assert filter_obj._parse_year("至今") == (datetime.now().year, True)
        assert filter_obj._parse_year("present") == (datetime.now().year, True)
        assert filter_obj._parse_year("") == (None, False)
        assert filter_obj._parse_year(None) == (None, False)

    def test_filter_by_experience_present(self):
        """测试经验过滤对‘至今’的处理"""
        filter_obj = HardFilter()

        resumes = [
            {
                "id": "resume_001",
                "metadata": {
                    "work_experience": [
                        {"start_date": "2020-01", "end_date": "至今"}
                    ]
                }
            },
            {
                "id": "resume_002",
                "metadata": {
                    "work_experience": [
                        {"start_date": "2025-01", "end_date": "至今"}
                    ]
                }
            }
        ]

        filtered_resumes = filter_obj._filter_by_experience(resumes, 4)
        assert len(filtered_resumes) == 1
        assert filtered_resumes[0]["id"] == "resume_001"


if __name__ == "__main__":
    pytest.main([__file__])