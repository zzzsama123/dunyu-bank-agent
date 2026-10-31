"""首页概览：余额、本月支出、下次扣费、待办计划、可用联系人。

给前端"功能面板/概览条"用，全部只读，不涉及任何资金操作。
"""

from __future__ import annotations

from . import bankcore, tools


def build(conn, user_id: int) -> dict:
    balance = bankcore.get_balance(conn, user_id)
    spend = tools.tool_analyze_spending(conn, user_id, {"range": "this_month"})
    spend_data = spend.get("data") or {}

    subs = [s for s in bankcore.list_subscriptions(conn, user_id) if s.get("status") == "active"]
    subs.sort(key=lambda s: str(s.get("next_charge_at") or "9999"))
    next_sub = subs[0] if subs else None

    plans = conn.execute(
        "SELECT id, goal, next_run_at FROM standing_plans WHERE user_id=? AND status='active' "
        "ORDER BY next_run_at LIMIT 1", (user_id,)
    ).fetchall()
    plan = dict(plans[0]) if plans else None

    contacts = [
        {"id": c["id"], "name": c["name"], "alias": c.get("alias")}
        for c in bankcore.list_contacts(conn, user_id)
    ]

    cats = spend_data.get("by_category") or []
    top = cats[0] if cats else None
    return {
        "balance_cents": int(balance),
        "month": {
            "label": spend_data.get("range_label") or "",
            "spend_cents": int(spend_data.get("total_cents") or 0),
            "count": int(spend_data.get("txn_count") or 0),
            "top_category": top,
        },
        "subscription": {
            "count": len(subs),
            "next": {"name": next_sub["merchant"], "amount_cents": int(next_sub["amount_cents"]),
                     "charge_at": str(next_sub.get("next_charge_at") or "")[:10]} if next_sub else None,
        },
        "plan": {
            "count": len(plans),
            "next": {"goal": plan.get("goal"), "run_at": str(plan.get("next_run_at") or "")[:10]}
            if plan else None,
        },
        "contacts": contacts,
    }
