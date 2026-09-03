"""Editor Agent：语言与排版优化，输出 MD + HTML。"""

from __future__ import annotations

import re
from typing import Optional

from paper2post.llm.base import LLMProvider
from paper2post.prompts import Prompts
from paper2post.schemas.review import FactCheck
from paper2post.utils import markdown_to_html

from .base import BaseAgent


class EditorAgent(BaseAgent):
    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        super().__init__(llm, prompts, config)

    def _draft(self, article: str, factcheck: FactCheck, options: dict) -> dict:
        markdown = self._light_clean(article)
        html = markdown_to_html(markdown)
        return {"markdown": markdown, "html": html, "title": self._extract_title(markdown)}

    @staticmethod
    def _extract_title(markdown: str) -> str:
        m = re.search(r"^#\s+(.+)$", markdown, re.M)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _light_clean(article: str) -> str:
        text = (article or "").replace("\r\n", "\n").strip()
        # 合并连续空行（保留至多 2 个）
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def run(
        self,
        article: str,
        factcheck: FactCheck,
        options: dict,
    ) -> dict:
        draft = self._draft(article, factcheck, options)
        if self.llm.is_mock:
            return draft

        # 截断 article 到 3KB 避免 vision 模型 ~3KB+ 阈值空响应
        safe_article = (article or "")[:3000]
        # factcheck 全 dump 可能 2-3KB，截断 issues 到前 5 条
        compact_fc = {
            "overall_score": factcheck.overall_score,
            "passed": factcheck.passed,
            "issues": [
                {
                    "category": getattr(i, "category", ""),
                    "severity": getattr(i, "severity", ""),
                    "claim": (getattr(i, "claim", "") or "")[:150],
                    "problem": (getattr(i, "problem", "") or "")[:150],
                    "suggestion": (getattr(i, "suggestion", "") or "")[:150],
                }
                for i in (factcheck.issues or [])[:5]
            ],
        }

        user = self.dump(
            {
                "draft_article": safe_article,
                "fact_check": compact_fc,
                "options": {k: v for k, v in (options or {}).items() if k in ("language", "style", "target_audience")},
            }
        )
        text = self.llm.chat(
            system=self.prompts.editor,
            user=user,
            temperature=float(self.config.get("editor_temperature", 0.3)),
            max_tokens=self.max_tokens(),
        )
        # 兜底：vision 模型在 6KB+ 输入下经常返回空字符串。
        # Editor 只是润色/排版，原稿丢失远比润色失败更糟，
        # 因此 LLM 失败/空响应时退回到原稿 + 原 HTML。
        if not (text or "").strip():
            return draft
        markdown = self._light_clean(text)
        # 再次兜底：LLM 偶尔返回的"无法编辑"类元消息（以 "无法" / "I cannot" 等开头）
        if re.match(r"^[\s#]*(" + r"|".join([
            "无法", "I cannot", "I can\u2019t", "I am unable",
            "Sorry", "I don't have", "I do not have", "I'm sorry",
            "（请", "Draft is empty",
        ]) + r")", markdown, re.IGNORECASE):
            return draft
        return {
            "markdown": markdown,
            "html": markdown_to_html(markdown),
            "title": self._extract_title(markdown),
        }
