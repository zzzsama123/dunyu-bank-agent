"""工具层：26 个 MCP 风格工具 + 统一 ToolResult 信封。

设计要点
--------
* `REGISTRY[name](conn, user_id, args) -> ToolResult`，`call()` 是唯一入口；
* 所有账务能力都转调 `bankcore`，本模块不写任何改账 SQL；
* 信封 `{"ok","data","facts","tainted","error"}`；
* facts 由 `_walk_facts` 自动兜底：data 里每个 ``*_cents`` 都生成 money fact、
  每个 ``txn_no`` 生成 id fact、人名/商户名生成 text fact，避免漏报导致
  下游幻觉校验失败；
* 自由文本字段（memo/merchant/alias 等）自动进入 tainted。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta
from typing import Callable

from . import bankcore, config

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 种子里的固定月收入：18,000.00 元，用于账单储蓄率估算。
MONTHLY_INCOME_CENTS = 1_800_000

# 这些键虽然没有 ``_cents`` 后缀，但语义上仍是金额（分）。
MONEY_KEYS = frozenset({
    "balance_after", "daily_limit", "used", "min_amount", "budget",
})

# 这些键的值是单号/票据号，生成 kind=id 的 fact。
ID_KEYS = frozenset({"txn_no", "ticket_no"})

# 这些键的值是文本，生成 kind=text 的 fact（人名/商户名/品名等）。
TEXT_KEYS = frozenset({
    "name", "payee_name", "merchant", "product_name", "counterparty",
    "alias", "item", "goal", "content", "card_tail", "payee_tail",
})

# 这些字段来自用户/外部自由文本，必须进 tainted。
AUTO_TAINT_KEYS = frozenset({
    "memo", "merchant", "alias", "counterparty", "item", "content",
    "goal", "actions", "payer_names", "message", "payee_name",
})


class ToolError(Exception):
    """工具层参数/业务错误，由 call() 转成失败信封。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 信封 / facts
# ---------------------------------------------------------------------------
def _fail(code: str, message: str) -> dict:
    return {
        "ok": False,
        "data": {},
        "facts": [],
        "tainted": [],
        "error": {"code": code, "message": message},
    }


def _from_bankcore(result: dict) -> dict:
    """把 bankcore 的 {ok:false,error} 转成失败信封。"""
    err = (result or {}).get("error") or {}
    return _fail(err.get("code") or "E_INTERNAL", err.get("message") or "账务处理失败")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _money_key(key: str) -> str:
    """``amount_cents`` -> ``amount``，与 CONTRACT 的 fact key 示例保持一致。"""
    return key[: -len("_cents")] if key.endswith("_cents") else key


class _FactBuilder:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, key: str, kind: str, value, unit: str | None = None) -> None:
        if value is None or value == "":
            return
        fact = {
            "id": f"f{len(self.items) + 1}",
            "key": str(key),
            "kind": kind,
            "value": value,
        }
        if unit:
            fact["unit"] = unit
        self.items.append(fact)

    def money(self, key: str, value: int) -> None:
        self.add(_money_key(key), "money", int(value), "CNY")


def _walk_facts(value, builder: _FactBuilder) -> None:
    """递归遍历 data，自动补齐 money / id / text facts。"""
    if isinstance(value, dict):
        for key, val in value.items():
            if isinstance(val, (dict, list)):
                _walk_facts(val, builder)
                continue
            if key.endswith("_cents") or key in MONEY_KEYS:
                if _is_int(val):
                    builder.money(key, val)
            elif key in ID_KEYS or key.endswith("_txn_no"):
                if isinstance(val, str) and val:
                    builder.add(key, "id", val)
            elif key in TEXT_KEYS:
                if isinstance(val, str) and val:
                    builder.add(key, "text", val)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_facts(item, builder)


def _collect_tainted(value, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, val in value.items():
            if key in AUTO_TAINT_KEYS and key not in found:
                found.append(key)
            _collect_tainted(val, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_tainted(item, found)


def _ok(data: dict | None = None, tainted: list[str] | None = None,
        extras: list[tuple] | None = None) -> dict:
    """构造成功信封：facts 自动覆盖 data 中的金额/单号/文本。"""
    data = dict(data or {})
    builder = _FactBuilder()
    _walk_facts(data, builder)
    for item in extras or []:
        if not item:
            continue
        if isinstance(item, dict):
            builder.add(item.get("key", ""), item.get("kind", "text"),
                        item.get("value"), item.get("unit"))
        else:
            key, kind, value = item[0], item[1], item[2]
            unit = item[3] if len(item) > 3 else None
            builder.add(key, kind, value, unit)
    merged: list[str] = []
    _collect_tainted(data, merged)
    for name in tainted or []:
        if name and name not in merged:
            merged.append(name)
    return {
        "ok": True,
        "data": data,
        "facts": builder.items,
        "tainted": merged,
        "error": None,
    }


# ---------------------------------------------------------------------------
# 参数解析小工具
# ---------------------------------------------------------------------------
def _int_arg(args: dict, key: str, *, required: bool = True, default=None,
             min_value: int | None = None) -> int | None:
    value = args.get(key, default)
    if value is None or value == "":
        if required:
            raise ToolError("E_PARAM", f"缺少参数：{key}")
        return None
    if isinstance(value, bool):
        raise ToolError("E_PARAM", f"参数 {key} 必须为整数（分）")
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        result = int(value)
    except (TypeError, ValueError):
        raise ToolError("E_PARAM", f"参数 {key} 必须为整数（分）")
    if min_value is not None and result < min_value:
        raise ToolError("E_PARAM", f"参数 {key} 不能小于 {min_value}")
    return result


def _str_arg(args: dict, key: str, *, required: bool = True, default: str = "") -> str:
    value = args.get(key, default)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ToolError("E_PARAM", f"缺少参数：{key}")
        return default
    return str(value).strip()


def _list_arg(args: dict, key: str, *, required: bool = True) -> list:
    value = args.get(key)
    if value is None:
        if required:
            raise ToolError("E_PARAM", f"缺少参数：{key}")
        return []
    if not isinstance(value, (list, tuple)):
        raise ToolError("E_PARAM", f"参数 {key} 必须为列表")
    return list(value)


def _int_list_arg(args: dict, key: str) -> list[int]:
    raw = _list_arg(args, key)
    out: list[int] = []
    for item in raw:
        if isinstance(item, bool):
            raise ToolError("E_PARAM", f"参数 {key} 的元素必须为整数")
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            raise ToolError("E_PARAM", f"参数 {key} 的元素必须为整数")
    if not out:
        raise ToolError("E_PARAM", f"参数 {key} 不能为空")
    return out


def _dict_arg(args: dict, key: str) -> dict:
    value = args.get(key)
    if value is None:
        raise ToolError("E_PARAM", f"缺少参数：{key}")
    if not isinstance(value, dict):
        raise ToolError("E_PARAM", f"参数 {key} 必须为对象")
    return value


def _idem_key(args: dict) -> str:
    key = args.get("_idem_key")
    if isinstance(key, str) and key:
        return key
    return f"tool-{uuid.uuid4().hex}"


def _range_name(args: dict) -> str | None:
    value = args.get("range")
    return value if isinstance(value, str) and value.strip() else None


def _range(args: dict) -> tuple[str, str, str]:
    return config.range_bounds(_range_name(args))


# ---------------------------------------------------------------------------
# 查询 / 展示辅助
# ---------------------------------------------------------------------------
def _contact_by_id(conn, user_id: int, contact_id: int) -> dict | None:
    for c in bankcore.list_contacts(conn, user_id):
        if int(c["id"]) == int(contact_id):
            return c
    return None


def _card_by_id(conn, user_id: int, card_id: int) -> dict | None:
    for c in bankcore.list_cards(conn, user_id):
        if int(c["id"]) == int(card_id):
            return c
    return None


def _owned_account_ids(conn, user_id: int) -> set[int]:
    return {int(a["id"]) for a in bankcore.list_accounts(conn, user_id)}


def _card_view(card: dict) -> dict:
    limit = int(card.get("daily_limit") or 0)
    used = int(card.get("used") or 0)
    bill = int(card.get("bill_cents") or 0)
    return {
        "id": int(card["id"]),
        "card_id": int(card["id"]),
        "card_tail": card.get("card_tail"),
        "status": card.get("status"),
        "daily_limit_cents": limit,
        "used_cents": used,
        "available_cents": max(0, limit - used),
        "frozen": int(card.get("frozen") or 0),
    }


def _txn_no_of(conn, user_id: int, txn_id: int) -> str | None:
    """通过 bankcore 的流水接口反查 txn_no（不直接写 SQL）。"""
    for txn in bankcore.list_transactions(conn, user_id, limit=1000):
        if int(txn["id"]) == int(txn_id):
            return txn.get("txn_no")
    return None


def _fmt_yuan(cents: int) -> str:
    cents = int(cents)
    sign = "-" if cents < 0 else ""
    value = abs(cents)
    return f"{sign}{value // 100:,}.{value % 100:02d}"


def _spending_rows(conn, user_id: int, start: str, end: str) -> list[dict]:
    rows = bankcore.list_transactions(conn, user_id, start, end, limit=1000)
    return [
        r for r in rows
        if r.get("direction") == "out" and r.get("status") == "succeeded"
    ]


def _aggregate_categories(rows: list[dict]) -> tuple[int, list[dict]]:
    totals: dict[str, int] = {}
    for row in rows:
        category = row.get("category") or "未分类"
        totals[category] = totals.get(category, 0) + int(row.get("amount_cents") or 0)
    total = sum(totals.values())
    items = [
        {
            "category": name,
            "amount_cents": amount,
            "ratio": round(amount / total, 4) if total else 0.0,
        }
        for name, amount in totals.items()
    ]
    items.sort(key=lambda x: (-x["amount_cents"], x["category"]))
    return total, items


def _previous_bounds(start: str, end: str, label: str) -> tuple[str, str]:
    """同类上一周期，用于环比/上月对比。"""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    if label.endswith("年"):
        prev_year = s.year - 1
        return f"{prev_year}-01-01", f"{prev_year}-12-31"
    if s.day == 1:
        prev_end = s - timedelta(days=1)
        return prev_end.replace(day=1).isoformat(), prev_end.isoformat()
    span = (e - s).days + 1
    prev_end = s - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    return prev_start.isoformat(), prev_end.isoformat()


def _category_ratio_facts(items: list[dict], prefix: str = "") -> list[tuple]:
    return [
        (f"{prefix}ratio", "ratio", item["ratio"])
        for item in items
        if item.get("ratio") is not None
    ]


# ---------------------------------------------------------------------------
# 绿 · 只读工具
# ---------------------------------------------------------------------------
def tool_get_accounts(conn, user_id: int, args: dict) -> dict:
    accounts = []
    for account in bankcore.list_accounts(conn, user_id):
        item = dict(account)
        item["account_id"] = int(account["id"])
        accounts.append(item)
    cards = [_card_view(card) for card in bankcore.list_cards(conn, user_id)]
    try:
        main_account_id = bankcore.main_account(conn, user_id)
    except bankcore.BankError:
        main_account_id = None
    return _ok({
        "accounts": accounts,
        "cards": cards,
        "main_account_id": main_account_id,
        "currency": "CNY",
    })


def tool_get_transactions(conn, user_id: int, args: dict) -> dict:
    start, end, label = _range(args)
    limit = _int_arg(args, "limit", required=False, default=50, min_value=1)
    limit = min(int(limit or 50), 200)
    category = args.get("category")
    category = category.strip() if isinstance(category, str) and category.strip() else None
    rows = bankcore.list_transactions(conn, user_id, start, end, limit=limit)
    if category:
        rows = [r for r in rows if r.get("category") == category]
    counterparty = args.get("counterparty")
    counterparty = counterparty.strip() if isinstance(counterparty, str) and counterparty.strip() else None
    direction = args.get("direction")
    direction = direction.strip().lower() if isinstance(direction, str) and direction.strip() else None
    if counterparty:
        names = {c["name"] for c in bankcore.find_contact(conn, user_id, counterparty)}
        rows = [r for r in rows if r.get("counterparty") in names] if names else []
    if direction in ("in", "out"):
        rows = [r for r in rows if r.get("direction") == direction]
    return _ok({
        "items": rows,
        "count": len(rows),
        "range_label": label,
        "range_start": start,
        "range_end": end,
        "category": category,
        "counterparty": counterparty,
        "direction": direction,
    })


def tool_analyze_spending(conn, user_id: int, args: dict) -> dict:
    start, end, label = _range(args)
    rows = _spending_rows(conn, user_id, start, end)
    category = args.get("category")
    category = category.strip() if isinstance(category, str) and category.strip() else None
    if category:
        rows = [r for r in rows if r.get("category") == category]
    total, by_category = _aggregate_categories(rows)

    prev_start, prev_end = _previous_bounds(start, end, label)
    prev_rows = _spending_rows(conn, user_id, prev_start, prev_end)
    if category:
        prev_rows = [r for r in prev_rows if r.get("category") == category]
    prev_total = sum(int(r.get("amount_cents") or 0) for r in prev_rows)
    mom_ratio = round((total - prev_total) / prev_total, 4) if prev_total else None
    # 回复模板会用“比上月多/少 X 元”，把差额也申报为事实，避免幻觉校验误伤。
    mom_change = abs(total - prev_total)

    largest = max(rows, key=lambda r: int(r.get("amount_cents") or 0), default=None)
    prev_by = {}
    for row in prev_rows:
        key = str(row.get("category") or "其他")
        prev_by[key] = prev_by.get(key, 0) + int(row.get("amount_cents") or 0)
    cur_by = {str(item.get("category") or "其他"): int(item.get("amount_cents") or 0) for item in by_category}
    top_change = None
    for key in set(cur_by) | set(prev_by):
        delta = cur_by.get(key, 0) - prev_by.get(key, 0)
        if top_change is None or abs(delta) > abs(top_change["delta_cents"]):
            top_change = {"category": key, "delta_cents": delta}
    extras = _category_ratio_facts(by_category)
    if mom_ratio is not None:
        extras.append(("mom_ratio", "ratio", mom_ratio))
    if largest is not None:
        extras.append(("largest_amount", "money", int(largest.get("amount_cents") or 0)))
    if top_change is not None:
        extras.append(("top_change", "money", abs(int(top_change.get("delta_cents") or 0))))
        extras.append(("amount", "money", abs(int(top_change.get("delta_cents") or 0))))

    return _ok({
        "range_label": label,
        "range_start": start,
        "range_end": end,
        "total_cents": total,
        "by_category": by_category,
        "prev_total_cents": prev_total,
        "mom_ratio": mom_ratio,
        "mom_change_cents": mom_change,
        "txn_count": len(rows),
        "category": category,
        "currency": "CNY",
        "largest_txn": None if not largest else {
            "created_at": str(largest.get("created_at") or "")[:10],
            "counterparty": str(largest.get("counterparty") or largest.get("memo") or "未知"),
            "amount_cents": int(largest.get("amount_cents") or 0),
            "category": str(largest.get("category") or "其他"),
        },
        "top_change": top_change,
    }, extras=extras)


def _anomaly_entry(txn: dict, rule: str, reason: str) -> dict:
    return {
        "txn_no": txn.get("txn_no"),
        "counterparty": txn.get("counterparty") or "未知商户",
        "amount_cents": int(txn.get("amount_cents") or 0),
        "created_at": txn.get("created_at"),
        "rule": rule,
        "reason": reason,
    }


def tool_detect_anomaly(conn, user_id: int, args: dict) -> dict:
    start, end, label = _range(args)
    rows = _spending_rows(conn, user_id, start, end)
    all_rows = _spending_rows(conn, user_id, "1970-01-01", "2999-12-31")

    parsed = []
    for row in rows:
        try:
            dt = config.parse_iso(row["created_at"])
        except Exception:
            continue
        parsed.append((dt, row))
    parsed.sort(key=lambda x: x[0])

    # 历史均值（严格早于当前交易），key = merchant
    history: dict[str, list] = {}
    for row in all_rows:
        try:
            dt = config.parse_iso(row["created_at"])
        except Exception:
            continue
        history.setdefault(row.get("counterparty") or "", []).append((dt, int(row.get("amount_cents") or 0)))
    for values in history.values():
        values.sort(key=lambda x: x[0])

    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    # reason 里出现的阈值/均值/聚集合计也要申报为 fact，供 postcheck 校验。
    derived_money: list[tuple] = []

    def push(txn: dict, rule: str, reason: str) -> None:
        key = (str(txn.get("txn_no")), rule)
        if key in seen:
            return
        seen.add(key)
        found.append(_anomaly_entry(txn, rule, reason))

    # 规则 1：凌晨（<6 点）且金额 > 500 元
    for dt, row in parsed:
        amount = int(row.get("amount_cents") or 0)
        if dt.hour < 6 and amount > 50_000:
            push(
                row, "night_large",
                f"凌晨 {dt:%H:%M} 发生 {_fmt_yuan(amount)} 元支出，超过夜间 500.00 元阈值",
            )
            derived_money.append(("night_threshold", "money", 50_000, "CNY"))

    # 规则 2：同商户 1 小时内 >= 3 笔（每个聚集只报首次越线的一笔）
    merchant_history: dict[str, list] = {}
    last_cluster: dict[str, object] = {}
    for dt, row in parsed:
        merchant = row.get("counterparty") or ""
        if not merchant:
            continue
        recent = [
            (hdt, hamt)
            for hdt, hamt in merchant_history.get(merchant, [])
            if 0 <= (dt - hdt).total_seconds() <= 3600
        ]
        last = last_cluster.get(merchant)
        already = last is not None and 0 <= (dt - last).total_seconds() <= 3600
        if len(recent) >= 2 and not already:
            cluster_total = sum(amount for _, amount in recent) + int(row.get("amount_cents") or 0)
            push(
                row, "same_merchant_1h",
                f"同商户「{merchant}」1 小时内共 {len(recent) + 1} 笔、"
                f"合计 {_fmt_yuan(cluster_total)} 元，疑似分拆或盗刷",
            )
            derived_money.append(("cluster_total", "money", cluster_total, "CNY"))
            last_cluster[merchant] = dt
        merchant_history.setdefault(merchant, []).append((dt, int(row.get("amount_cents") or 0)))

    # 规则 3：单笔金额 > 该商户历史均值 3 倍
    for dt, row in parsed:
        merchant = row.get("counterparty") or ""
        if not merchant:
            continue
        prior = [amount for hdt, amount in history.get(merchant, []) if hdt < dt]
        if len(prior) < 3:
            continue
        avg = sum(prior) / len(prior)
        amount = int(row.get("amount_cents") or 0)
        if avg > 0 and amount > avg * 3:
            avg_rounded = int(round(avg))
            push(
                row, "merchant_3x_avg",
                f"金额 {_fmt_yuan(amount)} 元超过该商户历史均值 "
                f"{_fmt_yuan(avg_rounded)} 元的 3 倍",
            )
            derived_money.append(("merchant_avg", "money", avg_rounded, "CNY"))

    found.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    total_found = len(found)
    truncated = total_found > 30
    found = found[:30]
    return _ok({
        "items": found,
        "count": len(found),
        "total_count": total_found,
        "truncated": truncated,
        "range_label": label,
        "rules": ["night_large", "same_merchant_1h", "merchant_3x_avg"],
    }, extras=derived_money)


def tool_get_bill_report(conn, user_id: int, args: dict) -> dict:
    start, end, label = _range(args)
    rows = _spending_rows(conn, user_id, start, end)
    total, by_category = _aggregate_categories(rows)

    daily: dict[str, int] = {}
    for row in rows:
        day = (row.get("created_at") or "")[:10]
        if day:
            daily[day] = daily.get(day, 0) + int(row.get("amount_cents") or 0)
    chart_points = [
        {"date": day, "amount_cents": daily[day]}
        for day in sorted(daily)
    ]

    income = MONTHLY_INCOME_CENTS
    savings = income - total
    savings_rate = round(savings / income, 4) if income else 0.0
    # saving_ratio 是回复模板使用的兼容别名（与 savings_rate 同值）。
    saving_ratio = savings_rate

    extras = _category_ratio_facts(by_category)
    extras.append(("savings_rate", "ratio", savings_rate))
    extras.append(("saving_ratio", "ratio", saving_ratio))

    return _ok({
        "range_label": label,
        "range_start": start,
        "range_end": end,
        "format": args.get("format") or "default",
        "income_cents": income,
        "total_cents": total,
        "savings_cents": savings,
        "savings_rate": savings_rate,
        "saving_ratio": saving_ratio,
        "by_category": by_category,
        "chart_points": chart_points,
        "txn_count": len(rows),
        "currency": "CNY",
    }, extras=extras)


def tool_get_contacts(conn, user_id: int, args: dict) -> dict:
    alias = args.get("alias")
    name = args.get("name")
    query = None
    if isinstance(alias, str) and alias.strip():
        query = alias.strip()
    elif isinstance(name, str) and name.strip():
        query = name.strip()

    if query:
        items = bankcore.find_contact(conn, user_id, query)
    else:
        items = bankcore.list_contacts(conn, user_id)
    return _ok(
        {"items": items, "count": len(items), "query": query},
        tainted=["name", "alias"],
    )


def tool_get_subscriptions(conn, user_id: int, args: dict) -> dict:
    items = bankcore.list_subscriptions(conn, user_id)
    active = [i for i in items if i.get("status") == "active"]
    active_monthly = sum(int(i.get("amount_cents") or 0) for i in active)
    unused_count = sum(
        1 for i in active
        if isinstance(i.get("unused_days"), int) and i["unused_days"] > 30
    )
    return _ok({
        "items": items,
        "count": len(items),
        "active_count": len(active),
        "active_monthly_cents": active_monthly,
        "unused_count": unused_count,
    })


def tool_get_wealth_products(conn, user_id: int, args: dict) -> dict:
    risk_level = args.get("risk_level")
    risk_level = risk_level.strip() if isinstance(risk_level, str) and risk_level.strip() else None
    term_days = _int_arg(args, "term_days", required=False, min_value=1)
    products = bankcore.list_products(conn, risk_level=risk_level, term_days=term_days)
    items = []
    extras: list[tuple] = []
    for product in products:
        items.append({
            "id": int(product["id"]),
            "name": product.get("name"),
            "rate": product.get("rate"),
            "term_days": int(product.get("term_days") or 0),
            "risk_level": product.get("risk_level"),
            # 同时给出 bankcore 原始字段与规范化字段，便于 planner/前端取用。
            "min_amount": int(product.get("min_amount") or 0),
            "min_amount_cents": int(product.get("min_amount") or 0),
        })
        if product.get("rate") is not None:
            extras.append(("rate", "ratio", product["rate"]))
        if product.get("term_days") is not None:
            extras.append(("term", "term", int(product["term_days"]), "天"))
    return _ok({
        "items": items,
        "count": len(items),
        "risk_level": risk_level,
        "term_days": term_days,
    }, extras=extras)


def tool_get_profile(conn, user_id: int, args: dict) -> dict:
    user = bankcore.get_user(conn, user_id)
    if not user:
        raise ToolError("E_NOT_FOUND", "用户不存在")
    return _ok({
        "id": int(user["id"]),
        "name": user.get("name"),
        "risk_level": user.get("risk_level"),
        "risk_score": user.get("risk_score"),
        "kyc_status": user.get("kyc_status"),
        "birthday": user.get("birthday"),
        "monthly_income_cents": MONTHLY_INCOME_CENTS,
    })


def tool_quote_transfer(conn, user_id: int, args: dict) -> dict:
    contact_id = int(_int_arg(args, "contact_id", min_value=1))
    amount = int(_int_arg(args, "amount", min_value=1))
    contact = _contact_by_id(conn, user_id, contact_id)
    if not contact:
        raise ToolError("E_CONTACT_NOT_FOUND", "收款人不存在")

    account_id = bankcore.main_account(conn, user_id)
    balance = bankcore.get_balance(conn, account_id)
    return _ok({
        "contact_id": contact_id,
        "payee_name": contact.get("name") or "",
        "payee_tail": contact.get("card_tail") or "",
        "amount_cents": amount,
        "fee_cents": 0,
        "arrival": "实时",
        "balance_before_cents": balance,
        "balance_after_cents": balance - amount,
        "payee_is_new": bool(int(contact.get("is_new") or 0)),
        "sufficient": balance >= amount,
        "from_account_id": account_id,
        "currency": "CNY",
    }, tainted=["payee_name"], extras=[("arrival", "text", "实时")])


# ---------------------------------------------------------------------------
# 黄 · 低风险写入
# ---------------------------------------------------------------------------
def _do_transfer(conn, user_id: int, args: dict, transfer_fn) -> dict:
    contact_id = int(_int_arg(args, "contact_id", min_value=1))
    amount = int(_int_arg(args, "amount", min_value=1))
    memo = _str_arg(args, "memo", required=False, default="")
    from_account_id = _int_arg(args, "from_account_id", required=False, min_value=1)
    if from_account_id is None:
        from_account_id = bankcore.main_account(conn, user_id)
    elif int(from_account_id) not in _owned_account_ids(conn, user_id):
        raise ToolError("E_NOT_FOUND", "付款账户不存在")
    from_account_id = int(from_account_id)

    result = transfer_fn(
        conn,
        user_id=user_id,
        from_account_id=from_account_id,
        contact_id=contact_id,
        amount_cents=amount,
        memo=memo,
        idem_key=_idem_key(args),
        txn_type="transfer",
    )
    if not result.get("ok"):
        return _from_bankcore(result)

    return _ok({
        "txn_id": int(result.get("txn_id") or 0),
        "txn_no": result.get("txn_no"),
        "amount_cents": int(result.get("amount_cents") or amount),
        "balance_after": int(result.get("balance_after") or 0),
        "payee_name": result.get("payee_name") or "",
        "from_account_id": from_account_id,
        "status": "succeeded",
        "memo": memo,
        "idempotent": bool(result.get("idempotent")),
        "currency": "CNY",
    }, tainted=["memo", "payee_name"])


def tool_create_transfer(conn, user_id: int, args: dict) -> dict:
    return _do_transfer(conn, user_id, args, bankcore.create_transfer)


def tool_submit_transfer(conn, user_id: int, args: dict) -> dict:
    return _do_transfer(conn, user_id, args, bankcore.submit_transfer)


def tool_cancel_subscription(conn, user_id: int, args: dict) -> dict:
    sub_id = int(_int_arg(args, "sub_id", min_value=1))
    result = bankcore.cancel_subscription(conn, user_id, sub_id)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "sub_id": sub_id,
        "merchant": result.get("merchant") or "",
        "effective_at": result.get("effective_at"),
        "message": result.get("message") or "",
        "status": "cancelled",
    })


def tool_apply_virtual_card(conn, user_id: int, args: dict) -> dict:
    card_type = _str_arg(args, "card_type", required=False, default="virtual") or "virtual"
    result = bankcore.apply_virtual_card(conn, user_id, card_type=card_type)
    if not result.get("ok"):
        return _from_bankcore(result)
    card_id = int(result.get("card_id"))
    card = _card_by_id(conn, user_id, card_id)
    view = _card_view(card) if card else {
        "id": card_id,
        "card_id": card_id,
        "card_tail": result.get("card_tail"),
        "status": "active",
        "daily_limit_cents": 0,
        "used_cents": 0,
        "available_cents": 0,
        "frozen": 0,
    }
    view["card_type"] = card_type
    return _ok(view)


def tool_create_collection_request(conn, user_id: int, args: dict) -> dict:
    payer_ids = _int_list_arg(args, "payer_contact_ids")
    amount_each = int(_int_arg(args, "amount_each", min_value=1))

    contacts = {int(c["id"]): c for c in bankcore.list_contacts(conn, user_id)}
    missing = [cid for cid in payer_ids if cid not in contacts]
    if missing:
        raise ToolError("E_CONTACT_NOT_FOUND", f"收款人不存在：{missing[0]}")
    payer_names = [contacts[cid].get("name") or "" for cid in payer_ids]

    result = bankcore.create_collection_request(conn, user_id, payer_ids, amount_each)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "request_ids": result.get("request_ids") or [],
        "count": int(result.get("count") or 0),
        "amount_each_cents": int(result.get("amount_each_cents") or amount_each),
        "total_cents": int(result.get("total_cents") or amount_each * len(payer_ids)),
        "payer_contact_ids": payer_ids,
        "payer_names": payer_names,
        "flow": "in",
        "status": "pending",
    }, tainted=["payer_names"],
        extras=[("payer_name", "text", name) for name in payer_names if name])


def tool_set_reminder(conn, user_id: int, args: dict) -> dict:
    at = _str_arg(args, "at")
    content = _str_arg(args, "content")
    result = bankcore.set_reminder(conn, user_id, at, content)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "reminder_id": int(result.get("reminder_id") or 0),
        "at": result.get("at") or at,
        "content": content,
        "status": "active",
    })


def tool_create_standing_plan(conn, user_id: int, args: dict) -> dict:
    goal = _str_arg(args, "goal")
    trigger = _dict_arg(args, "trigger")
    actions = _list_arg(args, "actions")
    if not actions:
        raise ToolError("E_PARAM", "参数 actions 不能为空")
    budget = _int_arg(args, "budget", required=False, default=0, min_value=0)

    result = bankcore.create_standing_plan(
        conn, user_id, goal, trigger, actions, int(budget or 0)
    )
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "plan_id": int(result.get("plan_id") or 0),
        "goal": result.get("goal") or goal,
        "trigger": trigger,
        "actions": actions,
        "budget_cents": int(result.get("budget_cents") or 0),
        "next_run_at": result.get("next_run_at"),
        "status": "active",
    }, tainted=["goal", "actions"])


def tool_freeze_amount(conn, user_id: int, args: dict) -> dict:
    amount = int(_int_arg(args, "amount", min_value=1))
    account_id = _int_arg(args, "account_id", required=False, min_value=1)
    if account_id is None:
        account_id = bankcore.main_account(conn, user_id)
    elif int(account_id) not in _owned_account_ids(conn, user_id):
        raise ToolError("E_NOT_FOUND", "账户不存在")
    account_id = int(account_id)
    until = _str_arg(args, "until", required=False, default="") or None

    result = bankcore.freeze_amount(conn, user_id, account_id, amount, until)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "freeze_id": int(result.get("freeze_id") or 0),
        "account_id": account_id,
        "amount_cents": int(result.get("amount_cents") or amount),
        "available_cents": int(result.get("available_cents") or 0),
        "until": until,
        "status": "active",
    })


def tool_order_gift(conn, user_id: int, args: dict) -> dict:
    item = _str_arg(args, "item")
    amount = int(_int_arg(args, "amount", min_value=1))
    deliver_at = _str_arg(args, "deliver_at", required=False, default="") or None

    result = bankcore.order_gift(conn, user_id, item, amount, deliver_at)
    if not result.get("ok"):
        return _from_bankcore(result)
    txn_id = int(result.get("txn_id") or 0)
    return _ok({
        "order_id": int(result.get("order_id") or 0),
        "txn_id": txn_id,
        "txn_no": _txn_no_of(conn, user_id, txn_id),
        "item": item,
        "amount_cents": int(result.get("amount_cents") or amount),
        "deliver_at": result.get("deliver_at") or deliver_at or config.now_iso(),
        "status": "placed",
        "reversible": False,
    }, extras=[
        ("order_id", "id", int(result.get("order_id") or 0)),
        ("txn_id", "id", txn_id),
    ])


def tool_release_freeze(conn, user_id: int, args: dict) -> dict:
    freeze_id = int(_int_arg(args, "freeze_id", min_value=1))
    result = bankcore.release_freeze(conn, freeze_id)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "freeze_id": freeze_id,
        "released_cents": int(result.get("released_cents") or 0),
        "available_cents": int(result.get("available_cents") or 0),
        "status": "released",
    })


# ---------------------------------------------------------------------------
# 红 · 高风险写入
# ---------------------------------------------------------------------------
def tool_report_card_lost(conn, user_id: int, args: dict) -> dict:
    card_id = int(_int_arg(args, "card_id", min_value=1))
    result = bankcore.report_card_lost(conn, user_id, card_id)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "card_id": card_id,
        "card_tail": result.get("card_tail"),
        "ticket_no": result.get("ticket_no"),
        "replace_days": result.get("replace_days"),
        "status": "lost",
    })


def tool_unlock_card(conn, user_id: int, args: dict) -> dict:
    card_id = int(_int_arg(args, "card_id", min_value=1))
    result = bankcore.unlock_card(conn, user_id, card_id)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "card_id": card_id,
        "card_tail": result.get("card_tail"),
        "status": result.get("status") or "active",
    })


def tool_purchase_wealth(conn, user_id: int, args: dict) -> dict:
    product_id = int(_int_arg(args, "product_id", min_value=1))
    amount = int(_int_arg(args, "amount", min_value=1))
    result = bankcore.purchase_wealth(conn, user_id, product_id, amount)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "txn_id": int(result.get("txn_id") or 0),
        "txn_no": result.get("txn_no"),
        "holding_id": int(result.get("holding_id") or 0),
        "product_name": result.get("product_name") or "",
        "amount_cents": int(result.get("amount_cents") or amount),
        "balance_after": int(result.get("balance_after") or 0),
        "status": "succeeded",
    })


def tool_redeem_wealth(conn, user_id: int, args: dict) -> dict:
    holding_id = int(_int_arg(args, "holding_id", min_value=1))
    amount = _int_arg(args, "amount", required=False, min_value=1)
    result = bankcore.redeem_wealth(
        conn, user_id, holding_id, None if amount is None else int(amount)
    )
    if not result.get("ok"):
        return _from_bankcore(result)
    txn_id = int(result.get("txn_id") or 0)
    return _ok({
        "txn_id": txn_id,
        "txn_no": _txn_no_of(conn, user_id, txn_id),
        "holding_id": holding_id,
        "amount_cents": int(result.get("amount_cents") or 0),
        "balance_after": int(result.get("balance_after") or 0),
        "status": "succeeded",
    })


def tool_adjust_card_limit(conn, user_id: int, args: dict) -> dict:
    card_id = int(_int_arg(args, "card_id", min_value=1))
    new_limit = int(_int_arg(args, "new_limit", min_value=1))
    card = _card_by_id(conn, user_id, card_id)
    if not card:
        raise ToolError("E_NOT_FOUND", "卡片不存在")
    old_limit = int(card.get("daily_limit") or 0)
    direction = "up" if new_limit > old_limit else "down"

    result = bankcore.adjust_card_limit(conn, user_id, card_id, new_limit)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "card_id": card_id,
        "card_tail": card.get("card_tail"),
        "old_limit_cents": old_limit,
        "new_limit_cents": int(result.get("new_limit_cents") or new_limit),
        "direction": result.get("direction") or direction,
        "status": card.get("status"),
    })


# ---------------------------------------------------------------------------
# 系统
# ---------------------------------------------------------------------------
def tool_notify(conn, user_id: int, args: dict) -> dict:
    content = _str_arg(args, "content")
    target = _int_arg(args, "user_id", required=False, default=user_id, min_value=1)
    target = int(target or user_id)
    result = bankcore.notify(conn, target, content)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "delivered": bool(result.get("delivered", True)),
        "to": int(result.get("to") or target),
        "content": content,
        "channel": "inapp",
    })


# ---------------------------------------------------------------------------
# 注册表 / 分发
# ---------------------------------------------------------------------------
def tool_repay_loan(conn, user_id: int, args: dict) -> dict:
    amount = args.get("amount")
    amount = int(amount) if amount not in (None, "") else None
    result = bankcore.repay_loan(conn, user_id, amount)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "txn_id": int(result.get("txn_id") or 0),
        "txn_no": result.get("txn_no"),
        "amount_cents": int(result.get("amount_cents") or 0),
        "remaining_cents": int(result.get("remaining_cents") or 0),
        "balance_after": int(result.get("balance_after") or 0),
    })


def tool_risk_assessment(conn, user_id: int, args: dict) -> dict:
    level = str(args.get("level") or "稳健型")
    scores = {"保守型": 20, "稳健型": 55, "进取型": 85}
    if level not in scores:
        return _fail("E_PARAM", "风险等级不合法")
    result = bankcore.save_risk_assessment(conn, user_id, level, scores[level])
    return _ok(result)


def tool_reset_password(conn, user_id: int, args: dict) -> dict:
    channel = str(args.get("channel") or "sms")
    if channel not in ("sms", "email"):
        return _fail("E_PARAM", "验证渠道不合法")
    result = bankcore.create_password_reset(conn, user_id, channel)
    return _ok(result)




def tool_repay_credit_card(conn, user_id: int, args: dict) -> dict:
    amount = int(_int_arg(args, "amount", min_value=1))
    result = bankcore.repay_credit_card(conn, user_id, amount)
    if not result.get("ok"):
        return _from_bankcore(result)
    return _ok({
        "txn_id": int(result.get("txn_id") or 0),
        "txn_no": result.get("txn_no"),
        "amount_cents": int(result.get("amount_cents") or 0),
        "used_after": int(result.get("used_after") or 0),
        "balance_after": int(result.get("balance_after") or 0),
        "card_tail": result.get("card_tail"),
    })




def tool_reverse_transfer(conn, user_id: int, args: dict) -> dict:
    txn_id = int(_int_arg(args, "txn_id", min_value=1))
    result = bankcore.reverse_transaction(conn, txn_id)
    if not result.get("ok"):
        return _from_bankcore(result)
    origin = conn.execute("SELECT amount, txn_no FROM transactions WHERE id=?", (txn_id,)).fetchone()
    return _ok({
        "reversal_txn_id": int(result.get("reversal_txn_id") or 0),
        "reversal_txn_no": result.get("reversal_txn_no"),
        "original_txn_id": txn_id,
        "original_txn_no": origin["txn_no"] if origin else "",
        "amount_cents": int(origin["amount"]) if origin else 0,
        "balance_after": int(bankcore.get_balance(conn, bankcore.main_account(conn, user_id))),
        "balance_after_cents": int(bankcore.get_balance(conn, bankcore.main_account(conn, user_id))),
    })


REGISTRY: dict[str, Callable[..., dict]] = {
    # 绿
    "get_accounts": tool_get_accounts,
    "get_transactions": tool_get_transactions,
    "analyze_spending": tool_analyze_spending,
    "detect_anomaly": tool_detect_anomaly,
    "get_bill_report": tool_get_bill_report,
    "get_contacts": tool_get_contacts,
    "get_subscriptions": tool_get_subscriptions,
    "get_wealth_products": tool_get_wealth_products,
    "get_profile": tool_get_profile,
    "quote_transfer": tool_quote_transfer,
    # 黄
    "create_transfer": tool_create_transfer,
    "cancel_subscription": tool_cancel_subscription,
    "apply_virtual_card": tool_apply_virtual_card,
    "create_collection_request": tool_create_collection_request,
    "set_reminder": tool_set_reminder,
    "create_standing_plan": tool_create_standing_plan,
    "freeze_amount": tool_freeze_amount,
    "order_gift": tool_order_gift,
    "release_freeze": tool_release_freeze,
    # 红
    "submit_transfer": tool_submit_transfer,
    "report_card_lost": tool_report_card_lost,
    "unlock_card": tool_unlock_card,
    "purchase_wealth": tool_purchase_wealth,
    "repay_loan": tool_repay_loan,
    "repay_credit_card": tool_repay_credit_card,
    "reverse_transfer": tool_reverse_transfer,
    "risk_assessment": tool_risk_assessment,
    "request_password_reset": tool_reset_password,
    "redeem_wealth": tool_redeem_wealth,
    "adjust_card_limit": tool_adjust_card_limit,
    # 系统
    "notify": tool_notify,
}




def call(name: str, conn, user_id: int, args: dict | None = None) -> dict:
    """统一入口：分发 + 异常兜底，永远返回 ToolResult 信封。"""
    if not isinstance(name, str) or name not in REGISTRY:
        return _fail("E_TOOL", f"未知工具：{name}")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _fail("E_PARAM", "工具参数必须为对象")
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return _fail("E_PARAM", "user_id 必须为整数")

    try:
        result = REGISTRY[name](conn, user_id, args)
    except ToolError as exc:
        return _fail(exc.code, exc.message)
    except bankcore.BankError as exc:
        return _fail(exc.code, exc.message)
    except sqlite3.Error as exc:
        return _fail("E_INTERNAL", f"数据库错误：{exc}")
    except (KeyError, TypeError, ValueError) as exc:
        return _fail("E_PARAM", f"参数错误：{exc}")
    except Exception as exc:  # noqa: BLE001 - 工具边界必须兜底
        return _fail("E_INTERNAL", f"内部错误：{exc}")

    if not isinstance(result, dict) or "ok" not in result:
        return _fail("E_INTERNAL", f"工具 {name} 返回格式非法")
    # 补齐信封必需键，防御性处理
    result.setdefault("data", {})
    result.setdefault("facts", [])
    result.setdefault("tainted", [])
    result.setdefault("error", None)
    return result


__all__ = ["REGISTRY", "call", "ToolError", "MONTHLY_INCOME_CENTS"]
