"""结构化论文分析与解析结果 Schema."""

from __future__ import annotations

from typing import Dict, List, Any, Union

from pydantic import BaseModel, Field, field_validator


def _coerce_to_str(v: Any) -> str:
    """把 LLM 输出的各种类型都归一成 str。

    真实 LLM 经常把 year 返回成 int (2025)，把 sample_size 返回成 list
    (["Pancancer38k…","约 8,444 个细胞"])，pydantic 默认严格类型会直接 422。
    这里统一在 schema 入口做归一化，下游用 string 即可。
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        # year 这种纯数字最常见；其他数字也按字符串化
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)
    if isinstance(v, list):
        # 列表转成 "；" 分隔的字符串
        return "；".join(_coerce_to_str(x) for x in v if x is not None and str(x).strip())
    if isinstance(v, dict):
        return "；".join(f"{k}：{_coerce_to_str(val)}" for k, val in v.items())
    return str(v)


class Samples(BaseModel):
    species: List[str] = Field(default_factory=list)
    sample_size: str = ""
    tissue: List[str] = Field(default_factory=list)
    dataset: List[str] = Field(default_factory=list)

    @field_validator("sample_size", mode="before")
    @classmethod
    def _v_sample_size(cls, v):
        return _coerce_to_str(v)


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

    @field_validator("year", mode="before")
    @classmethod
    def _v_year(cls, v):
        return _coerce_to_str(v)

    @field_validator("main_findings", mode="before")
    @classmethod
    def _v_findings(cls, v):
        # LLM 偶尔把 main_findings 写成字符串列表而非 finding 对象列表，
        # 把它归一成 [{finding_id, finding, evidence, figure, importance}]
        if not isinstance(v, list):
            return []
        out: List[Dict[str, Any]] = []
        for i, item in enumerate(v, 1):
            if isinstance(item, dict):
                out.append(
                    {
                        "finding_id": item.get("finding_id") or f"F{i}",
                        "finding": _coerce_to_str(item.get("finding", "")),
                        "evidence": _coerce_to_str(item.get("evidence", "")),
                        "figure": _coerce_to_str(item.get("figure", "")),
                        "importance": _coerce_to_str(item.get("importance", "medium")),
                    }
                )
            elif isinstance(item, str):
                out.append({"finding_id": f"F{i}", "finding": item, "evidence": "", "figure": "", "importance": "medium"})
        return out

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
