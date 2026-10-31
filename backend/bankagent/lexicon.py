"""本地语言资源层：口语同义改写 + 示例语料模糊匹配。

不依赖任何在线服务或大模型：
- canonicalize: 把"到时候/叫我/别忘了/十五号"等口语改写成规则能懂的说法；
- match: 与 data/exemplars.json 里的示例句做字符二元组相似度匹配，
  在规则置信度低时兜底，避免直接掉进"没听清"。

示例库可继续往里加句子，不需要改代码。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .normalize import cn_to_int

_DATA = Path(__file__).with_name("data") / "exemplars.json"

_SYNONYMS = [
    (r"(到时候|到点|到时间|到日子)", ""),
    (r"(叫我|喊我|通知我|吱一声|说一声)", "提醒我"),
    (r"(别忘了|不要忘了|别忘记|记得提醒)", "提醒我"),
    (r"(查查|瞅瞅|看一眼|看下|看一下|瞧瞧)", "查"),
    (r"(钱都花哪了|钱花哪了|花哪了|钱都去哪了)", "这个月钱都去哪了"),
    (r"(打钱|转点钱|转一下|汇点钱|转笔钱)", "转账"),
    (r"(退了|退掉吧|取消掉|不要了|不想用了|不想续了)", "退掉"),
    (r"(卡不能用了|卡用不了|卡刷不了)", "我卡丢了"),
    (r"(还剩多少|还有多少|剩多少)", "还剩多少"),
]


def canonicalize(text: str) -> str:
    out = str(text or "").strip()
    if not out:
        return out
    # 中文数字日期："十五号" → "15号"
    def _num(match: re.Match) -> str:
        number = cn_to_int(match.group(1))
        return str(number) if number else match.group(1)

    out = re.sub(r"([一二两三四五六七八九十]{1,3})(?=\s*[号日])", _num, out)
    for pattern, repl in _SYNONYMS:
        out = re.sub(pattern, repl, out)
    return re.sub(r"\s{2,}", " ", out).strip()


@lru_cache(maxsize=1)
def exemplars() -> list[dict]:
    try:
        data = json.loads(_DATA.read_text(encoding="utf-8"))
        return [item for item in data if item.get("text") and item.get("name")]
    except (OSError, ValueError):
        return []


def _grams(text: str) -> set[str]:
    clean = re.sub(r"[\s，。！？、,.!?；;：:（）()]", "", text or "")
    return {clean[i:i + 2] for i in range(max(0, len(clean) - 1))}


def _similarity(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _amount_cents(text: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万|千)?\s*(?:元|块|块钱)?", text)
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2) == "万":
        value *= 10000
    elif m.group(2) == "千":
        value *= 1000
    if value <= 0:
        return None
    return int(round(value * 100))


def match(text: str, contacts: list[dict] | None = None) -> dict | None:
    """示例语料模糊匹配；分数不够返回 None。"""
    clean = canonicalize(text)
    if not clean or len(clean) > 40:
        return None
    best = None
    for item in exemplars():
        score = _similarity(clean, canonicalize(str(item.get("text") or "")))
        if best is None or score > best[0]:
            best = (score, item)
    if not best or best[0] < 0.55:
        return None

    score, item = best
    name = str(item["name"])
    slots = dict(item.get("slots") or {})
    if name == "transfer":
        amount = _amount_cents(clean)
        if not amount:
            return None
        slots.setdefault("amount", amount)
        payee = None
        for contact in contacts or []:
            for token in (contact.get("alias"), contact.get("name")):
                if token and str(token) in clean:
                    payee = str(token)
                    break
            if payee:
                break
        if not payee:
            return None
        slots.setdefault("payee", payee)
    return {
        "name": name,
        "slots": slots,
        "missing": [],
        "ambiguity": [],
        "spans": {},
        "confidence": round(min(0.9, 0.6 + score * 0.3), 2),
        "source": "lexicon",
    }
