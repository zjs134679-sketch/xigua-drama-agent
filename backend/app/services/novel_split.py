"""小说自动分集：优先按"第X章/节/回/集"标题拆，无标题则按长度兜底。"""
from __future__ import annotations

import re

# 第1章 / 第一章 / 第 12 节 / 第三回 / Chapter 3 …
_CHAPTER_RE = re.compile(
    r"(第\s*[0-9一二三四五六七八九十百千零两]+\s*[章节回集卷][^\n]*"
    r"|Chapter\s+\d+[^\n]*)",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _by_length(text: str, max_chars: int) -> list[dict]:
    """按段落聚合到接近 max_chars 一段。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if buf and len(buf) + len(para) > max_chars:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    if not chunks:
        chunks = [text]
    return [{"title": f"第{i}节", "content": c} for i, c in enumerate(chunks, start=1)]


def split_novel(text: str, max_chars: int = 2000) -> list[dict]:
    """返回 [{title, content}]，至少 1 段。"""
    text = _clean(text)
    if not text:
        return []

    parts = _CHAPTER_RE.split(text)
    # 有章节标题：re.split 带捕获组 → [前言, 标题1, 正文1, 标题2, 正文2, ...]
    if len(parts) > 1:
        chapters: list[dict] = []
        preamble = parts[0].strip()
        if preamble:
            chapters.append({"title": "序", "content": preamble})
        i = 1
        while i < len(parts):
            title = parts[i].strip()
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            content = f"{title}\n{body}".strip()
            if content:
                chapters.append({"title": title[:60], "content": content})
            i += 2
        if chapters:
            return chapters

    return _by_length(text, max_chars)
