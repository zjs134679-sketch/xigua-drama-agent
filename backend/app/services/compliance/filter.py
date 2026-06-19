"""红黄线判定：`check(text) -> FilterResult`。

策略：红线（字面 + 谐音）命中即 red（硬拦截）；否则黄线命中即 yellow（轻提示）；否则 pass。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.compliance.dictionary import dictionary
from app.services.compliance.normalizer import normalize, to_pinyin


@dataclass
class Hit:
    word: str
    level: str  # red / yellow
    category: str = ""


@dataclass
class FilterResult:
    level: str  # pass / yellow / red
    hits: list[Hit] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.level == "red"

    @property
    def warn(self) -> bool:
        return self.level == "yellow"

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "blocked": self.blocked,
            "hits": [{"word": h.word, "level": h.level, "category": h.category} for h in self.hits],
        }


def _category(word: str) -> str:
    return dictionary.categories.get(word, "")


def check(text: str) -> FilterResult:
    if not dictionary.loaded:
        dictionary.load()
    if not text:
        return FilterResult("pass", [])

    norm = normalize(text)
    hits: list[Hit] = []

    # 红线 —— 字面
    for w in dictionary.red.all_hits(norm):
        hits.append(Hit(w, "red", _category(w)))

    # 红线 —— 谐音/拼音
    if len(dictionary.red_pinyin):
        py = to_pinyin(text)
        seen_py = set()
        for py_hit in dictionary.red_pinyin.all_hits(py):
            if py_hit in seen_py:
                continue
            seen_py.add(py_hit)
            orig = dictionary.red_pinyin_src.get(py_hit, py_hit)
            hits.append(Hit(orig, "red", "谐音"))

    if hits:
        # 按词去重；同词同时命中字面与谐音时，保留字面分类
        by_word: dict[str, Hit] = {}
        for h in hits:
            if h.word not in by_word or by_word[h.word].category == "谐音":
                by_word[h.word] = h
        return FilterResult("red", list(by_word.values()))

    # 黄线
    yhits = [Hit(w, "yellow", _category(w)) for w in dictionary.yellow.all_hits(norm)]
    if yhits:
        uniq = {(h.word, h.category): h for h in yhits}
        return FilterResult("yellow", list(uniq.values()))

    return FilterResult("pass", [])
