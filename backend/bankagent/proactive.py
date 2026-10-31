"""主动服务：到期/扣费/还款/异常提醒。"""

from __future__ import annotations

from datetime import date, timedelta

from . import bankcore, config, tools


def _days_until(value: str) -> int | None:
    try:
        return (date.fromisoformat(str(value)[:10]) - config.now_dt().date()).days
    except (ValueError, TypeError):
        return None


def build(conn, user_id: int) -> dict:
    today = config.now_dt().date()
    items = []
    for sub in bankcore.list_subscriptions(conn, user_id):
        if sub.get("status") != "active":
            continue
        days = _days_until(sub.get("next_charge_at") or "")
        if days is not None and 0 <= days <= 7:
            items.append({
                "type": "subscription",
                "title": f"{sub.get('merchant')} 将于 {days} 天后扣费",
                "detail": f"{int(sub.get('amount') or 0) / 100:.2f} 元",
                "due_at": str(sub.get("next_charge_at"))[:10],
                "action": f"{sub.get('merchant')} 多少钱",
            })
    for plan in bankcore.list_standing_plans(conn, user_id):
        if plan.get("status") != "active":
            continue
        days = _days_until(plan.get("next_run_at") or "")
        if days is not None and 0 <= days <= 7:
            items.append({
                "type": "plan",
                "title": f"计划「{plan.get('goal')}」{days} 天后触发",
                "detail": "长期计划",
                "due_at": str(plan.get("next_run_at"))[:10],
                "action": "我的长期计划",
            })
    if (today + timedelta(days=7)).isoformat() >= "2026-11-15":
        items.append({
            "type": "repayment",
            "title": "房贷 11-15 还款",
            "detail": "月供 7,800.00 元",
            "due_at": "2026-11-15",
            "action": "我的还款计划",
        })
    try:
        anomaly = tools.detect_anomaly(conn, user_id, {"range": "last_30d"})
        for row in (anomaly.get("data") or {}).get("items", [])[:2]:
            items.append({
                "type": "anomaly",
                "title": f"异常交易：{row.get('counterparty')} {int(row.get('amount_cents') or 0) / 100:.2f} 元",
                "detail": row.get("reason") or "",
                "due_at": str(row.get("created_at"))[:10],
                "action": "有没有乱扣费",
            })
    except Exception:  # noqa: BLE001
        pass
    return {"items": items, "total": len(items), "generated_at": config.now_iso()}


def summary(conn, user_id: int) -> str | None:
    data = build(conn, user_id)
    if not data["items"]:
        return "今天没有需要处理的提醒。"
    lines = [f"· {item['title']}（{item['detail']}）" for item in data["items"][:6]]
    return "今天有 {} 件事需要留意：\n{}".format(len(data["items"]), "\n".join(lines))
