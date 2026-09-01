"""Prompt 加载器：从 prompts/ 目录读取各 Agent 的 system prompt。"""

from __future__ import annotations

from pathlib import Path
from typing import Dict


class Prompts:
    """按名字加载 prompts 目录下的 .md 文件。"""

    def __init__(self, prompts_dir: Path):
        self.dir = prompts_dir
        self._cache: Dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            path = self.dir / f"{name}.md"
            if path.exists():
                self._cache[name] = path.read_text(encoding="utf-8")
            else:
                self._cache[name] = ""
        return self._cache[name]

    @property
    def reader(self) -> str:
        return self.get("reader")

    @property
    def reader_section(self) -> str:
        return self.get("reader_section")

    @property
    def writer(self) -> str:
        return self.get("writer")

    @property
    def writer_section(self) -> str:
        return self.get("writer_section")

    @property
    def storyteller(self) -> str:
        return self.get("storyteller")

    @property
    def figure_agent(self) -> str:
        return self.get("figure_agent")

    @property
    def writer(self) -> str:
        return self.get("writer")

    @property
    def reviewer(self) -> str:
        return self.get("reviewer")

    @property
    def editor(self) -> str:
        return self.get("editor")

    @classmethod
    def load(cls, root: Path) -> "Prompts":
        return cls(root / "prompts")
