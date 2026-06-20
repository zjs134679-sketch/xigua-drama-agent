"""文本归一化 —— 抗规避（全半角 / 大小写 / 夹符拆字 / 谐音拼音）。"""
from __future__ import annotations

import unicodedata

from pypinyin import lazy_pinyin

# 需剔除的"夹符"字符：防占位词被空格、标点等拆开规避。
_SEP_CHARS = set(
    " \t\r\n　.·*-_~/\\|+=^@#$%&"
    ",，。、;；:：!！?？'\"“”‘’()（）[]【】{}<>《》…—~﹏"
    "​‌‍﻿"  # 零宽字符
)


def full_to_half(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            code = 0x20
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        out.append(chr(code))
    return "".join(out)


def normalize(text: str) -> str:
    """字面归一：NFKC + 全转半 + 小写 + 去夹符。用于字符级 DFA 匹配。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = full_to_half(text)
    text = text.lower()
    return "".join(ch for ch in text if ch not in _SEP_CHARS)


def to_pinyin(text: str) -> str:
    """转拼音串（去夹符后），用于谐音匹配。非中文原样保留。"""
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if ch not in _SEP_CHARS)
    return "".join(lazy_pinyin(cleaned)).lower()
