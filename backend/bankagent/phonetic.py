"""同音字匹配：内置静态拼音表（20,924 字），运行时零依赖。

用途：把"李那→李娜""云储存→云存储"这类同音/近音输入对上；
不改变语义，只在精确匹配失败时兜底。
"""

from __future__ import annotations

import difflib
import json
from functools import lru_cache
from pathlib import Path

_TABLE_PATH = Path(__file__).with_name("data") / "pinyin.json"


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        return json.loads(_TABLE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pinyin_key(text: str) -> str:
    table = _table()
    return "".join(table.get(ch, "") for ch in str(text or "") if not ch.isspace())


def ratio(a: str, b: str) -> float:
    ka, kb = pinyin_key(a), pinyin_key(b)
    if not ka or not kb:
        return 0.0
    return difflib.SequenceMatcher(None, ka, kb).ratio()


def same_sound(a: str, b: str, threshold: float = 0.82) -> bool:
    return ratio(a, b) >= threshold


def contains_sound(haystack: str, needle: str, min_len: int = 2) -> bool:
    """needle 的拼音是否是 haystack 拼音的子串（名字≥2 字才用，避免"妈"误伤）。"""
    if len(needle or "") < min_len:
        return False
    kh, kn = pinyin_key(haystack), pinyin_key(needle)
    return bool(kn and kn in kh)
