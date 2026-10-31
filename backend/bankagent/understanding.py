"""语义理解层（确定性）：先把一句话拆成"语义帧"，再交给规划器编译成命令。

帧的结构：
    {domain, action, object, filters{range, direction, counterparty, category, ...},
     evidence{每个字段来自原话哪一段}}

这一层的价值：
- 可解释：每个槽位都有 evidence，审计里能看到"为什么这么理解"；
- 可测：同义说法（查/看/列/统计、这个月/本月、我妈/妈妈）都能覆盖，不依赖模型；
- 可兜底：置信度不足时再交给大模型，模型结果同样要过校验。

目前覆盖"查询类"语句（流水 / 转账记录 / 账单），其余业务仍由 nlu.py 处理。
"""

from __future__ import annotations

from .normalize import normalize
from . import phonetic

import re

# ---------------------------------------------------------------- 词典
VERB_QUERY = r"(查询|查一下|查下|查|看一下|看下|看看|列出|列一下|拉一下|给我看|调出)"

OBJ_FLOW = r"(流水|明细|记录|转账记录|交易记录|进出账|进出明细)"
OBJ_BILL = r"(账单|消费|支出|开销|花费|花了|钱都去哪)"

DIR_IN = r"(给我的|转给我|打给我|收到|入账|转入|来账|打过来的|转进来的)"
DIR_OUT = r"(我转给|转给|转出|支出|打给|付给|汇给|转出去的)"

RANGE_RULES = [
    (r"今天|今日", "today"),
    (r"昨天|昨日", "yesterday"),
    (r"前天", "day_before"),
    (r"本周|这周|这礼拜", "this_week"),
    (r"上周|上个星期|上星期", "last_7d"),
    (r"最近\s*(\d+)\s*天|近\s*(\d+)\s*天", "days"),
    (r"最近\s*(\d+)\s*个?月|近\s*(\d+)\s*个?月", "months"),
    (r"最近一周|近一周|最近\s*7\s*天|上周|上个星期|上星期", "last_7d"),
    (r"这个月|本月|当月", "this_month"),
    (r"上个月|上月", "last_month"),
    (r"今年|本年", "this_year"),
    (r"最近三个月|近三个月", "last_3m"),
]

RANGE_LABEL = {
    "this_month": "本月", "last_month": "上月", "this_year": "今年",
    "last_7d": "近 7 天", "last_30d": "近 30 天", "last_3m": "近 3 个月",
}


def _range_from_days(n: int) -> str:
    if n <= 7:
        return "last_7d"
    if n <= 31:
        return "last_30d"
    return "last_3m"


def extract_range(text: str) -> tuple[str | None, str | None]:
    for pattern, kind in RANGE_RULES:
        m = re.search(pattern, text)
        if not m:
            continue
        if kind == "days":
            n = int(m.group(1) or m.group(2) or 7)
            return _range_from_days(n), m.group(0)
        if kind == "months":
            n = int(m.group(1) or m.group(2) or 1)
            return _range_from_days(n * 30), m.group(0)
        return kind, m.group(0)
    return None, None


def extract_direction(text: str) -> tuple[str | None, str | None]:
    m_out = re.search(DIR_OUT, text)
    m_in = re.search(DIR_IN, text)
    # "我妈给我的" 里同时含"给我"，优先判 in；"我转给我妈" 优先判 out
    if m_out and not m_in:
        return "out", m_out.group(0)
    if m_in and not m_out:
        return "in", m_in.group(0)
    if m_out and m_in:
        return ("out" if m_out.start() < m_in.start() else "in"), (m_out.group(0) if m_out.start() < m_in.start() else m_in.group(0))
    return None, None


def extract_contact(text: str, contacts: list[dict]) -> tuple[str | None, str | None]:
    best = None
    for c in contacts:
        for token in (c.get("alias"), c.get("name")):
            if not token:
                continue
            if str(token) in text and (best is None or len(str(token)) > len(best[0])):
                best = (str(token), c)
    if best:
        return best[0], best[0]
    # 同音字兜底：李那 → 李娜
    for c in contacts:
        for token in (c.get("alias"), c.get("name")):
            if token and len(str(token)) >= 2 and phonetic.contains_sound(text, str(token)):
                return str(token), str(token)
    # 没有联系人时，尝试"我妈/你妈"这类称呼
    m = re.search(r"(?:我|你)([爸妈姐弟妹哥嫂姨叔婶姑舅])", text)
    if m:
        return m.group(1), m.group(0)
    return None, None


def extract_category(text: str) -> str | None:
    for cat in ("餐饮", "购物", "交通", "娱乐", "居住", "医疗", "教育", "转账"):
        if cat in text:
            return cat
    return None


def parse(text: str, ctx: dict | None = None, contacts: list[dict] | None = None) -> dict | None:
    """返回 Intent 形状（与 nlu.parse 一致）；不是"查询流水"类语句时返回 None。"""
    raw = normalize(text or "")
    if not raw or len(raw) > 60:
        return None
    # 可疑扣费：直接走异常识别，别退化成订阅列表
    if re.search(r"(乱扣费|乱扣钱|莫名扣费|可疑扣费|盗刷)", raw):
        rng, rev = extract_range(raw)
        slots = {"focus": "anomaly"}
        if rng:
            slots["range"] = rng
        return {"name": "bill_analysis", "slots": slots, "missing": [], "ambiguity": [],
                "spans": {"range": rev} if rev else {}, "confidence": 0.9,
                "source": "understanding.anomaly"}

    has_verb = bool(re.search(VERB_QUERY, raw))
    has_flow = bool(re.search(OBJ_FLOW, raw))
    if not has_flow and has_verb and "转账" in raw:
        has_flow = True          # "查询…的转账" 里的"转账"是名词
    has_bill = bool(re.search(OBJ_BILL, raw))
    inflow_ask = bool(
        (re.search(r"(转给我|打给我|给我|收到|转入)", raw) and re.search(r"(多少|几笔)", raw))
        or re.search(r"(给我的|转给我的|打给我的)(钱|款|转账)", raw)
    )
    if not has_flow and not has_bill and not inflow_ask:
        return None
    bill_question = has_bill and bool(re.search(r"(多少|几|统计|分析|报告|占比|花在)", raw))
    if not has_verb and not re.search(r"(流水|明细)", raw) and not inflow_ask and not bill_question:
        return None
    # "转 500 给妈" 这类指令不是查询
    if re.search(r"(转账|打钱|转)\s*\d", raw) and has_flow and not has_verb:
        return None

    range_key, range_ev = extract_range(raw)
    direction, dir_ev = extract_direction(raw)
    counterparty, cp_ev = extract_contact(raw, contacts or [])
    category = extract_category(raw)

    # "我妈这个月转给我多少钱" 这类：不是转账指令，是问来账金额
    if not has_flow and counterparty and re.search(r"(转给我|打给我|给我|收到|转入)", raw) \
            and (re.search(r"(多少|几笔)", raw)
                 or re.search(r"(给我的|转给我的|打给我的)(钱|款|转账)", raw)):
        slots = {"focus": "transactions", "direction": "in", "counterparty": counterparty}
        if range_key:
            slots["range"] = range_key
        return {"name": "bill_analysis", "slots": slots, "missing": [], "ambiguity": [],
                "spans": {"counterparty": cp_ev}, "confidence": 0.9,
                "source": "understanding.inflow"}

    if has_flow:
        slots = {"focus": "transactions"}
        if range_key:
            slots["range"] = range_key
        if direction:
            slots["direction"] = direction
        if counterparty:
            slots["counterparty"] = counterparty
        if category:
            slots["category"] = category
        evidence = {k: v for k, v in {
            "range": range_ev, "direction": dir_ev, "counterparty": cp_ev, "category": category,
        }.items() if v}
        return {
            "name": "bill_analysis", "slots": slots, "missing": [], "ambiguity": [],
            "spans": evidence, "confidence": 0.95 if has_verb else 0.8,
            "source": "understanding.flow",
        }

    # 账单/消费类：带分类（餐饮/购物/交通…）时，编译成带过滤的支出统计
    if has_bill and category:
        slots = {"focus": "summary", "category": category}
        if range_key:
            slots["range"] = range_key
        return {"name": "bill_analysis", "slots": slots, "missing": [], "ambiguity": [],
                "spans": {"category": category, "range": range_ev}, "confidence": 0.9,
                "source": "understanding.category"}

    # 账单/消费类：其余仍交给 nlu（它已有成熟的区间与报表逻辑）
    return None
