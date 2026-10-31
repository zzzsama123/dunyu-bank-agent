"""澄清候选：听不懂时别回"我不明白"，而是给 2-3 个可点/可回数字的选项。

这是工业界客服最有效的一招：
- 用户不需要重新组织语言，回个"1"就行；
- 我们能从选择里拿到确定的意图，继续走槽位/确认/执行；
- 候选带默认值（如"查这个月账单"），选完直接出结果。
"""

from __future__ import annotations

import re

# key, 关键词, 选项文案, 意图模板
CANDIDATES = [
    ("bill", ("账单", "消费", "花钱", "花哪", "支出", "流水", "明细", "开销"),
     "查这个月的账单 / 流水",
     {"name": "bill_analysis", "slots": {"range": "this_month", "focus": "summary"}}),
    ("transfer", ("转账", "转给", "打钱", "汇款", "付款", "给"),
     "给某人转账（再说一句给谁、多少）",
     {"name": "transfer", "slots": {}}),
    ("subscription", ("会员", "订阅", "自动续费", "扣费", "退订", "续费"),
     "看看 / 退掉不用的订阅",
     {"name": "subscription", "slots": {"action": "query"}}),
    ("wealth", ("理财", "基金", "收益", "申购", "赎回", "持仓"),
     "查理财产品和收益",
     {"name": "wealth", "slots": {"action": "query"}}),
    ("card", ("卡", "额度", "挂失", "解挂", "补卡"),
     "卡片相关（额度 / 挂失 / 解挂）",
     {"name": "card", "slots": {"action": "query"}}),
    ("standing_plan", ("生日", "纪念日", "提醒", "计划", "安排"),
     "安排长期计划 / 到点提醒",
     {"name": "standing_plan", "slots": {}}),
]

HANDOFF = {"key": "handoff", "label": "转人工客服", "intent": None}

DEFAULT_KEYS = ["bill", "transfer", "subscription"]


def _score(text: str, words: tuple[str, ...]) -> int:
    return sum(1 for w in words if w in text)


def build(text: str, ctx: dict | None = None, last_intent: dict | None = None, limit: int = 3) -> list[dict]:
    """按关键词给候选；一个都没命中时给默认三项。"""
    scored = []
    for key, words, label, intent in CANDIDATES:
        s = _score(text or "", words)
        if s:
            scored.append((s, key, label, intent))
    scored.sort(key=lambda x: -x[0])
    picked = scored[:limit]
    if not picked:
        by_key = {c[0]: c for c in CANDIDATES}
        picked = [(0, k, by_key[k][2], by_key[k][3]) for k in DEFAULT_KEYS[:limit]]
    # "再说一遍上次的" 这类：把上一轮意图放到第一位
    if not last_intent or last_intent.get("name") not in {c[1] for c in picked}:
        pass
    out = [{"key": k, "label": label, "intent": intent} for _, k, label, intent in picked]
    out.append(HANDOFF)
    return out


def render(cands: list[dict]) -> str:
    lines = ["没太听清。你可以直接选（第 1 项是查这个月账单）："]
    for i, c in enumerate(cands, start=1):
        lines.append(f"{i}. {c['label']}")
    lines.append("回个数字（比如 1）；也可以直接说人话，比如「这个月花了多少」。")
    return "\n".join(lines)


_NUM = re.compile(r"^\s*(?:第)?\s*([1-9])\s*(?:个|项|条|号)?\s*[。.!！]?\s*$")
_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4}


def pick(state: dict | None, text: str) -> dict | None:
    """用户回"1"/"第一个"/"1."时解析成候选；不匹配返回 None。"""
    if not state or not isinstance(state.get("candidates"), list):
        return None
    m = _NUM.match(text or "")
    idx = None
    if m:
        idx = int(m.group(1))
    else:
        t = (text or "").strip()
        if len(t) == 1 and t in _CN:
            idx = _CN[t]
        elif t in ("第一个", "第二个", "第三个"):
            idx = {"第一个": 1, "第二个": 2, "第三个": 3}[t]
    if idx is None:
        return None
    cands = state["candidates"]
    if 1 <= idx <= len(cands):
        return cands[idx - 1]
    return None
