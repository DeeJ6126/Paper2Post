"""结构化论文分析与解析结果 Schema."""

from __future__ import annotations

from typing import Dict, List, Any

from pydantic import BaseModel, Field


class Samples(BaseModel):
    species: List[str] = Field(default_factory=list)
    sample_size: str = ""
    tissue: List[str] = Field(default_factory=list)
    dataset: List[str] = Field(default_factory=list)


class Finding(BaseModel):
    """单个核心发现，并绑定证据。"""

    finding_id: str = ""
    finding: str = ""
    evidence: str = ""
    figure: str = ""
    importance: str = "high"


class PaperAnalysis(BaseModel):
    """Paper Reader 输出：结构化论文理解。"""

    title: str = ""
    journal: str = ""
    year: str = ""
    research_field: List[str] = Field(default_factory=list)
    research_question: str = ""
    background: List[str] = Field(default_factory=list)
    knowledge_gap: str = ""
    hypothesis: str = ""
    samples: Samples = Field(default_factory=Samples)
    methods: List[str] = Field(default_factory=list)
    main_findings: List[Finding] = Field(default_factory=list)
    innovation: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    authors_conclusion: str = ""

    model_config = {"extra": "allow"}


# ---------- PDF Parser 输出 ----------


class PaperSection(BaseModel):
    heading: str = ""
    text: str = ""


class ParsedFigure(BaseModel):
    index: int = 0
    label: str = ""          # 例如 "Figure 1"
    path: str = ""           # 图像文件路径 (PNG)
    page: int = 0
    bbox: List[float] = Field(default_factory=list)
    caption: str = ""


class ParsedPaper(BaseModel):
    """PDF Parser 输出：供各 Agent 消费的论文输入。"""

    pdf_path: str = ""
    title: str = ""
    authors: str = ""
    affiliations: str = ""
    abstract: str = ""
    full_text: str = ""
    sections: List[PaperSection] = Field(default_factory=list)
    figures: List[ParsedFigure] = Field(default_factory=list)
    captions: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}
