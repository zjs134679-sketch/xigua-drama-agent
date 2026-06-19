"""DFA（确定有限自动机 / 敏感词 Trie）多模匹配，最长匹配、O(n) 扫描。"""
from __future__ import annotations


class DFA:
    _END = "\x00"

    def __init__(self, words: list[str] | None = None) -> None:
        self._root: dict = {}
        self._size = 0
        for w in words or []:
            self.add(w)

    def add(self, word: str) -> None:
        if not word:
            return
        node = self._root
        for ch in word:
            node = node.setdefault(ch, {})
        if self._END not in node:
            node[self._END] = word
            self._size += 1

    def __len__(self) -> int:
        return self._size

    def first(self, text: str) -> str | None:
        for hit in self._scan(text, stop_first=True):
            return hit
        return None

    def all_hits(self, text: str) -> list[str]:
        return list(self._scan(text, stop_first=False))

    def _scan(self, text: str, stop_first: bool):
        n = len(text)
        i = 0
        while i < n:
            node = self._root
            j = i
            last = None
            last_j = i
            while j < n and text[j] in node:
                node = node[text[j]]
                if self._END in node:
                    last = node[self._END]
                    last_j = j + 1
                j += 1
            if last is not None:
                yield last
                if stop_first:
                    return
                i = last_j  # 最长匹配后跳过
            else:
                i += 1
