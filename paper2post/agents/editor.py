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

        user = self.dump(
            {
                "draft_article": article,
                "fact_check": factcheck.model_dump(),
                "options": options,
            }
        )
        text = self.llm.chat(
            system=self.prompts.editor,
            user=user,
            temperature=float(self.config.get("editor_temperature", 0.3)),
            max_tokens=self.max_tokens(),
        )
        markdown = self._light_clean(text)
        return {
            "markdown": markdown,
            "html": markdown_to_html(markdown),
            "title": self._extract_title(markdown),
        }
