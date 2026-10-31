"""条件式 DAG：把“如果 <条件> 就 <动作> [否则 <动作>]”编译为查询节点 + 条件动作。

约束：
- 不修改 policy.py / validator.py / 审计哈希链；
- 只是计划元数据与执行前求值，最终仍走原有策略、确认、强验证。
"""

from __future__ import annotations

import re

from . import bankcore, tools

_OPS = [
    ("不低于", ">="), ("不少于", ">="), ("至少", ">="),
    ("不超过", "<="), ("不高于", "<="), ("至多", "<="),
    ("超过", ">"), ("超", ">"), ("大于", ">"), ("高于", ">"),
    ("低于", "<"), ("少于", "<"), ("小于", "<"), ("不到", "<"),
    ("等于", "=="), ("达到", ">="),
]
_RANGE_CN = {"上月": "last_month", "上个月": "last_month", "本月": "this_month",
             "这个月": "this_month", "当月": "this_month"}


def _number(text: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万)?", text or "")
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2):
        value *= 10000
    return int(round(value * 100))


def parse(text: str) -> dict | None:
    raw = (text or "").strip()
    m_if = re.search(r"如果|若|要是", raw)
    if not m_if:
        return None
    prefix = raw[:m_if.start()]
    body = raw[m_if.start():]
    m = re.match(r"^(?:如果|若|要是)(.+?)(?:，|,)?\s*(?:就|那么|则)(.+)$", body)
    if not m:
        return None
    cond_text, rest = m.group(1).strip(), m.group(2).strip()
    if not re.search(r"余额|账户里|卡里|账单|支出|消费|花", cond_text):
        cond_text = prefix + cond_text
    else_text = ""
    parts = re.split(r"(?:，|,)?\s*(?:否则|不然)\s*", rest, maxsplit=1)
    then_text = parts[0].strip()
    if len(parts) > 1:
        else_text = parts[1].strip()
    if not then_text:
        return None

    conditions = []
    for clause in re.split(r"(?:并且|而且|同时|且)", cond_text):
        clause = clause.strip()
        if not clause:
            continue
        op = None
        for word, symbol in _OPS:
            if word in clause:
                op = symbol
                break
        if not op:
            continue
        value = _number(clause)
        if value is None:
            continue
        if re.search(r"余额|账户里|卡里", clause):
            conditions.append({"kind": "balance", "op": op, "value": value,
                               "label": f"余额 {op} {value / 100:.2f} 元",
                               "query": "get_accounts", "field": "balance_cents"})
        elif re.search(r"账单|支出|消费|花", clause):
            range_key = "last_month" if re.search(r"上个月|上月", clause) else "this_month"
            conditions.append({"kind": "spending", "range": range_key, "op": op,
                               "value": value, "label": f"{'上月' if range_key == 'last_month' else '本月'}支出 {op} {value / 100:.2f} 元",
                               "query": "analyze_spending", "field": "total_cents"})
        else:
            continue
    if not conditions:
        return None
    return {"conditions": conditions, "then": then_text, "else": else_text,
            "raw": raw}


def evaluate(conn, user_id: int, spec: dict) -> dict:
    results = []
    ok = True
    for cond in spec["conditions"]:
        if cond["kind"] == "spending":
            data = tools.tool_analyze_spending(conn, user_id, {"range": cond["range"]}).get("data") or {}
            value = int(data.get("total_cents") or 0)
            label = data.get("range_label") or cond["label"]
        else:
            value = int(bankcore.get_balance(conn, user_id))
            label = "账户余额"
        passed = _compare(value, cond["op"], cond["value"])
        ok = ok and passed
        results.append({"kind": cond["kind"], "label": label,
                        "value_cents": value, "op": cond["op"],
                        "threshold_cents": cond["value"], "passed": passed,
                        "text": f"{label} {value / 100:.2f} 元"})
    return {"ok": ok, "results": results}


def _compare(left: int, op: str, right: int) -> bool:
    return {"<": left < right, "<=": left <= right, ">": left > right,
            ">=": left >= right, "==": left == right}.get(op, False)


def plan_nodes(spec: dict, prefix: str = "c") -> list[dict]:
    """生成查询节点；条件引用这些节点。"""
    nodes = []
    for index, cond in enumerate(spec["conditions"]):
        node = {"id": f"n{index}", "tool": cond["query"],
                "args": {"range": cond["range"]} if cond["kind"] == "spending" else {},
                "provenance": {}, "depends_on": [], "risk_hint": "green"}
        nodes.append(node)
    return nodes


def condition_metadata(spec: dict) -> list[dict]:
    return [{"node": f"n{index}", "field": cond["field"], "op": cond["op"],
             "value": cond["value"], "label": cond["label"]}
            for index, cond in enumerate(spec["conditions"])]
