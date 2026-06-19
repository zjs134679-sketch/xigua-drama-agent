"""词库加载与热更新。

- 仓库内 dict/red.txt、dict/yellow.txt 仅含占位/演示词，**不含任何真实敏感词**。
- 真实词库由云端 auth-server 加密下发，落地为 dict/red.local.txt / dict/yellow.local.txt
  （已 gitignore）。加载时若存在 .local 则优先使用，否则回退仓库内占位文件。
- 行格式：`词` 或 `词|分类`，`#` 开头为注释。
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.services.compliance.dfa import DFA
from app.services.compliance.normalizer import normalize, to_pinyin


class ComplianceDictionary:
    def __init__(self) -> None:
        self.red = DFA()
        self.yellow = DFA()
        self.red_pinyin = DFA()
        self.red_pinyin_src: dict[str, str] = {}  # 拼音 -> 原词
        self.categories: dict[str, str] = {}  # 归一词 -> 分类
        self.loaded = False

    def _resolve(self, base: str) -> Path:
        """优先 .local（云端下发的真实词库），否则用仓库占位文件。"""
        local = settings.dict_dir / f"{base}.local.txt"
        return local if local.exists() else settings.dict_dir / f"{base}.txt"

    @staticmethod
    def _parse_file(path: Path) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        if not path.exists():
            return entries
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            word, _, category = line.partition("|")
            word = word.strip()
            if word:
                entries.append((word, category.strip()))
        return entries

    def load(self) -> "ComplianceDictionary":
        red, yellow, red_py = DFA(), DFA(), DFA()
        py_src: dict[str, str] = {}
        cats: dict[str, str] = {}

        for word, cat in self._parse_file(self._resolve("red")):
            nw = normalize(word)
            if not nw:
                continue
            red.add(nw)
            cats[nw] = cat or "red"
            if len(nw) >= 2:  # 谐音匹配只对 ≥2 字词，降误报
                py = to_pinyin(word)
                if len(py) >= 4:
                    red_py.add(py)
                    py_src[py] = word

        for word, cat in self._parse_file(self._resolve("yellow")):
            nw = normalize(word)
            if not nw:
                continue
            yellow.add(nw)
            cats[nw] = cat or "yellow"

        self.red, self.yellow, self.red_pinyin = red, yellow, red_py
        self.red_pinyin_src, self.categories = py_src, cats
        self.loaded = True
        return self

    def reload(self) -> "ComplianceDictionary":
        return self.load()

    def stats(self) -> dict:
        if not self.loaded:
            self.load()
        return {"red": len(self.red), "yellow": len(self.yellow), "red_homophone": len(self.red_pinyin)}


dictionary = ComplianceDictionary()
