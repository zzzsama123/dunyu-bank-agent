"""命令 IR：把"理解"和"执行"之间的协议固定下来。

模型只允许输出有限命令（cmd code）+ 参数，例如：
    {"cmd": "query.transactions", "args": {"range": "last_3m", "direction": "in", "counterparty": "妈"}}

系统负责：命令是否合法、参数类型、金额单位、权限裁决、执行与审计。
模型不能发明命令，也不能直接调用工具。
"""

from __future__ import annotations

CATALOG: dict[str, dict] = {
    "query.transactions": {
        "desc": "查流水 / 转账记录（可按时间、方向、对方、分类过滤）",
        "intent": "bill_analysis", "fixed": {"focus": "transactions"},
        "args": {"range": "str", "direction": "in|out", "counterparty": "str", "category": "str"},
    },
    "query.bill": {
        "desc": "看某段时间的支出汇总 / 账单",
        "intent": "bill_analysis", "fixed": {"focus": "summary"},
        "args": {"range": "str"},
    },
    "query.anomaly": {
        "desc": "可疑扣费 / 异常交易",
        "intent": "bill_analysis", "fixed": {"focus": "anomaly"},
        "args": {"range": "str"},
    },
    "query.subscriptions": {
        "desc": "看自动扣费 / 订阅列表",
        "intent": "subscription", "fixed": {"action": "query"}, "args": {},
    },
    "manage.subscription.cancel": {
        "desc": "取消某个订阅（用户点名时才带 merchant）",
        "intent": "subscription", "fixed": {"action": "cancel"},
        "args": {"merchant": "str"},
    },
    "query.wealth": {
        "desc": "查理财产品 / 收益",
        "intent": "wealth", "fixed": {"action": "query"}, "args": {"range": "str"},
    },
    "transfer.create": {
        "desc": "转账给某人",
        "intent": "transfer", "args": {"amount": "int_cents", "payee": "str", "memo": "str"},
    },
    "transfer.collection": {
        "desc": "AA 收款（资金流入）",
        "intent": "transfer", "fixed": {"mode": "collection"},
        "args": {"amount": "int_cents", "payer_count": "int"},
    },
    "plan.create": {
        "desc": "创建长期计划 / 生日提醒 / 定时事务",
        "intent": "standing_plan",
        "args": {"payee": "str", "budget": "int_cents", "when": "str", "goal": "str"},
    },
    "card.query": {"desc": "查卡 / 额度", "intent": "card", "fixed": {"action": "query"}, "args": {}},
    "card.lost": {"desc": "挂失", "intent": "card", "fixed": {"action": "lost"}, "args": {}},
    "card.unlock": {"desc": "解挂", "intent": "card", "fixed": {"action": "unlock"}, "args": {}},
    "card.limit": {
        "desc": "调整额度", "intent": "card", "fixed": {"action": "limit"},
        "args": {"new_limit": "int_cents"},
    },
    "human.handoff": {"desc": "转人工", "intent": None, "args": {}},
}

ALLOWED_RANGE = {"this_month", "last_month", "this_year", "last_7d", "last_30d", "last_3m"}


def catalog_for_prompt() -> str:
    lines = []
    for code, spec in CATALOG.items():
        args = ", ".join(f"{k}:{v}" for k, v in spec.get("args", {}).items()) or "无"
        lines.append(f"- {code}：{spec['desc']}；参数：{args}")
    return "\n".join(lines)


def coerce_amount(value) -> int | None:
    """金额统一成分；模型偶尔会给"元"，这里只接受整数分并做合理性检查。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n <= 0 or n > 100_000_000:
        return None
    return n


def to_intent(cmd: dict) -> dict | None:
    """命令 → 现有 Intent 形状；非法命令返回 None。"""
    if not isinstance(cmd, dict):
        return None
    code = str(cmd.get("cmd") or "").strip()
    spec = CATALOG.get(code)
    if not spec:
        return None
    raw_args = cmd.get("args") if isinstance(cmd.get("args"), dict) else {}
    slots: dict = dict(spec.get("fixed") or {})
    for key, kind in (spec.get("args") or {}).items():
        if key not in raw_args or raw_args[key] in (None, ""):
            continue
        value = raw_args[key]
        if kind == "int_cents":
            value = coerce_amount(value)
            if value is None:
                continue
        elif kind in ("in|out",):
            value = str(value).lower()
            if value not in ("in", "out"):
                continue
        elif kind == "int":
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        else:
            value = str(value).strip()
        slots[key] = value
    if "range" in slots and slots["range"] not in ALLOWED_RANGE:
        slots.pop("range")
    if spec.get("intent") is None:
        return {"name": "handoff", "slots": {}, "missing": [], "ambiguity": [],
                "spans": {}, "confidence": float(cmd.get("confidence") or 0.9), "source": "command"}
    return {"name": spec["intent"], "slots": slots, "missing": [], "ambiguity": [],
            "spans": {}, "confidence": float(cmd.get("confidence") or 0.9),
            "source": "command", "command": code}
