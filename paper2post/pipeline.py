"""Pipeline：编排 PDF -> 推文 全流程。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import PROJECT_ROOT, load_settings
from .prompts import Prompts
from .llm import LLMProvider, MockProvider, build_provider
from .parser import parse_pdf
from .utils import dump_json, build_evidence_map, timestamp
from .agents import (
    ReaderAgent,
    StorytellerAgent,
    FigureAgent,
    WriterAgent,
    ReviewerAgent,
    EditorAgent,
)
from .schemas import (
    ParsedPaper,
    PaperAnalysis,
    EvidenceMap,
    Storyline,
    FactCheck,
)


def make_provider(
    settings: Dict[str, Any],
    *,
    provider_name: Optional[str] = None,
    mock: bool = False,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMProvider:
    """按配置构建 LLM Provider（支持 OpenAI 兼容与原生协议）。"""
    if mock:
        return MockProvider()
    return build_provider(
        settings,
        provider_name=provider_name,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )


class Pipeline:
    def __init__(
        self,
        settings: Optional[Dict[str, Any]] = None,
        llm: Optional[LLMProvider] = None,
    ):
        self.settings = settings or load_settings()
        self.llm = llm
        self.prompts = Prompts.load(PROJECT_ROOT)

    def run(
        self,
        pdf_path: str,
        output_dir: str = "outputs",
        options: Optional[Dict[str, Any]] = None,
        figures_dir: Optional[str] = None,
        progress: Optional[Callable[..., None]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        if self.llm is None:
            raise RuntimeError("Pipeline 需要 LLM Provider；请传入 llm 参数或使用 make_provider 构建。")

        effective_options = {
            "article_type": options.get("article_type", self.settings.get("article_type", "deep_review")),
            "target_audience": options.get("target_audience", self.settings.get("target_audience", "")),
            "article_length": options.get("article_length", self.settings.get("article_length", 2500)),
            "language": options.get("language", self.settings.get("language", "zh-CN")),
            "style": options.get("style", self.settings.get("style", "academic_popularization")),
        }

        fig_dir = figures_dir or str(out / "figures")
        save_figures = bool(effective_options.get("extract_figures", True)) or True

        self._emit(progress, 0, "PDF Parser")
        # ---- 1. 解析 PDF ----
        paper: ParsedPaper = parse_pdf(pdf_path, figures_dir=fig_dir if save_figures else None)

        # 双 LLM 策略：reader/writer/reviewer/editor 走 text_model（flash 文本任务更稳），
        # figure_agent 走 self.llm（vision 必须）。text_model 在 options / settings 里可配。
        text_model_name = (
            options.get("text_model")
            or self.settings.get("text_model")
        )
        text_llm = self.llm
        if text_model_name and text_model_name != getattr(self.llm, "model", None):
            try:
                text_llm = make_provider(
                    self.settings,
                    provider_name=self.settings.get("provider", "deepseek"),
                    model=text_model_name,
                    base_url=self.settings.get("base_url"),
                )
            except Exception:
                text_llm = self.llm

        self._emit(progress, 1, "Paper Reader")
        # ---- 2. Paper Reader ----
        reader = ReaderAgent(text_llm, self.prompts, self.settings)
        analysis: PaperAnalysis = reader.run(paper)

        self._emit(progress, 2, "Evidence Mapping")
        # ---- 3. Evidence ----
        evidence: EvidenceMap = build_evidence_map(analysis)

        self._emit(progress, 3, "Story Planner")
        # ---- 4. Story Planner ----
        storyteller = StorytellerAgent(text_llm, self.prompts, self.settings)
        storyline: Storyline = storyteller.run(analysis, evidence, effective_options["article_type"])

        self._emit(progress, 4, "Figure Agent")
        # ---- 5. Figure Agent（必须用 vision model，但支持 skip_vision） ----
        figure_agent = FigureAgent(self.llm, self.prompts, self.settings)
        skip_vision = bool(effective_options.get("skip_vision", False))
        fig_analysis: List = figure_agent.run(
            paper, storyline, effective_options["article_type"], skip_vision=skip_vision
        )

        self._emit(progress, 5, "Writer")
        # ---- 6. Writer ----
        writer = WriterAgent(text_llm, self.prompts, self.settings)
        draft_article: str = writer.run(analysis, evidence, storyline, fig_analysis, effective_options)

        self._emit(progress, 6, "Scientific Reviewer")
        # ---- 7. Reviewer ----
        reviewer = ReviewerAgent(text_llm, self.prompts, self.settings)
        factcheck: FactCheck = reviewer.run(paper, evidence, draft_article)

        self._emit(progress, 7, "Editor")
        # ---- 8. Editor ----
        editor = EditorAgent(text_llm, self.prompts, self.settings)
        final: Dict[str, str] = editor.run(draft_article, factcheck, effective_options)

        # ---- 9. 导出 ----
        paths = self._export(
            out,
            paper,
            analysis,
            evidence,
            storyline,
            fig_analysis,
            draft_article,
            factcheck,
            final,
        )

        report = self._report(effective_options, self.llm.name, paper, analysis, factcheck, final, paths)
        (out / "generation_report.md").write_text(report, encoding="utf-8")
        return paths

    @staticmethod
    def _emit(progress, idx, label):
        if progress:
            try:
                progress(idx, 8, label)
            except Exception:
                pass

    def _export(
        self,
        out: Path,
        paper: ParsedPaper,
        analysis: PaperAnalysis,
        evidence: EvidenceMap,
        storyline: Storyline,
        fig_analysis: List,
        draft_article: str,
        factcheck: FactCheck,
        final: Dict[str, str],
    ) -> Dict[str, str]:
        meta = {
            "pdf_path": paper.pdf_path,
            "title": paper.title,
            "authors": paper.authors,
            "abstract": paper.abstract,
            "page_count": paper.metadata.get("page_count"),
            "pdf_metadata": paper.metadata.get("pdf_metadata", {}),
        }
        dump_json(meta, out / "metadata.json")
        dump_json(analysis, out / "paper_analysis.json")
        dump_json(evidence, out / "evidence.json")
        dump_json(storyline, out / "storyline.json")
        dump_json(fig_analysis, out / "figure_analysis.json")
        dump_json(factcheck, out / "fact_check.json")
        dump_json({"captions": paper.captions}, out / "captions.json")

        (out / "draft_article.md").write_text(draft_article, encoding="utf-8")
        (out / "final_article.md").write_text(final["markdown"], encoding="utf-8")
        (out / "final_article.html").write_text(final["html"], encoding="utf-8")

        return {
            "metadata.json": str(out / "metadata.json"),
            "paper_analysis.json": str(out / "paper_analysis.json"),
            "evidence.json": str(out / "evidence.json"),
            "storyline.json": str(out / "storyline.json"),
            "figure_analysis.json": str(out / "figure_analysis.json"),
            "fact_check.json": str(out / "fact_check.json"),
            "draft_article.md": str(out / "draft_article.md"),
            "final_article.md": str(out / "final_article.md"),
            "final_article.html": str(out / "final_article.html"),
            "captions.json": str(out / "captions.json"),
        }

    @staticmethod
    def _report(
        options: Dict[str, Any],
        provider: str,
        paper: ParsedPaper,
        analysis: PaperAnalysis,
        factcheck: FactCheck,
        final: Dict[str, str],
        paths: Dict[str, str],
    ) -> str:
        lines = [
            "# Paper2Post 生成报告",
            "",
            "- 时间: " + timestamp(),
            "- PDF: " + paper.pdf_path,
            "- 标题: " + (paper.title or "（未识别）"),
            "- 模式: " + str(options.get("article_type")),
            "- 目标受众: " + str(options.get("target_audience")),
            "- 使用 Provider: " + provider,
            "- 语言: " + str(options.get("language", "")),
            "",
            "## 阶段输出",
            "",
        ]
        for k in paths:
            lines.append("- " + k)
        lines.append("")
        lines.append("## 关键 Finding 数量")
        lines.append("")
        lines.append("- main_findings: " + str(len(analysis.main_findings)))
        lines.append("")
        lines.append("## 事实核验")
        lines.append("")
        lines.append("- overall_score: " + str(factcheck.overall_score))
        lines.append("- passed: " + str(factcheck.passed))
        lines.append("- issues: " + str(len(factcheck.issues)))
        lines.append("")
        lines.append("## 说明")
        lines.append("")
        lines.append("本报告由 Paper2Post 自动生成，人工发布前请审核 final_article.md。")
        return "\n".join(lines)
