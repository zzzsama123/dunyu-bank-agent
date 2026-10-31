"""Planner：把 Intent 编译成可校验的 DAG（纯函数，禁止 I/O）。

对外唯一入口：

    build_plan(intent: dict, ctx: dict) -> dict
    {"plan_id": "pl_xxxx", "goal": "...", "nodes": [...], "confirm_points": [...]}

节点结构严格遵循 CONTRACT.md §8 / §5.5.1：
    {"id", "tool", "args", "provenance", "depends_on", "risk_hint"}

**运行时引用约定**（Planner 不碰 DB，contact_id 等只能在执行期得到）：
    args 中以 "$n1.contact_id" 形式出现的是对前序节点 ToolResult.data 的引用；
    executor 解析时优先取 data 同名字段，取不到再回退到 items[0].id。
    - get_contacts         -> $nX.contact_id
    - get_accounts         -> $nX.cards[0].card_id / $nX.main_account_id
    - get_wealth_products  -> $nX.product_id（回退 items[0].id）
    - get_subscriptions    -> 具体 sub_id（来自上下文，非引用）
    AA 没有具体姓名时，用 $nX.items[i].id 形式按下标取联系人。
    金额、别名、range 等业务参数一律写具体值，不使用引用。
    该引用只允许作为「身份类参数」（contact_id/card_id/product_id/account_id），
    validator 对金额类参数仍要求具体整数分。

其它约束：
* 节点数 ≤ specs.MAX_PLAN_NODES（8）；
* 只使用 specs.TOOL_SPECS 内的工具（且不生成 system 组工具）；
* 金额 > config.LIMIT_DAILY（1000 元）走 submit_transfer，否则 create_transfer；
* 资金参数 provenance 只允许 user / tool / const，绝不允许 context / untrusted。
"""

from __future__ import annotations

import secrets

from . import config, specs

INTENT_NAMES = {
    "transfer",
    "bill_analysis",
    "subscription",
    "wealth",
    "card",
    "service",
    "standing_plan",
    "unknown",
}

# 生日计划的默认预算（分）——仅在用户未给预算时使用，provenance=const
DEFAULT_BIRTHDAY_BUDGET = 100_000
BIRTHDAY_GIFT_ACTIONS = [
    {"tool": "order_gift", "item": "鲜花", "amount": 30_000},
    {"tool": "order_gift", "item": "蛋糕", "amount": 20_000},
]


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _fmt_money(cents: int | None) -> str:
    try:
        value = int(cents or 0)
    except (TypeError, ValueError):
        value = 0
    return f"{value // 100:,}.{value % 100:02d}"


def _int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _ref(node_id: str, path: str) -> str:
    return f"${node_id}.{path}"


def _risk(tool: str) -> str:
    group = specs.group_of(tool)
    return group if group in ("green", "yellow", "red") else "green"


def _source(intent: dict, key: str, default: str = "user") -> str:
    """参数来源：继承的已确认槽位=user；上下文实体=非资金 context / 资金 tool。"""
    if intent.get("carry_over"):
        return "user"
    if intent.get("from_context"):
        return "context" if key not in specs.FUND_PARAMS else "tool"
    return default


def _provenance(args: dict, sources: dict | None = None) -> dict:
    """为每个参数补齐 provenance，未标注的默认 const。"""
    sources = sources or {}
    return {key: sources.get(key, "const") for key in args}


def _node(node_id: str, tool: str, args: dict, provenance: dict,
          depends_on: list[str] | None = None) -> dict:
    return {
        "id": node_id,
        "tool": tool,
        "args": args,
        "provenance": provenance,
        "depends_on": depends_on or [],
        "risk_hint": _risk(tool),
    }


def _range_label(value: str | None) -> str:
    return {
        "this_month": "本月",
        "last_month": "上月",
        "this_year": "今年",
        "last_7d": "近 7 天",
        "last_30d": "近 30 天",
        "all": "全部",
    }.get(value or "", value or "本月")


def _plan(goal: str, nodes: list[dict], intent_name: str = "unknown") -> dict:
    # 构造期就保证 ≤8，避免把明显超限的计划交给 validator
    nodes = nodes[: specs.MAX_PLAN_NODES]
    confirm = [
        n["id"] for n in nodes
        if specs.group_of(n["tool"]) in ("yellow", "red")
    ]
    return {
        "plan_id": "pl_" + secrets.token_hex(4),
        "intent": intent_name,
        "goal": goal,
        "nodes": nodes,
        "confirm_points": confirm,
    }


def _get_contacts(node_id: str, alias: str | None = None,
                  depends_on: list[str] | None = None) -> dict:
    args = {"alias": alias} if alias else {}
    sources = {"alias": "user"} if alias else {}
    return _node(node_id, "get_contacts", args, _provenance(args, sources), depends_on)


# ---------------------------------------------------------------------------
# 各意图
# ---------------------------------------------------------------------------
def _plan_transfer(intent: dict, slots: dict, ctx: dict) -> tuple[str, list[dict]]:
    amount = _int(slots.get("amount"))
    payee = slots.get("payee")
    if slots.get("mode") == "collection":
        return _plan_collection(intent, slots, ctx)

    if amount is None:
        nodes = [_get_contacts("n1", payee)] if payee else []
        return "请补充转账金额", nodes
    if not payee:
        return "请补充收款人", []

    n1 = _get_contacts("n1", payee)
    quote_args = {
        "contact_id": _ref("n1", "contact_id"),
        "amount": amount,
        "payee": payee,
    }
    n2 = _node(
        "n2", "quote_transfer", quote_args,
        _provenance(quote_args, {
            "contact_id": "tool",
            "amount": _source(intent, "amount"),
            "payee": _source(intent, "payee"),
        }),
        ["n1"],
    )
    tool = "submit_transfer" if amount > config.LIMIT_DAILY else "create_transfer"
    n3_args = {
        "contact_id": _ref("n1", "contact_id"),
        "amount": amount,
        "payee": payee,
    }
    n3 = _node(
        "n3", tool, n3_args,
        _provenance(n3_args, {
            "contact_id": "tool",
            "amount": _source(intent, "amount"),
            "payee": _source(intent, "payee"),
        }),
        ["n1", "n2"],
    )
    goal = f"向{payee}转账 {_fmt_money(amount)} 元"
    if tool == "submit_transfer":
        goal += "（大额，需强验证）"
    return goal, [n1, n2, n3]


def _plan_collection(intent: dict, slots: dict, ctx: dict) -> tuple[str, list[dict]]:
    per = _int(slots.get("amount"))
    if per is None:
        return "请补充 AA 收款金额", []

    payers = slots.get("payers")
    payers = [p for p in payers if isinstance(p, str) and p] if isinstance(payers, list) else []
    payer_count = _int(slots.get("payer_count"))
    nodes: list[dict] = []
    refs: list[str] = []

    if payers:
        for index, name in enumerate(payers[:7], start=1):
            node_id = f"n{index}"
            nodes.append(_get_contacts(node_id, name))
            refs.append(_ref(node_id, "contact_id"))
    else:
        # 没有具体姓名时，先拉联系人列表，再按前 N 个联系人收款。
        count = payer_count if payer_count and payer_count > 0 else 1
        count = min(count, 6)
        node_id = "n1"
        nodes.append(_get_contacts(node_id, None))
        for index in range(count):
            refs.append(_ref(node_id, f"items[{index}].id"))

    collection_id = f"n{len(nodes) + 1}"
    collection_args = {"payer_contact_ids": refs, "amount_each": per}
    collection_sources = {
        "payer_contact_ids": "tool",
        "amount_each": _source(intent, "amount"),
    }
    if payers:
        collection_args["payer_names"] = list(payers[:7])
        collection_sources["payer_names"] = "tool"
    nodes.append(
        _node(
            collection_id, "create_collection_request", collection_args,
            _provenance(collection_args, collection_sources),
            [n["id"] for n in nodes],
        )
    )
    count = max(len(refs), 1)
    goal = f"AA 收款 {_fmt_money(per)} 元/人 × {count} 人（资金流入）"
    return goal, nodes


def _plan_bill(intent: dict, slots: dict) -> tuple[str, list[dict]]:
    range_value = slots.get("range") or "this_month"
    range_source = _source(intent, "range") if slots.get("range") else "const"
    if slots.get("focus") == "transactions":
        # "查我妈给我的转账 / 最近3个月流水"：只查流水，不做支出分析
        nodes: list[dict] = []
        args = {"range": range_value, "limit": 200}
        prov = {"range": range_source}
        if slots.get("direction"):
            args["direction"] = slots["direction"]
            prov["direction"] = _source(intent, "direction")
        if slots.get("category"):
            args["category"] = slots["category"]
            prov["category"] = _source(intent, "category")
        if slots.get("counterparty"):
            args["counterparty"] = slots["counterparty"]
            prov["counterparty"] = _source(intent, "counterparty")
            nodes.append(_node("n1", "get_contacts", {"alias": slots["counterparty"]},
                               _provenance({"alias": slots["counterparty"]},
                                           {"alias": _source(intent, "counterparty")}), []))
        nodes.append(_node("n%d" % (len(nodes) + 1), "get_transactions", args,
                           _provenance(args, prov), []))
        who = slots.get("counterparty") or ""
        flow = "转出" if slots.get("direction") == "out" else ("转入" if slots.get("direction") == "in" else "")
        return f"查询{_range_label(range_value)}{who}的{flow}流水", nodes
    if slots.get("focus") == "anomaly":
        # 账单追问 "有没有不对劲的"：只跑异常识别，不重复做分类统计
        n1 = _node(
            "n1", "detect_anomaly", {"range": range_value},
            _provenance({"range": range_value}, {"range": range_source}), [],
        )
        return f"识别{_range_label(range_value)}异常交易", [n1]
    n1_args = {"range": range_value}
    n1_prov = {"range": range_source}
    if slots.get("category"):
        n1_args["category"] = slots["category"]
        n1_prov["category"] = _source(intent, "category")
    n1 = _node(
        "n1", "analyze_spending", n1_args,
        _provenance(n1_args, n1_prov), [],
    )
    n2 = _node(
        "n2", "detect_anomaly", {"range": range_value},
        _provenance({"range": range_value}, {"range": range_source}), ["n1"],
    )
    nodes = [n1, n2]
    if slots.get("report"):
        n3 = _node(
            "n3", "get_bill_report", {"range": range_value},
            _provenance({"range": range_value}, {"range": range_source}), ["n2"],
        )
        nodes.append(n3)
    goal = f"分析{_range_label(range_value)}支出并识别异常"
    return goal, nodes


def _context_subscriptions(ctx: dict) -> list[dict]:
    """从 ctx["recent_entities"]["subscriptions"] 取长期未使用（≥60 天）的订阅。"""
    raw_lists: list = []
    if isinstance(ctx.get("subscriptions"), list):
        raw_lists.append(ctx["subscriptions"])
    entities = ctx.get("recent_entities")
    if isinstance(entities, dict):
        if isinstance(entities.get("subscriptions"), list):
            raw_lists.append(entities["subscriptions"])
        for key in ("last_entity", "entity", "last"):
            value = entities.get(key)
            if isinstance(value, dict) and isinstance(value.get("subscriptions"), list):
                raw_lists.append(value["subscriptions"])

    picked: list[dict] = []
    seen: set[int] = set()
    for items in raw_lists:
        for item in items:
            if not isinstance(item, dict):
                continue
            sub_id = _int(item.get("id") if item.get("id") is not None else item.get("sub_id"))
            unused_days = _int(item.get("unused_days"))
            if sub_id is None or unused_days is None or unused_days < 60:
                continue
            if sub_id in seen:
                continue
            seen.add(sub_id)
            merchant = item.get("merchant") or item.get("name") or ""
            picked.append({"sub_id": sub_id, "merchant": merchant if isinstance(merchant, str) else ""})
            if len(picked) >= 3:
                return picked
    return picked


def _plan_subscription(intent: dict, slots: dict, ctx: dict) -> tuple[str, list[dict]]:
    action = slots.get("action") or "query"
    n1 = _node("n1", "get_subscriptions", {}, {})
    nodes = [n1]
    if action == "cancel":
        if slots.get("batch"):
            # 批量取消：从上下文挑 unused_days>=60 的订阅，最多 3 个。
            # 取消节点不依赖查询节点（上下文里已经有订阅列表）。
            eligible = _context_subscriptions(ctx)
            for index, item in enumerate(eligible, start=2):
                args = {"sub_id": item["sub_id"]}
                sources = {"sub_id": "tool"}
                if item.get("merchant"):
                    args["merchant"] = item["merchant"]
                    sources["merchant"] = "tool"
                nodes.append(
                    _node(f"n{index}", "cancel_subscription", args,
                          _provenance(args, sources), [])
                )
            if eligible:
                goal = f"取消 {len(eligible)} 项长期未使用的订阅"
            else:
                goal = "查询订阅并确认要取消的项目"
        else:
            sub_ids = slots.get("sub_ids")
            sub_ids = [s for s in sub_ids if _int(s) is not None] if isinstance(sub_ids, list) else []
            # 1 个查询节点 + 最多 7 个取消节点 = 8
            for index, sub_id in enumerate(sub_ids[:7], start=2):
                args = {"sub_id": int(sub_id)}
                nodes.append(
                    _node(f"n{index}", "cancel_subscription", args,
                          _provenance(args, {"sub_id": "tool"}), ["n1"])
                )
            goal = f"取消 {len(nodes) - 1} 项订阅/自动扣费" if len(nodes) > 1 else "查询订阅并确认要取消的项目"
    elif action == "renew":
        goal = "查看订阅续费信息"
    else:
        goal = "查看订阅与自动扣费"
    return goal, nodes


def _plan_wealth(intent: dict, slots: dict) -> tuple[str, list[dict]]:
    action = slots.get("action") or "query"
    args = {}
    sources = {}
    if isinstance(slots.get("risk_level"), str) and slots["risk_level"]:
        args["risk_level"] = slots["risk_level"]
        sources["risk_level"] = _source(intent, "risk_level")
    n1 = _node("n1", "get_wealth_products", args, _provenance(args, sources), [])
    nodes = [n1]

    if action == "purchase":
        amount = _int(slots.get("amount"))
        product_id = _int(slots.get("product_id"))
        if amount is None:
            return "请补充理财申购金额", nodes
        product_ref = product_id if product_id is not None else _ref("n1", "product_id")
        n2_args = {"product_id": product_ref, "amount": amount}
        nodes.append(
            _node("n2", "purchase_wealth", n2_args,
                  _provenance(n2_args,
                              {"product_id": "tool", "amount": _source(intent, "amount")}),
                  ["n1"])
        )
        goal = f"申购理财 {_fmt_money(amount)} 元"
    elif action == "redeem":
        holding_id = _int(slots.get("holding_id"))
        if holding_id is None:
            goal = "查看可赎回的理财持仓"
        else:
            amount = _int(slots.get("amount"))
            n2_args = {"holding_id": holding_id}
            sources2 = {"holding_id": "tool"}
            if amount is not None:
                n2_args["amount"] = amount
                sources2["amount"] = _source(intent, "amount")
            nodes.append(
                _node("n2", "redeem_wealth", n2_args, _provenance(n2_args, sources2), ["n1"])
            )
            goal = f"赎回理财 {_fmt_money(amount)} 元" if amount else "赎回理财持仓"
    else:
        goal = "查看理财产品"
    return goal, nodes


def _plan_card(intent: dict, slots: dict) -> tuple[str, list[dict]]:
    action = slots.get("action") or "query"
    n1 = _node("n1", "get_accounts", {}, {})
    nodes = [n1]
    card_id = _int(slots.get("card_id"))
    card_ref = card_id if card_id is not None else _ref("n1", "cards[0].card_id")

    if action == "lost":
        args = {"card_id": card_ref}
        nodes.append(
            _node("n2", "report_card_lost", args,
                  _provenance(args, {"card_id": "tool"}), ["n1"])
        )
        goal = "挂失卡片（不可逆，需强验证）"
    elif action == "unlock":
        args = {"card_id": card_ref}
        nodes.append(
            _node("n2", "unlock_card", args,
                  _provenance(args, {"card_id": "tool"}), ["n1"])
        )
        goal = "解除卡片挂失"
    elif action == "limit":
        new_limit = _int(slots.get("new_limit"))
        if new_limit is None:
            return "请补充新的卡片额度", nodes
        args = {"card_id": card_ref, "new_limit": new_limit}
        nodes.append(
            _node("n2", "adjust_card_limit", args,
                  _provenance(args, {"card_id": "tool",
                                     "new_limit": _source(intent, "new_limit")}),
                  ["n1"])
        )
        goal = f"调整卡片额度为 {_fmt_money(new_limit)} 元"
    elif action == "apply":
        args = {"card_type": "virtual"}
        nodes.append(
            _node("n2", "apply_virtual_card", args,
                  _provenance(args, {"card_type": "const"}), ["n1"])
        )
        goal = "申请虚拟卡"
    else:
        goal = "查看账户与卡片"
    return goal, nodes


def _plan_standing(intent: dict, slots: dict) -> tuple[str, list[dict]]:
    goal_text = slots.get("goal") or "长期计划"
    payee = slots.get("payee")
    budget = _int(slots.get("budget"))
    budget_source = _source(intent, "budget") if budget is not None else "const"

    is_birthday = "生日" in goal_text or "纪念日" in goal_text or bool(payee and "生日" in goal_text)
    if is_birthday or "生日" in str(slots.get("when") or ""):
        if budget is None:
            budget = DEFAULT_BIRTHDAY_BUDGET
            budget_source = "const"
        n1 = _get_contacts("n1", payee)
        n2 = _node("n2", "get_profile", {}, {})
        freeze_args = {"amount": budget}
        freeze_prov = {"amount": budget_source}
        n3 = _node("n3", "freeze_amount", freeze_args,
                   _provenance(freeze_args, freeze_prov), ["n1", "n2"])
        trigger = {"type": "birthday", "days_before": 2, "time": "09:00"}
        if slots.get("remind_at"):
            trigger = {"type": "at", "at": str(slots["remind_at"]), "time": "09:00"}
        plan_args = {
            "goal": goal_text,
            "trigger": trigger,
            "actions": BIRTHDAY_GIFT_ACTIONS,
            "budget": budget,
        }
        n4 = _node(
            "n4", "create_standing_plan", plan_args,
            _provenance(plan_args, {
                "goal": _source(intent, "goal"),
                "trigger": "const",
                "actions": "const",
                "budget": budget_source,
            }),
            ["n2", "n3"],
        )
        label = "生日" if "生日" in goal_text else "纪念日"
        goal = f"为{payee or '家人'}准备{label}（预算 {_fmt_money(budget)} 元）"
        return goal, [n1, n2, n3, n4]

    # 非生日的长期计划：读档案 → 创建计划（提醒/定时任务，不锁预算）
    n1 = _node("n1", "get_profile", {}, {})
    plan_args = {
        "goal": goal_text,
        "trigger": {"type": "reminder", "when": slots.get("when") or "next_due"},
        "actions": [{"tool": "notify", "content": goal_text}],
    }
    if budget:
        plan_args["budget"] = budget
    n2 = _node(
        "n2", "create_standing_plan", plan_args,
        _provenance(plan_args, {
            "goal": _source(intent, "goal"),
            "trigger": "const",
            "actions": "const",
            "budget": budget_source,
        }),
        ["n1"],
    )
    return f"创建长期计划：{goal_text}", [n1, n2]


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------
def _plan_service(intent: dict, slots: dict) -> tuple[str, list[dict]]:
    action = str(slots.get("action") or "risk")
    if action == "reverse":
        txn_id = _int(slots.get("txn_id"))
        if txn_id is None:
            return "没找到可撤销的最近一笔转账", []
        args = {"txn_id": txn_id}
        node = _node("n1", "reverse_transfer", args,
                     _provenance(args, {"txn_id": "tool"}))
        return "撤销最近一笔转账", [node]
    if action == "repay_card":
        amount = _int(slots.get("amount"))
        if amount is None:
            return "请补充信用卡还款金额", []
        args = {"amount": amount}
        node = _node("n1", "repay_credit_card", args,
                     _provenance(args, {"amount": _source(intent, "amount")}))
        return f"信用卡还款 {amount / 100:.2f} 元", [node]
    if action == "repay":
        args = {}
        amount = _int(slots.get("amount"))
        if amount:
            args["amount"] = amount
        node = _node("n1", "repay_loan", args,
                     _provenance(args, {"amount": _source(intent, "amount") if amount else "const"}))
        return "归还贷款", [node]
    if action == "password":
        args = {"channel": str(slots.get("channel") or "sms")}
        node = _node("n1", "request_password_reset", args,
                     _provenance(args, {"channel": "const"}))
        return "重置密码（发送一次性链接）", [node]
    level = str(slots.get("level") or "稳健型")
    args = {"level": level}
    node = _node("n1", "risk_assessment", args,
                 _provenance(args, {"level": _source(intent, "level") or "const"}))
    return f"风险测评（{level}）", [node]


def build_plan(intent: dict, ctx: dict | None = None) -> dict:
    """按契约映射把 Intent 编译为 Plan dict。纯函数，不读 DB/文件。"""
    if not isinstance(intent, dict):
        intent = {}
    if not isinstance(ctx, dict):
        ctx = {}
    name = intent.get("name")
    if name not in INTENT_NAMES:
        name = "unknown"
    slots = intent.get("slots") if isinstance(intent.get("slots"), dict) else {}

    if name == "transfer":
        goal, nodes = _plan_transfer(intent, slots, ctx)
    elif name == "bill_analysis":
        goal, nodes = _plan_bill(intent, slots)
    elif name == "subscription":
        goal, nodes = _plan_subscription(intent, slots, ctx)
    elif name == "wealth":
        goal, nodes = _plan_wealth(intent, slots)
    elif name == "card":
        goal, nodes = _plan_card(intent, slots)
    elif name == "service":
        goal, nodes = _plan_service(intent, slots)
    elif name == "standing_plan":
        goal, nodes = _plan_standing(intent, slots)
    else:
        goal, nodes = "未识别的请求", []

    # 最终防线：过滤白名单外工具与 system 工具（正常路径不会触发）
    nodes = [
        n for n in nodes
        if n.get("tool") in specs.TOOL_SPECS
        and specs.group_of(n.get("tool", "")) != "system"
    ]
    return _plan(goal, nodes, name)
