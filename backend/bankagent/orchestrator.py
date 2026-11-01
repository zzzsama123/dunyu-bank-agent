"""编排器：一次对话的完整状态机。

ingest → nlu → clarify/plan → validate → policy → confirm/stepup → execute
       → compensate → render → postcheck → audit
"""

from __future__ import annotations

import difflib
import hashlib
import hmac
import calendar
import json
import re
import secrets
import time
from datetime import timedelta

from . import bankcore, clarify, commands, conditional, config, dialogue, executor, identity, insight, knowledge, lexicon, llm, nlu, phonetic, planner, policy, proactive, semantic, specs, tools, trust, understanding, validator

LEVEL_RANK = {"green": 0, "yellow": 1, "red": 2, "deny": 3}

TOOL_LABEL = {
    "get_accounts": "查询账户", "get_transactions": "查流水", "analyze_spending": "分类统计",
    "detect_anomaly": "异常识别", "get_bill_report": "生成账单报告", "get_contacts": "查找联系人",
    "get_subscriptions": "查订阅", "get_wealth_products": "查理财", "get_profile": "读用户档案",
    "quote_transfer": "转账试算", "create_transfer": "小额转账", "submit_transfer": "大额转账",
    "cancel_subscription": "取消订阅", "apply_virtual_card": "申请虚拟卡",
    "create_collection_request": "发起 AA 收款", "set_reminder": "设置提醒",
    "create_standing_plan": "创建长期计划", "freeze_amount": "锁定资金", "release_freeze": "解除冻结",
    "order_gift": "商户下单", "report_card_lost": "卡片挂失", "unlock_card": "解除挂失",
    "purchase_wealth": "理财申购", "redeem_wealth": "理财赎回", "adjust_card_limit": "调整额度",
    "notify": "发送通知",
}

SANITY_AMOUNT_REPLY = ("这个金额看起来不对，我不确定是你要的。\n"
                       "你是想说「还房贷」，还是「算一下月供」？")


CONFIRM_TTL = config.CONFIRM_TTL_SECONDS
STEPUP_TTL = config.STEPUP_TTL_SECONDS


def _ev(events: list, typ: str, **data) -> None:
    events.append({"seq": len(events) + 1, "type": typ, "data": data, "ts": config.now_iso()})


def _hash_params(params: dict) -> str:
    raw = json.dumps(params, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:32]


def _money(cents) -> str:
    return trust.money(int(cents))


def _plan_summary(plan: dict) -> str:
    parts = [TOOL_LABEL.get(n["tool"], n["tool"]) for n in executor.topo_order(plan)]
    return " → ".join(parts)


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _plan_outflow(plan: dict) -> int:
    total = 0
    for n in plan.get("nodes", []):
        flow = specs.flow_of(n["tool"])
        args = n.get("args") or {}
        amount = _int(args.get("amount") or args.get("amount_each") or 0)
        if flow == "out":
            total += amount
        elif flow == "in":
            total -= amount
    return max(total, 0)


CRITICAL_MISSING = {"amount", "payee", "merchant", "product", "budget"}


def _contact_variants(name: str) -> list[str]:
    """「我妈」「给我妈」这类口语，去掉前缀再查一次。"""
    out = [name]
    for p in ("我", "给", "帮", "向", "转给"):
        out.append(name.replace(p, ""))
    return [x for x in dict.fromkeys(out) if x]


def _contact_of(conn, user_id: int, plan: dict) -> dict | None:
    """解析计划里的收款人：优先 contact_id，其次按 payee/alias 模糊查。"""
    for n in plan.get("nodes", []):
        args = n.get("args") or {}
        cid = args.get("contact_id")
        if isinstance(cid, int):
            row = conn.execute("SELECT * FROM contacts WHERE id=? AND user_id=?", (cid, user_id)).fetchone()
            if row:
                return dict(row)
        name = args.get("payee") or args.get("alias") or args.get("name")
        if isinstance(name, str) and name and not name.startswith("$"):
            for variant in _contact_variants(name):
                rows = bankcore.find_contact(conn, user_id, variant)
                if len(rows) == 1:
                    return rows[0]
            for c in bankcore.list_contacts(conn, user_id):
                if phonetic.same_sound(name, str(c.get("name") or "")) or \
                        phonetic.same_sound(name, str(c.get("alias") or "")):
                    return c
    return None


def _card_of(conn, user_id: int, plan: dict) -> dict | None:
    for n in plan.get("nodes", []):
        cid = (n.get("args") or {}).get("card_id")
        if isinstance(cid, int):
            row = conn.execute("SELECT * FROM cards WHERE id=? AND user_id=?", (cid, user_id)).fetchone()
            if row:
                return dict(row)
    row = conn.execute("SELECT * FROM cards WHERE user_id=? ORDER BY id LIMIT 1", (user_id,)).fetchone()
    return dict(row) if row else None


def _node_action(conn, user_id: int, node: dict) -> str:
    if node["tool"] == "adjust_card_limit":
        card = _card_of(conn, user_id, {"nodes": [node]})
        new_limit = int((node.get("args") or {}).get("new_limit") or 0)
        if card and new_limit > int(card["daily_limit"] or 0):
            return "adjust_card_limit_up"
        return "adjust_card_limit_down"
    return node["tool"]


CANCEL_HINTS = ("退掉", "退订", "取消", "关掉", "不想要", "停掉", "退了")
QUERY_WORDS = ("哪些", "有什么", "多少", "查一下", "看看", "列表", "明细", "什么会员")


def _resolve_awaiting(conn, session: dict, text: str) -> dict | None:
    """上一轮问了"想取消哪一项"，这一轮用户只回一个名字/序号，也要能接住。"""
    entities = json.loads(session.get("recent_entities") or "{}")
    awaiting = entities.get("awaiting") or {}
    if awaiting.get("kind") != "subscription_choice":
        return None
    merchants = [m for m in (awaiting.get("merchants") or []) if m]
    hit = next((m for m in merchants if m in text), None)
    if not hit:
        m = re.search(r"第\s*([1-9])\s*个", text)
        if m and int(m.group(1)) <= len(merchants):
            hit = merchants[int(m.group(1)) - 1]
    if not hit:
        return None
    return {"name": "subscription", "slots": {"action": "cancel", "merchant": hit},
            "missing": [], "ambiguity": [], "spans": {"merchant": hit}, "confidence": 0.95}


def _entity_intent(conn, user_id: int, text: str) -> dict | None:
    """实体优先兜底：话里出现某个订阅商户全名时，即使没有"会员/订阅"字样也认得。"""
    for s in bankcore.list_subscriptions(conn, user_id):
        if s.get("status") != "active":
            continue
        m = s.get("merchant") or ""
        if m and m in text:
            action = "cancel" if any(h in text for h in CANCEL_HINTS) else "query"
            return {"name": "subscription", "slots": {"action": action, "merchant": m},
                    "missing": [], "ambiguity": [], "spans": {"merchant": m}, "confidence": 0.9}
    return None


def _enrich_plan(conn, user_id: int, intent: dict, plan: dict, text: str) -> dict:
    """订阅场景补全 + 长期计划的语义纠偏（生日/纪念日必须用 birthday 触发器）。"""
    if intent.get("name") == "standing_plan":
        node = next((n for n in plan.get("nodes", []) if n["tool"] == "create_standing_plan"), None)
        if node is not None:
            args = node.setdefault("args", {})
            if "生日" in text or "纪念日" in text:
                args["trigger"] = {
                    "type": "birthday" if "生日" in text else "anniversary",
                    "days_before": 2, "time": "09:00",
                }
                goal = str(args.get("goal") or "")
                if not goal or "长期" in goal or "转账" in goal:
                    args["goal"] = "生日准备" if "生日" in text else "纪念日安排"
                if not plan.get("goal") or "长期" in str(plan.get("goal")):
                    plan["goal"] = args["goal"]
        return plan

    if intent.get("name") != "subscription":
        return plan
    slots = intent.get("slots") or {}
    subs = [s for s in bankcore.list_subscriptions(conn, user_id) if s.get("status") == "active"]
    hint = str(slots.get("merchant") or slots.get("name") or "")
    quoted = any(m in text for s in subs for m in [s.get("merchant") or ""] if m)
    bare_choose = (len(text.strip()) <= 12 and quoted and not any(w in text for w in QUERY_WORDS))
    wants_cancel = slots.get("action") == "cancel" or any(h in text for h in CANCEL_HINTS) or bare_choose
    if not wants_cancel:
        return plan
    target = None
    for s in subs:
        m = s.get("merchant") or ""
        if (hint and (hint in m or m in hint)) or (m and m in text):
            target = s
            break
    if not target:
        return plan
    return {
        "plan_id": plan.get("plan_id") or ("pl_" + secrets.token_hex(4)),
        "goal": f"取消订阅 {target['merchant']}",
        "intent": "subscription",
        "nodes": [{
            "id": "n1", "tool": "cancel_subscription",
            "args": {"sub_id": int(target["id"]), "merchant": target["merchant"]},
            "provenance": {"sub_id": "tool"}, "depends_on": [], "risk_hint": "yellow",
        }],
        "confirm_points": ["n1"],
    }


def _missing_payee(conn, user_id: int, plan: dict) -> str | None:
    """转账类计划但收款人解析不出来时，返回用户说的那个名字（用于澄清）。"""
    nodes = [n for n in plan.get("nodes", [])
             if n["tool"] in ("quote_transfer", "create_transfer", "submit_transfer")]
    if not nodes:
        return None
    if _contact_of(conn, user_id, plan):
        return None
    for n in nodes:
        for k in ("payee", "alias", "name"):
            v = (n.get("args") or {}).get(k)
            if isinstance(v, str) and not v.startswith("$"):
                return v
    return ""


TRANSFER_ACTIONS = {"create_transfer", "submit_transfer", "create_collection_request"}
CARD_ACTIONS_SET = {"report_card_lost", "unlock_card", "adjust_card_limit", "apply_virtual_card"}


TRIGGER_TYPES = {"birthday", "anniversary", "monthly", "weekly", "daily", "once"}


def _ground_product(conn, text: str, intent: dict) -> dict:
    """从"买5000稳盈90天"里认出产品，补 product_id 并清掉 missing。"""
    if intent.get("name") != "wealth":
        return intent
    slots = intent.get("slots") if isinstance(intent.get("slots"), dict) else {}
    if slots.get("product_id") or not (slots.get("action") == "purchase"):
        return intent
    flat = re.sub(r"\s+", "", text or "")
    rows = conn.execute("SELECT id, name FROM wealth_products ORDER BY id").fetchall()
    for row in rows:
        name = re.sub(r"\s+", "", row["name"] or "")
        if name and name in flat:
            slots["product_id"] = int(row["id"])
            slots["product"] = row["name"]
            intent["slots"] = slots
            intent["missing"] = [m for m in (intent.get("missing") or []) if m != "product"]
            return intent
    return intent

def _ground_payee(conn, user_id: int, text: str, intent: dict) -> dict:
    """把话里出现的联系人（别名/姓名）补进 slots.payee——模型常常只给 when/budget 就漏了人。"""
    slots = intent.get("slots") if isinstance(intent.get("slots"), dict) else {}
    if slots.get("payee"):
        return intent
    for c in bankcore.list_contacts(conn, user_id):
        for token in (c.get("alias"), c.get("name")):
            if token and str(token) in text:
                slots["payee"] = token
                intent["slots"] = slots
                return intent
    for c in bankcore.list_contacts(conn, user_id):
        for token in (c.get("alias"), c.get("name")):
            if token and len(str(token)) >= 2 and phonetic.contains_sound(text, str(token)):
                slots["payee"] = token
                intent["slots"] = slots
                return intent
    return intent


def _plan_matches_intent(intent: dict, plan: dict) -> bool:
    """模型的计划必须真的"办那件事"：用户要转账，计划里就得有转账工具。"""
    name = intent.get("name")
    slots = intent.get("slots") or {}
    tools = {n["tool"] for n in plan.get("nodes", [])}
    if name == "transfer" and not (tools & TRANSFER_ACTIONS):
        return False
    if name == "bill_analysis" and str(slots.get("focus")) == "transactions":
        if "get_transactions" not in tools:
            return False
        if "analyze_spending" in tools:
            return False          # 流水查询不应该再做支出分析
    if name == "bill_analysis" and str(slots.get("focus")) == "anomaly":
        if "detect_anomaly" not in tools or "analyze_spending" in tools:
            return False          # 可疑扣费应只做异常识别
    if name == "standing_plan":
        if not (tools & {"create_standing_plan", "set_reminder"}):
            return False
        node = next((n for n in plan["nodes"] if n["tool"] == "create_standing_plan"), None)
        if node is not None:
            trig = (node.get("args") or {}).get("trigger") or {}
            known = isinstance(trig, dict) and (
                trig.get("at") or str(trig.get("type") or "") in TRIGGER_TYPES)
            if not known:
                return False          # 触发条件不明确（如 schedule/{}）→ 交回规则规划器
            if (slots.get("payee")) and "get_contacts" not in tools:
                return False          # 说了"老婆/妈"却没查联系人 → 计划不完整
    if name == "subscription" and str(slots.get("action")) == "cancel" and "cancel_subscription" not in tools:
        return False
    if name == "card":
        action = str(slots.get("action") or "")
        need = {"lost": "report_card_lost", "unlock": "unlock_card",
                "limit": "adjust_card_limit", "apply": "apply_virtual_card"}.get(action)
        if need and need not in tools:
            return False
    if name == "wealth" and str(slots.get("action")) in ("purchase", "redeem"):
        need = "purchase_wealth" if slots.get("action") == "purchase" else "redeem_wealth"
        if need not in tools:
            return False
    return True


def _policy_ctx(conn, user_id: int, plan: dict, node: dict | None, session: dict,
                plan_outflow: int, batch_count: int) -> dict:
    entities = json.loads(session.get("recent_entities") or "{}")
    last_stepup = entities.get("last_stepup") or {}
    contact = _contact_of(conn, user_id, {"nodes": [node]} if node else plan)
    amount = _int((node.get("args") or {}).get("amount")
                  or (node.get("args") or {}).get("amount_each") or 0) if node else 0
    return {
        "action": _node_action(conn, user_id, node) if node else "get_accounts",
        "amount": amount,
        "daily_used": bankcore.get_daily_used(conn, user_id),
        "payee_is_new": bool(contact and contact.get("is_new")),
        "payee_id": str(contact.get("id")) if contact else None,
        "channel": session.get("channel") or "app",
        "hour": config.now_dt().hour,
        "new_device": False,
        "risk_level": "normal",
        "risk_score": int(bankcore.get_user(conn, user_id).get("risk_score") or 10),
        "plan_outflow": plan_outflow,
        "batch_count": batch_count,
        "stepup_age_seconds": config.seconds_since(last_stepup.get("at")),
        "stepup_payee": last_stepup.get("payee"),
        "session_state": (session.get("state") or "NORMAL").lower(),
    }


def evaluate(conn, user_id: int, plan: dict, session: dict) -> dict:
    outflow = _plan_outflow(plan)
    batch = sum(1 for n in plan["nodes"] if n["tool"] == "cancel_subscription")
    best = None
    for node in plan["nodes"]:
        ctx = _policy_ctx(conn, user_id, plan, node, session, outflow, batch)
        verdict = policy.decide(ctx)
        verdict["node_id"] = node["id"]
        verdict["tool"] = node["tool"]
        if best is None:
            best = {"level": verdict.get("level", "deny"), "rule_id": verdict.get("rule_id", "R-DEFAULT"),
                    "reason": verdict.get("reason", ""), "required_factors": verdict.get("required_factors", []),
                    "nodes": [verdict]}
            continue
        best["nodes"].append(verdict)
        if LEVEL_RANK.get(verdict.get("level"), 3) > LEVEL_RANK.get(best["level"], 0):
            best.update({k: verdict.get(k) for k in ("level", "rule_id", "reason", "required_factors")})
    if best is None:
        best = {"level": "deny", "rule_id": "R-DEFAULT", "reason": "空计划", "required_factors": [], "nodes": []}
    return best


CARD_ACTIONS = {"report_card_lost", "unlock_card", "adjust_card_limit", "apply_virtual_card"}


def _confirm_params(conn, user_id: int, plan: dict, level: dict, intent: dict | None = None) -> dict:
    """确认卡内容按"动作语义"组织：转账才说收款人，长期计划说预算与触发时间。

    同时给出 `fields`（label/value 列表），前端优先按它渲染，避免把"关联人"
    错标成"收款人"、把"计划触发"错标成"到账时间"。
    """
    contact = _contact_of(conn, user_id, plan)
    if contact is None and intent:
        payee = str(((intent.get("slots") or {}).get("payee")) or "")
        for variant in _contact_variants(payee):
            rows = bankcore.find_contact(conn, user_id, variant)
            if len(rows) == 1:
                contact = rows[0]
                break
    card = _card_of(conn, user_id, plan)
    action_node = next((n for n in reversed(plan["nodes"]) if specs.flow_of(n["tool"]) == "out"), None)
    if action_node is None:
        action_node = next((n for n in reversed(plan["nodes"])
                            if n["tool"] in ("freeze_amount", "create_standing_plan", "cancel_subscription",
                                             "report_card_lost", "unlock_card", "adjust_card_limit",
                                             "set_reminder", "apply_virtual_card")), plan["nodes"][-1])
    args = action_node.get("args") or {}
    tool = action_node["tool"]
    amount = _int(args.get("amount") or args.get("amount_each") or args.get("budget") or 0)
    base = {"action": tool, "level": level["level"], "amount": _money(amount),
            "goal": plan.get("goal", "")}

    # 1) 转账
    if tool in ("create_transfer", "submit_transfer") and contact:
        base.update({
            "payee_name": contact["name"],
            "payee_tail": contact.get("card_tail"),
            "arrival": "实时",
            "amount_label": "转账金额",
            "fields": [["收款人", contact["name"]],
                       ["收款账户", f"尾号 {contact.get('card_tail') or '执行时核验'}"],
                       ["到账时间", "实时"]],
        })
        if args.get("memo"):
            base["fields"].append(["用途", str(args["memo"])[:30]])
        daily = bankcore.get_daily_used(conn, user_id)
        if daily >= 100_000:
            base["fields"].append(["提示", f"今日免密额度已用完（已用 {_money(daily)} / {_money(100_000)} 元），后续转账需强验证"])
        elif daily + amount > 90_000 and amount <= 50_000:
            base["fields"].append(["提示", f"再转 {_money(max(0, 100_000 - daily))} 元将触发强验证"])
        note = _transfer_risk_note(conn, user_id, contact, amount)
        if note:
            base["risk_note"] = note
            base["fields"].append(["风险提示", note])
        return base

    # 2) AA 收款
    if tool == "create_collection_request":
        base.update({
            "amount_label": "人均金额",
            "fields": [["收款方式", "向好友发起 AA 收款（资金流入）"],
                       ["人均", f"{_money(amount)} 元"]],
        })
        return base

    # 3) 长期计划 / 锁预算
    if tool in ("freeze_amount", "create_standing_plan"):
        trigger = args.get("trigger") if isinstance(args.get("trigger"), dict) else {}
        try:
            run_at = bankcore._next_run_at(conn, user_id, trigger) if trigger else None
        except Exception:  # noqa: BLE001
            run_at = None
        schedule = run_at.replace("T", " ")[:16] if run_at else "待定"
        fields = []
        if contact:
            fields.append(["关联人", contact["name"]])
        is_reminder = str(trigger.get("type") or "") == "reminder" or not args.get("budget")
        if is_reminder and tool == "create_standing_plan":
            fields.append(["提醒时间", schedule])
            fields.append(["提醒内容", plan.get("goal") or args.get("goal") or "定时提醒"])
            base.update({"amount_label": "提醒", "kind": "reminder", "fields": fields})
            base.pop("amount", None)
            return base
        fields.append(["计划触发", schedule])
        fields.append(["用途", plan.get("goal") or args.get("goal") or "长期计划"])
        base.update({"amount_label": "锁定预算", "fields": fields})
        return base

    # 4) 办理闭环：还款 / 风险测评 / 密码重置
    if tool == "redeem_wealth":
        holding = args.get("holding_id")
        holding_row = conn.execute(
            "SELECT amount FROM holdings WHERE id=? AND user_id=?", (holding, user_id)).fetchone() if holding else None
        if holding_row:
            base["amount"] = _money(int(holding_row["amount"]))
        base.update({"amount_label": "赎回金额",
                     "fields": [["持仓", f"第 {holding} 笔" if holding else "按持仓"],
                                ["到账", "T+1 个工作日（演示）"],
                                ["确认后", "不可撤回"]]})
        return base
    if tool == "schedule_transfer":
        run_at = str(args.get("run_at") or "")
        base.update({"amount_label": "预约金额",
                     "fields": [["收款人", contact["name"] if contact else str(args.get("payee") or "")],
                                ["执行时间", run_at.replace("T", " ")[:16]],
                                ["说明", "到点前可在审计/计划里取消；现在不扣款"]]})
        return base
    if tool == "reverse_transfer":
        txn_id = _int(args.get("txn_id"))
        row = conn.execute("SELECT * FROM transactions WHERE id=? AND user_id=?",
                           (txn_id, user_id)).fetchone()
        base.update({"amount_label": "撤销金额",
                     "fields": [["原交易", str(row["txn_no"]) if row else f"#{txn_id}"],
                                ["金额", f"{_money(int(row['amount']) if row else 0)} 元"],
                                ["说明", "反向分录，原交易标记为已冲正"]]})
        return base
    if tool == "repay_credit_card":
        cc = bankcore.credit_card_view(conn, user_id)
        base.update({"amount_label": "还款金额",
                     "fields": [["还款对象", f"信用卡（尾号 {cc['card_tail'] if cc else '6688'}）"],
                                ["还款方式", "从活期账户扣款"],
                                ["还款后", "信用卡已用额度相应减少"],
                                ["提示", "还款后额度恢复，请核对账单"]]})
        if cc and cc.get("status") == "lost":
            base["fields"].append(["卡片状态", "该卡当前已挂失，仅支持还款"])
        return base
    if tool == "repay_loan":
        amount = _int(args.get("amount")) or 780_000
        base["amount"] = _money(amount)
        base.update({"amount_label": "还款金额",
                     "fields": [["本次还款金额", f"{_money(amount)} 元"],
                                ["还款对象", "房贷（演示）"],
                                ["还款方式", "从活期账户扣款"],
                                ["剩余本金", "还款后更新"]]})
        return base
    if tool == "risk_assessment":
        level = str(args.get("level") or "稳健型")
        base.pop("amount", None)
        base.update({"amount_label": "测评结果", "kind": "risk",
                     "fields": [["测评结果", level], ["有效期", "12 个月"],
                                ["用途", "理财适当性匹配"]]})
        return base
    if tool == "request_password_reset":
        channel = "邮箱" if str(args.get("channel")) == "email" else "短信"
        base.pop("amount", None)
        base.update({"amount_label": "验证方式", "kind": "password",
                     "fields": [["验证方式", channel + "一次性链接"],
                                ["有效期", "15 分钟"],
                                ["安全说明", "对话中不收集新密码"]]})
        return base

    # 5) 取消订阅
    if tool in CARD_ACTIONS and card:
        op = {"report_card_lost": "挂失", "unlock_card": "解除挂失",
              "adjust_card_limit": "调整额度", "apply_virtual_card": "申请虚拟卡"}.get(tool, "卡片操作")
        if tool == "apply_virtual_card":
            base.update({"amount_label": "虚拟卡",
                         "fields": [["业务", "申请虚拟卡"],
                                    ["开卡方式", "实时开卡"],
                                    ["额度", "按默认额度，可在开卡后调整"],
                                    ["提示", "虚拟卡仅用于线上小额消费"]]})
            return base
        base.update({"card_tail": card["card_tail"], "amount_label": "调整后额度",
                     "fields": [["卡片", f"尾号 {card['card_tail']}"], ["操作", op]]})
        if tool == "adjust_card_limit":
            base["fields"].append(["调整后额度", f"{_money(_int(args.get('new_limit')))} 元"])
        if tool == "report_card_lost":
            base["fields"].append(["提示", "挂失不可撤销，补卡需 3–5 个工作日"])
        return base

    # 6) 理财申购
    if tool == "purchase_wealth":
        base.update({"amount_label": "申购金额",
                     "fields": [["产品", str(args.get("product") or plan.get("goal") or "理财产品")],
                                ["申购金额", f"{_money(amount)} 元"],
                                ["确认后", "不可撤单"],
                                ["提示", "非保本，历史收益不代表未来"]]})
        return base

    # 7) 兜底
    base["fields"] = [["业务", TOOL_LABEL.get(tool, tool)]]
    if tool == "cancel_subscription":
        base["fields"].append(["提示", "取消后当前周期仍可用，下期不再扣费"])
    if not amount:
        base.pop("amount", None)
    return base


def _maybe_transaction_query(conn, user_id: int, text: str) -> dict | None:
    """确定性识别"查某人给我的/我转出的流水"——这类查询 LLM 容易误判成账单分析。"""
    t = (text or "").strip()
    has_verb = bool(re.search(r"(查询|查一下|查|看一下|看看|找一下|列出|给我看)", t))
    has_noun = bool(re.search(r"(转账|流水|记录|明细|交易)", t))
    if not has_noun:
        return None
    if re.search(r"上周", t):
        return None  # 交给 Agent 循环：先查 7 天，空则自动扩大范围

    if not has_verb and not re.search(r"(流水|明细|最近\s*[0-9一二三四五六七八九十]+\s*笔)", t):
        return None
    if re.search(r"(转账|打钱|转)\s*\d", t) and "记录" not in t and "查询" not in t:
        return None
    direction = None
    if re.search(r"(我转给|转出|支出|付给|打给)", t):
        direction = "out"
    elif re.search(r"(给我的|转给我|打给我|收到|转入|进账|来账)", t):
        direction = "in"
    range_key = "this_month"
    if re.search(r"(最近|近)\s*3\s*个?月|三个月", t):
        range_key = "last_3m"
    elif re.search(r"(最近|近)\s*7\s*天|一周", t):
        range_key = "last_7d"
    elif re.search(r"上个月|上月", t):
        range_key = "last_month"
    elif re.search(r"(今年|本年)", t):
        range_key = "this_year"
    counterparty = None
    for c in bankcore.list_contacts(conn, user_id):
        for token in (c.get("alias"), c.get("name")):
            if token and str(token) in t:
                counterparty = str(token)
                break
        if counterparty:
            break
    slots = {"focus": "transactions", "range": range_key}
    if direction:
        slots["direction"] = direction
    if counterparty:
        slots["counterparty"] = counterparty
    return {"name": "bill_analysis", "slots": slots, "missing": [], "ambiguity": [],
            "spans": {"range": range_key}, "confidence": 0.95, "source": "rules_query"}

def _command_intent(conn, user_id: int, text: str, ctx: dict, audit, trace_id: str) -> dict | None:
    """模型先理解：把话编译成命令码；失败/非法则返回 None 交回确定性层。"""
    if config.AGENT_MODE != "model" or not llm.enabled():
        return None
    t0 = time.perf_counter()
    cmd, meta = llm.parse_command(text, ctx)
    ms = int((time.perf_counter() - t0) * 1000)
    if not cmd:
        audit.append(trace_id, actor="agent", action="command.parsed",
                     payload={"ok": False, "ms": ms, **meta}, decision="fallback")
        return None
    code = str(cmd.get("cmd") or "")
    if code == "none":
        reason = str(cmd.get("reason") or "unclear")
        audit.append(trace_id, actor="agent", action="command.parsed",
                     payload={"cmd": "none", "reason": reason, "ms": ms}, decision="refused")
        if reason != "out_of_scope":
            return None      # 听不清时先让确定性层试一次，别把熟悉说法也丢给候选
        return {"name": "unknown", "slots": {}, "missing": [], "ambiguity": ["out_of_scope"],
                "spans": {}, "confidence": 0.3, "source": "command"}
    intent = commands.to_intent(cmd)
    audit.append(trace_id, actor="agent", action="command.parsed",
                 payload={"cmd": code, "args": cmd.get("args"), "ms": ms,
                          "ok": intent is not None},
                 decision="ok" if intent else "invalid")
    return intent

_SUB_MONEY = re.compile(r"(多少钱|价格|费用|月费|扣多少|扣费|收费|贵不贵)")


def _match_subscription(text: str, subs: list[dict]) -> dict | None:
    """按名字模糊匹配（容忍"云储存/云存储"这类错字）。"""
    norm = (text or "").replace("贮存", "存储").replace("储存", "存储")
    norm = re.sub(r"(多少钱|价格|费用|月费|扣多少|扣费|收费|原来|现在|是|的|吗|呢|？|\?)", "", norm).strip()
    best, best_score = None, 0.0
    for s in subs:
        name = str(s.get("merchant") or "")
        if not name:
            continue
        if name in text or name in norm:
            return s
        score = difflib.SequenceMatcher(None, name, norm).ratio()
        if score > best_score:
            best, best_score = s, score
    if best_score >= 0.45:
        return best
    # 同音兜底：李那 → 李娜
    for s in subs:
        name = str(s.get("merchant") or "")
        if name and phonetic.ratio(name, norm) >= 0.8:
            return s
    return None


def _maybe_wealth_query(conn, user_id: int, text: str) -> str | None:
    """理财/持仓/收益类询问：直接给持仓与在售产品，不空回"操作已完成"。"""
    t = text or ""
    if not re.search(r"(理财|基金|持仓|收益)", t):
        return None
    if re.search(r"(买|申购|转入|投)", t) and not re.search(r"(买的|买过|赚|收益|持仓)", t):
        return None
    if not re.search(r"(收益|持仓|多少|查|看|有哪些|什么|情况|我的理财|赎回)", t):
        return None
    if re.search(r"赎回", t) and not re.search(r"(持仓|第\s*\d|全部|所有)", t):
        holds = conn.execute(
            "SELECT h.id, h.amount, p.name FROM holdings h LEFT JOIN wealth_products p "
            "ON p.id=h.product_id WHERE h.user_id=? ORDER BY h.id", (user_id,)).fetchall()
        if holds:
            detail = "、".join(f"第{h['id']}笔 {h['name'] or '理财'} {_money(int(h['amount']))} 元"
                              for h in holds)
            return f"你要赎回哪一笔？当前持仓：{detail}。请说「赎回第1笔」或「全部赎回」。"
        return "你当前没有可赎回的理财持仓。"

    products = conn.execute(
        "SELECT name, rate, term_days, risk_level, min_amount FROM wealth_products ORDER BY id"
    ).fetchall()
    holds = conn.execute(
        "SELECT h.amount, p.name FROM holdings h LEFT JOIN wealth_products p "
        "ON p.id = h.product_id WHERE h.user_id = ? ORDER BY h.id", (user_id,)
    ).fetchall()
    parts = []
    if holds:
        total = sum(int(h["amount"]) for h in holds)
        detail = "、".join(f"{h['name'] or '理财'} {_money(int(h['amount']))} 元" for h in holds)
        parts.append(f"你的理财持仓共 {len(holds)} 笔，合计 {_money(total)} 元（{detail}）。")
    else:
        parts.append("你当前没有理财持仓。")
    if "收益" in t:
        parts.append("历史收益需要逐日净值，演示环境未接入；可先看当前持仓。")
    top = products[:3]
    if top:
        desc = "、".join(
            f"{p['name']}（年化 {round(float(p['rate']) * 100, 1)}%，{p['term_days']} 天）"
            for p in top
        )
        parts.append(f"在售 {len(products)} 只，例如：{desc}。")
    return "".join(parts)

def _maybe_balance_query(conn, user_id: int, text: str) -> str | None:
    """问"卡里还有多少钱/余额"：直接回答可用余额，而不是列卡。"""
    t = text or ""
    if not re.search(r"(余额|卡里|账户里|还有多少钱|剩多少钱|有多少钱|余额宝)", t):
        return None
    if re.search(r"(卡有哪些|几张卡|卡号|额度)", t):
        return None
    acc = bankcore.main_account(conn, user_id)
    total = bankcore.get_balance(conn, acc)
    frozen = bankcore.frozen_total(conn, acc) + int(
        conn.execute("SELECT frozen FROM accounts WHERE id=?", (acc,)).fetchone()["frozen"] or 0)
    avail = total - frozen
    if frozen > 0:
        return (f"当前可用余额 {_money(avail)} 元（总余额 {_money(total)} 元，"
                f"其中 {_money(frozen)} 元已被计划锁定）。")
    return f"当前可用余额 {_money(avail)} 元。"

def _maybe_subscription_detail(conn, user_id: int, text: str) -> dict | None:
    """问"某个订阅多少钱/什么时候扣"：直接答价格与下次扣费，不再甩一张取消列表。"""
    if not _SUB_MONEY.search(text or ""):
        return None
    subs = bankcore.list_subscriptions(conn, user_id)
    sub = _match_subscription(text, subs)
    if not sub:
        return None
    status = "正常" if sub.get("status") == "active" else str(sub.get("status") or "")
    next_at = str(sub.get("next_charge_at") or "")[:10] or "——"
    reply = (f"{sub['merchant']}：每月 {_money(int(sub['amount_cents']))} 元，"
             f"下次扣费 {next_at}，状态{status}。\n"
             f"要退掉吗？说「退掉{sub['merchant']}」就行。")
    return {"reply": reply, "merchant": sub["merchant"], "amount_cents": int(sub["amount_cents"])}

_WRITE_HINT = re.compile(r"(转|打|汇|退|取消|挂失|解挂|额度|申购|赎回|买|锁|计划|提醒|安排|还|房贷|信用卡)")
_QUERY_HEAD = r"(?:查询|查一下|查查|查|看看|看一下|看)"


def _split_compound(text: str) -> tuple[str, str] | None:
    """把“先做 A，然后/再/接着 B”拆成两件事；A、B 谁先谁后都保留。"""
    m = re.search(r"^(.*?)(?:，|,)?\s*(?:并|然后|接着|随后|顺便|同时|再)\s*(.+)$", text or "")
    if not m:
        return None
    first = re.sub(r"^先\s*", "", m.group(1).strip())
    second = m.group(2).strip()
    if len(first) < 2 or len(second) < 2:
        return None
    if not (_WRITE_HINT.search(first) or _WRITE_HINT.search(second)):
        return None
    return first, second


def _run_pending_task(conn, session_id: str, out: dict) -> dict:
    """第一件事完成后，自动执行记下的第二件事，并把结果拼到回复里。"""
    sess = bankcore.get_session(conn, session_id) or {}
    ents = json.loads(sess.get("recent_entities") or "{}")
    task = ents.pop("pending_task", None)
    if not task:
        return out
    bankcore.update_session(conn, session_id, recent_entities=ents)
    try:
        second = handle_chat(conn, session_id, task)
    except Exception:  # noqa: BLE001
        return out
    text = str(second.get("reply") or "").strip()
    if text:
        out["reply"] = (out.get("reply") or "").rstrip() + "\n\n接着办了第二件：\n" + text
    return out

def _remember_result(conn, session_id: str, trace_id: str, res: dict) -> None:
    """记住上一轮查询结果，供"查看详情/哪几笔/看审计"追问使用。"""
    priority = ["detect_anomaly", "get_transactions", "analyze_spending",
                "get_subscriptions", "get_bill_report"]
    chosen = None
    for tool in priority:
        chosen = next((r for r in (res.get("results") or [])
                       if r.get("tool") == tool and r.get("ok")), None)
        if chosen:
            break
    if not chosen:
        return
    data = dict(chosen.get("data") or {})
    if isinstance(data.get("items"), list):
        data["items"] = data["items"][:200]
    ents = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
    ents["last_result"] = {"tool": chosen["tool"], "data": data, "trace_id": trace_id}
    bankcore.update_session(conn, session_id, recent_entities=ents)


def _maybe_followup(conn, session_id: str, text: str) -> str | None:
    """上下文追问：查看详情 / 哪几笔 / 看审计。"""
    t = (text or "").strip()
    ents = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
    last = ents.get("last_result") or {}
    if re.search(r"^(全部展示|看全部|展开全部|显示全部|剩下的|还有呢|还有哪些)[？?]?$", t):
        if last.get("tool") == "get_transactions":
            data = last.get("data") or {}
            rows = bankcore.list_transactions(
                conn, config.DEFAULT_USER_ID, data.get("range_start"),
                data.get("range_end"), limit=500)
            if not rows:
                return "上一轮查询没有可展开的流水。"
            lines = [f"{data.get('range_label') or '本次查询'}共 {len(rows)} 笔："]
            for row in rows:
                direction = "收入" if row.get("direction") == "in" else "支出"
                lines.append(
                    f"· {str(row.get('created_at'))[:10]} {row.get('counterparty') or ''} "
                    f"{direction} {_money(int(row.get('amount_cents') or 0))} 元")
            return "\n".join(lines)

    if re.search(r"^(?:那|那么)?\s*(?:帮我)?\s*(?:看看|看一下|查一下|查查|查)?\s*(上个月|上月)\s*(呢|的账单|账单)?[？?]?$", t):
        if last.get("tool") == "get_transactions":
            slots = ((ents.get("last_intent") or {}).get("slots") or {})
            who = str(slots.get("counterparty") or "")
            direction = str(slots.get("direction") or "")
            tail = "给我的" if direction == "in" else ("我转出的" if direction == "out" else "")
            follow_text = f"查{who}上个月{tail}转账" if who else "查上个月流水"
            nested = handle_chat(conn, session_id, follow_text)
            return nested.get("reply")
        data = tools.tool_analyze_spending(conn, config.DEFAULT_USER_ID, {"range": "last_month"})
        data = data.get("data") or {}
        cats = "、".join(f"{c['category']} {_money(int(c['amount_cents']))} 元"
                        for c in (data.get("by_category") or [])[:3])
        return (f"{data.get('range_label') or '上月'}支出合计 {_money(int(data.get('total_cents') or 0))} 元"
                + (f"，前三类：{cats}。" if cats else "。"))

    if re.search(r"(第一个|第1个).{0,5}(退掉|取消|不要|关掉)", t):
        if last.get("tool") == "get_subscriptions":
            items = (last.get("data") or {}).get("items") or []
            if items:
                merchant = items[0].get("merchant")
                return f"第 1 个是「{merchant}」。确认退掉请说「退掉{merchant}」。"

    if re.search(r"^(只看)?\s*(收入|支出|转入|转出|进账|消费)\s*(呢|的|明细|流水)?[？?]?$", t) \
            or re.search(r"只看(收入|支出|转入|转出|进账|消费)", t):
        if last.get("tool") != "get_transactions":
            return "先问我「最近的流水」或「这个月的流水」，我再帮你按收入/支出筛。"
        items = (last.get("data") or {}).get("items") or []
        label = (last.get("data") or {}).get("range_label") or ""
        income = bool(re.search(r"收入|转入|进账", t))
        picked = [i for i in items if i.get("direction") == ("in" if income else "out")]
        word = "收入" if income else "支出"
        if not picked:
            return f"{label}没有{word}记录。"
        total = sum(int(i.get("amount_cents") or 0) for i in picked)
        lines = [f"· {str(i.get('created_at'))[:10]} {i.get('counterparty') or ''} "
                 f"{_money(int(i.get('amount_cents') or 0))} 元" for i in picked[:8]]
        more = f"\n（还有 {len(picked) - 8} 笔）" if len(picked) > 8 else ""
        return (f"{label}共 {len(picked)} 笔{word}，合计 {_money(total)} 元。\n"
                + "\n".join(lines) + more)

    if re.search(r"(查看|看|展开|显示).{0,3}(详情|明细)|具体(是|哪)|哪几笔|明细|列出来", t):
        if not last:
            return "当前没有可展开的记录。你可以先问「有没有乱扣费的」或「最近的流水」。"
        tool = last.get("tool")
        data = last.get("data") or {}
        if tool == "detect_anomaly":
            items = data.get("items") or []
            if not items:
                return "上一次排查没有发现异常交易。"
            lines = [f"· {str(i.get('created_at'))[:10]} {i.get('counterparty') or ''} "
                     f"{_money(int(i.get('amount_cents') or 0))} 元 —— {i.get('reason') or ''}"
                     for i in items]
            return "上一次异常排查的明细：\n" + "\n".join(lines)
        if tool == "get_transactions":
            items = data.get("items") or []
            if not items:
                return "上一次查询没有找到记录。"
            lines = [f"· {str(i.get('created_at'))[:10]} {i.get('counterparty') or ''} "
                     f"{_money(int(i.get('amount_cents') or 0))} 元" for i in items]
            return "上一次查询的明细：\n" + "\n".join(lines)
        if tool == "analyze_spending":
            cats = data.get("by_category") or []
            lines = [f"· {c.get('category')} {_money(int(c.get('amount_cents') or 0))} 元"
                     for c in cats[:6]]
            return f"{data.get('range_label', '')}支出分类：\n" + "\n".join(lines)
        if tool == "get_subscriptions":
            items = data.get("items") or []
            lines = [f"· {i.get('merchant')} {_money(int(i.get('amount_cents') or 0))} 元/"
                     f"{int(i.get('cycle_days') or 30)} 天" for i in items]
            return "当前订阅明细：\n" + "\n".join(lines)
        return "这一条没有可展开的明细。"
    if re.search(r"审计|决策链|回放", t):
        trace = last.get("trace_id")
        if trace:
            return f"上一次操作的审计回放：/audit/{trace}\n也可以点结果卡下方的「查看审计」打开。"
    return None

def _parse_reminder_day(text: str):
    """识别提醒日期：15号 / 十五号 / 11月15号 / 下个月15号 / 明天 / 下周周三 / 月底。"""
    t = lexicon.canonicalize(text)
    today = config.now_dt().date()

    def result(day):
        return f"{day.isoformat()}T09:00:00+08:00", (day.month, day.day)

    for word, offset in (("大后天", 3), ("后天", 2), ("明天", 1)):
        if word in t:
            return result(today + timedelta(days=offset))

    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]", t)
    if m:
        year, month, day = today.year, int(m.group(1)), int(m.group(2))
        try:
            target = today.replace(year=year, month=month, day=day)
        except ValueError:
            return None, None
        if target < today:
            try:
                target = target.replace(year=year + 1)
            except ValueError:
                return None, None
        return result(target)

    m = re.search(r"下个?月\s*(\d{1,2})\s*[号日]", t)
    if m:
        month = today.month + 1
        year = today.year + (1 if month > 12 else 0)
        month = 1 if month > 12 else month
        day = int(m.group(1))
        last = calendar.monthrange(year, month)[1]
        if day > last:
            return None, None
        return result(today.replace(year=year, month=month, day=day))

    m = re.search(r"下(?:周|星期)([一二三四五六日天])", t)
    if m:
        offset = "一二三四五六日".index(m.group(1).replace("天", "日"))
        next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
        return result(next_monday + timedelta(days=offset))

    if "月底" in t:
        last = calendar.monthrange(today.year, today.month)[1]
        day = today.replace(day=last)
        if day < today:
            month = today.month + 1
            year = today.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            day = today.replace(year=year, month=month, day=calendar.monthrange(year, month)[1])
        return result(day)

    m = re.search(r"(\d{1,2})\s*[号日]", t)
    if not m:
        return None, None
    day = int(m.group(1))
    if day < 1 or day > 31:
        return None, None
    year, month = today.year, today.month
    if day < today.day:
        month += 1
        if month > 12:
            month, year = 1, year + 1
    if day > calendar.monthrange(year, month)[1]:
        return None, None
    return result(today.replace(year=year, month=month, day=day))


def _maybe_revise_proposal_budget(conn, session_id: str, text: str, user_id: int,
                                  trace_id: str, events: list, audit) -> dict | None:
    """生日方案后说“2000元呢”：更新预算并重出方案，不掉澄清。"""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:元|块)?\s*呢[？?]?\s*", text or "")
    if not m:
        return None
    entities = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
    proposal = entities.get("proposal") or {}
    if not proposal:
        return None
    budget = int(round(float(m.group(1)) * 100))
    if budget <= 0:
        return None
    proposal["budget"] = budget
    entities["proposal"] = proposal
    bankcore.update_session(conn, session_id, recent_entities=entities)
    kind = proposal.get("kind") or "birthday"
    who = proposal.get("payee") or "TA"
    trigger = {"type": kind, "days_before": 2, "time": "09:00"}
    run_at = bankcore._next_run_at(conn, user_id, trigger)
    try:
        from datetime import date as _date, timedelta as _timedelta
        target = _date.fromisoformat(run_at[:10]) + _timedelta(days=2)
        target_text = f"{target.month} 月 {target.day} 日"
        remind = f"{run_at[5:7]}-{run_at[8:10]}"
    except (ValueError, TypeError):
        target_text, remind = "下个月", "提前两天"
    flowers, cake = min(30_000, budget // 2), min(20_000, budget // 4)
    spare = max(budget - flowers - cake, 0)
    reply = (f"预算改为 {_money(budget)} 元。{target_text}是{who}的生日，建议这样安排：\n"
             f"· 鲜花 {_money(flowers)} 元（生日前 2 天送到）\n"
             f"· 蛋糕 {_money(cake)} 元（生日当天上午）\n"
             f"· 剩余 {_money(spare)} 元留作礼物备用（不自动下单）\n"
             f"我会在 {remind} 提醒你确认；确认后再锁定预算。按这个来吗？\n"
             f"计划预览（确认后执行）：① 查联系人 ② 读取生日档案 ③ 锁定预算 ④ 创建长期计划")
    audit.append(trace_id, actor="agent", action="proposal.budget_revised",
                 payload={"budget_cents": budget, "payee": who}, decision="awaiting_yes")
    _ev(events, "policy", level="green", rule_id="R-G-PROPOSAL",
        reason="只调整方案预算，未执行资金操作")
    _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
    return _finish(conn, trace_id, "done", events, reply, audit)


def _offer_plan_proposal(conn, session_id, trace_id, events, audit, intent, text, user_id) -> dict:
    """生日/纪念日这类"帮我准备一下"，先给方案再执行——别一上来就锁钱。"""
    slots = intent.get("slots") or {}
    payee = str(slots.get("payee") or "")
    contact = None
    for variant in _contact_variants(payee):
        rows = bankcore.find_contact(conn, user_id, variant)
        if len(rows) == 1:
            contact = rows[0]
            break
    budget = _int(slots.get("budget") or 100_000)
    kind = "birthday" if "生日" in text else "anniversary"
    trigger = {"type": kind, "days_before": 2, "time": "09:00"}
    run_at = bankcore._next_run_at(conn, user_id, trigger)
    try:
        from datetime import date as _date, timedelta as _timedelta

        target = _date.fromisoformat(run_at[:10]) + _timedelta(days=2)
        target_text = f"{target.month} 月 {target.day} 日"
        remind = f"{run_at[5:7]}-{run_at[8:10]}"
    except (ValueError, TypeError):
        target_text, remind = "下个月", "提前两天"
    who = contact["name"] if contact else (payee or "TA")
    flowers, cake = min(30_000, budget // 2), min(20_000, budget // 4)
    spare = max(budget - flowers - cake, 0)
    reply = (f"{target_text}是{who}的生日，预算 {_money(budget)} 元。我建议这样安排：\n"
             f"· 鲜花 {_money(flowers)} 元（生日前 2 天送到）\n"
             f"· 蛋糕 {_money(cake)} 元（生日当天上午）\n"
             f"· 剩余 {_money(spare)} 元留作礼物备用（不自动下单）\n"
             f"我会在 {remind} 提醒你确认；确认后再锁定预算。按这个来吗？\n"
             f"计划预览（确认后执行）：① 查联系人 ② 读取生日档案 ③ 锁定预算 ④ 创建长期计划")
    entities = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
    entities["proposal"] = {"kind": kind, "payee": who, "budget": budget}
    bankcore.update_session(conn, session_id, recent_entities=entities)
    audit.append(trace_id, actor="agent", action="proposal.offered",
                 payload={"payee": who, "budget_cents": budget, "trigger": trigger},
                 decision="awaiting_yes")
    _ev(events, "policy", level="green", rule_id="R-G-PROPOSAL",
        reason="方案建议阶段，未执行任何资金操作")
    _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
    return _finish(conn, trace_id, "done", events, reply, audit)


# ---------------------------------------------------------------- 异常熔断（R12）
CIRCUIT_OBSERVE_AT = 3      # 连续失败 3 次 → OBSERVE（预警）
CIRCUIT_LOCK_AT = 5         # 连续失败 5 次 → LOCKED（禁止写操作 15 分钟）
CIRCUIT_LOCK_SECONDS = 900


def _circuit_bump(conn, session_id: str, reason: str) -> tuple[int, str]:
    """记一次安全失败；达到阈值时把会话推进到 OBSERVE / LOCKED。"""
    sess = bankcore.get_session(conn, session_id) or {}
    ents = json.loads(sess.get("recent_entities") or "{}")
    streak = int(ents.get("fail_streak") or 0) + 1
    ents["fail_streak"] = streak
    ents["last_fail"] = reason
    state = sess.get("state") or "NORMAL"
    if streak >= CIRCUIT_LOCK_AT:
        state = "LOCKED"
        ents["locked_until"] = (config.now_dt() + timedelta(seconds=CIRCUIT_LOCK_SECONDS)).isoformat()
    elif streak >= CIRCUIT_OBSERVE_AT:
        state = "OBSERVE"
    bankcore.update_session(conn, session_id, state=state, recent_entities=ents)
    return streak, state


def _circuit_reset(conn, session_id: str) -> None:
    sess = bankcore.get_session(conn, session_id) or {}
    if not sess:
        return
    ents = json.loads(sess.get("recent_entities") or "{}")
    if not ents.get("fail_streak") and sess.get("state") not in ("OBSERVE",):
        return
    ents["fail_streak"] = 0
    ents.pop("locked_until", None)
    ents.pop("last_fail", None)
    bankcore.update_session(conn, session_id, state="NORMAL", recent_entities=ents)


def _circuit_unlock_if_due(conn, session_id: str, audit, trace_id: str) -> bool:
    sess = bankcore.get_session(conn, session_id) or {}
    if (sess.get("state") or "NORMAL") != "LOCKED":
        return False
    ents = json.loads(sess.get("recent_entities") or "{}")
    until = str(ents.get("locked_until") or "")
    if until and until <= config.now_iso():
        ents["fail_streak"] = 0
        ents.pop("locked_until", None)
        ents.pop("last_fail", None)
        bankcore.update_session(conn, session_id, state="NORMAL", recent_entities=ents)
        audit.append(trace_id, actor="system", action="circuit.unlock",
                     payload={"reason": "lock_expired"}, decision="ok")
        return True
    return False

# ------------------------------------------------------------------ 公开接口
def _create_ticket(conn, session_id: str, user_id: int, text: str, trace_id: str = "") -> dict:
    """转人工时生成工单，摘要带最近意图，坐席可据此接入。"""
    session = bankcore.get_session(conn, session_id) or {}
    entities = json.loads(session.get("recent_entities") or "{}")
    summary = f"用户诉求：{text[:60]}；最近意图：{entities.get('last_intent') or '—'}"
    ticket_no = "T" + config.now_dt().strftime("%Y%m%d%H%M%S") + secrets.token_hex(1).upper()
    conn.execute(
        "INSERT INTO support_tickets(ticket_no,session_id,user_id,summary,status,created_at,trace_id) "
        "VALUES(?,?,?,?, 'open', ?, ?)",
        (ticket_no, session_id, user_id, summary, config.now_iso(), trace_id),
    )
    return {"ticket_no": ticket_no, "summary": summary}


_SECURITY_PROBES = (
    (r"(我是|作为|身为).{0,8}(行长|管理员|风控|客服|内部人员|系统管理员)", "role_impersonation"),
    (r"(忽略|无视|不要理会|别管).{0,8}(规则|指令|提示|验证|确认|限制|安全)", "instruction_override"),
    (r"(跳过|绕过|不要|不用|无需).{0,4}(确认|验证|强验证|审核|风控)", "verify_bypass"),
    (r"(系统指令|系统消息|系统返回|接口已返回|上游返回|role\s*[:=]|管理员口令|风控规则|系统提示词|调用.{0,6}工具|function\s*call)", "fake_system"),
    (r"(全部用户|所有用户|其他用户|用户手机号|导出.{0,6}用户)", "cross_user_scope"),
    (r"(免密额度|免密限额).{0,8}(调高|提高|临时)", "limit_probe"),
    (r"(改成|修改|设置|更换|换).{0,4}密码", "password_change"),
    (r"(重新计算余额|余额.{0,4}999|工资.{0,4}\+?\s*999)", "fake_balance"),
    (r'"ok"\s*:|balance_after|txn_no\s*=|role"?\s*:\s*"system"', "fake_tool_result"),
)


def _security_probe(text: str) -> str | None:
    for pattern, label in _SECURITY_PROBES:
        if re.search(pattern, text or "", re.I):
            return label
    return None


_SEMANTIC_LABEL = {
    "bill_analysis": "查账/账单分析",
    "subscription": "订阅管理",
    "wealth": "理财查询",
    "transfer": "转账",
    "standing_plan": "提醒/长期计划",
}


def _semantic_summary(intent: dict) -> str:
    return _SEMANTIC_LABEL.get(str(intent.get("name")), "办理这件事")


def _consultation_reply(conn, user_id: int, text: str) -> str | None:
    """咨询/模态：只回答，不生成任何办理计划。"""
    t = text or ""
    if re.search(r"(提前还款|提前还贷)", t) and re.search(r"(违约金|吗|呢|划算|预约|能不能|可以|是否|怎么|如何)", t):
        return ("本演示贷款提前还款不收取违约金；真实合同以银行公示为准。"
                "如需办理，请明确说「我要提前还款」。")
    if re.search(r"(房贷|贷款).{0,6}(利率|利息)", t) or "房贷利率" in t:
        loan = bankcore.loan_view(conn, user_id)
        if loan:
            return (f"本演示房贷年利率 3.45%，剩余本金 {_money(loan['remaining_cents'])} 元，"
                    f"月供 {_money(loan['monthly_cents'])} 元；真实利率以合同为准。")
        return "当前没有生效中的房贷。"
    if re.search(r"(还款日|哪天还|几号还|还款计划)", t):
        loan = bankcore.loan_view(conn, user_id)
        if loan:
            return (f"房贷下次还款日 {loan['next_due']}，月供 {_money(loan['monthly_cents'])} 元；"
                    f"回复「我的还款计划」可看剩余本金。")
        return "当前没有生效中的房贷。"
    if re.search(r"(我能|可以|能否).{0,4}(提前还款|提前还)", t) or "提前还款划算吗" in t or "提前还款要预约吗" in t:
        return ("可以提前还款，本演示不需要预约、不收违约金；是否划算取决于剩余利息与资金机会成本。"
                "如需办理，请说「我要提前还款」。")
    if re.search(r"(提高|提升|调高|能不能提高|可以提).{0,6}(限额|额度)", t):
        return "额度可以调整，但需要重新评估；你可以直接说「把限额调到5000」，会进入调额确认流程。"
    if "信用卡" in t and re.search(r"(转|转账)", t) and not re.search(r"(还|归还|账单|额度)", t):
        return "本演示仅支持从活期账户转出，信用卡不能直接转账；可以先还信用卡，再用活期余额转。"
    if re.search(r"(卡|卡片).{0,4}挂失.{0,6}(还能用|可以用|怎么办)", t):
        return "挂失后卡片立即停用，不能继续消费或取现；补卡需 3–5 个工作日，期间可用虚拟卡。"
    return None


def _negation_reply(conn, session_id: str, text: str) -> str | None:
    t = text or ""
    if not re.search(r"(不|别|不要|不想|不用|无需).{0,6}(还|转|挂失|赎回|退订|买|办理|操作)", t):
        return None
    conn.execute(
        "UPDATE pending_intents SET status='cancelled' WHERE session_id=? "
        "AND status IN ('awaiting_confirm','awaiting_stepup')", (session_id,))
    return "好的，不执行这个操作；如果有待确认请求，已一并取消。"


def _red_needs_confirm(plan: dict) -> bool:
    """最高风险还款动作：先确认卡，再 MFA。"""
    return any(n.get("tool") in ("repay_loan", "repay_credit_card", "redeem_wealth", "reverse_transfer")
               for n in plan.get("nodes") or [])


def _maybe_export_query(text: str) -> str | None:
    t = text or ""
    if re.search(r"(导出|下载)", t) and re.search(r"(账单|流水)", t) and re.search(
            r"(excel|表格|xlsx|csv|文件)", t, re.I):
        return ("已生成本月流水文件（Excel）：/v1/export/transactions.xlsx?range=this_month\n"
                "也可以在「设置 → 数据 → 导出本月流水」里选择范围和对象。")
    return None


def _maybe_modify_pending(conn, session_id: str, text: str):
    """“改成200”：取消旧确认，按新金额重新发起，避免改 token/params。"""
    short = re.sub(r"\s+", "", text or "")
    m = re.fullmatch(r"(?:改成|改为|换成)\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", short)
    if not m:
        return None
    row = conn.execute(
        "SELECT * FROM pending_intents WHERE session_id=? AND status='awaiting_confirm' "
        "ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
    if not row:
        return None
    card = json.loads(row["card_json"] or "{}")
    payee = card.get("payee_name") or next(
        (value for label, value in card.get("fields") or [] if label in ("收款人", "关联人")), "")
    if not payee:
        return None
    conn.execute("UPDATE pending_intents SET status='cancelled' WHERE id=?", (row["id"],))
    return handle_chat(conn, session_id, f"给{payee}转{m.group(1)}")

def _maybe_annual_bill(conn, user_id: int, text: str):
    if not (re.search(r"年度|全年|今年|2026\s*年", text) and re.search(r"账单|报告", text)):
        return None
    data = tools.call("get_bill_report", conn, user_id, {"range": "this_year"}).get("data") or {}
    cats = "、".join(f"{x['category']} {_money(int(x['amount_cents']))} 元"
                    for x in (data.get("by_category") or [])[:3])
    return (f"{data.get('range_label', '今年')}账单：收入 {_money(int(data.get('income_cents') or 0))} 元，"
            f"支出 {_money(int(data.get('total_cents') or 0))} 元，"
            f"结余 {_money(int(data.get('savings_cents') or 0))} 元。"
            + (f"前三类：{cats}。" if cats else ""))


def _maybe_wealth_advice(conn, user_id: int, text: str):
    t = text or ""
    if not re.search(r"理财|基金|对比|比较", t):
        return None
    if re.search(r"买|申购|赎回|转入", t):
        return None
    if not re.search(r"推荐|对比|比较|哪个|有哪些|在售", t):
        return None
    data = tools.call("get_wealth_products", conn, user_id, {}).get("data") or {}
    items = data.get("items") or []
    if not items:
        return "当前没有在售理财产品。"
    picked = []
    t_clean = t.replace(" ", "")
    for item in items:
        name = str(item.get("name") or "")
        if name and name.replace(" ", "") in t_clean:
            picked.append(item)
    if "对比" in t and len(picked) >= 2:
        lines = []
        for item in picked[:3]:
            lines.append(f"{item['name']}：{float(item.get('rate') or 0) * 100:.2f}% / "
                         f"{item.get('term_days')} 天 / {item.get('risk_level')} / "
                         f"起购 {_money(int(item.get('min_amount_cents') or 0))} 元")
        return "对比：\n" + "\n".join(lines) + "\n（历史收益不代表未来，非保本。）"
    lines = [f"{i + 1}. {item['name']}  {float(item.get('rate') or 0) * 100:.2f}%  "
             f"{item.get('term_days')} 天  {item.get('risk_level')}  "
             f"起购 {_money(int(item.get('min_amount_cents') or 0))} 元"
             for i, item in enumerate(items[:4])]
    return "在售理财产品（非保本，历史收益不代表未来）：\n" + "\n".join(lines)


def _maybe_renewal_reminder(conn, user_id: int, text: str):
    if not re.search(r"(下次|续费|扣费).{0,6}(提醒|通知)", text or ""):
        return None
    subs = [s for s in bankcore.list_subscriptions(conn, user_id) if s.get("status") == "active"]
    subs = [s for s in subs if s.get("next_charge_at")]
    if not subs:
        return "当前没有可提醒的自动扣费项目。"
    sub = sorted(subs, key=lambda s: str(s.get("next_charge_at")))[0]
    next_at = str(sub["next_charge_at"])
    try:
        from datetime import date as _date, timedelta as _td
        day = _date.fromisoformat(next_at[:10]) - _td(days=1)
        at = f"{day.isoformat()}T09:00:00"
    except ValueError:
        at = next_at
    content = f"{sub.get('merchant') or '自动扣费'} 下次扣费提醒"
    res = tools.call("set_reminder", conn, user_id, {"at": at, "content": content})
    if not res.get("ok"):
        return "提醒设置失败，请稍后再试。"
    try:
        label = f"{day.month} 月 {day.day} 日"
    except Exception:
        label = at[:10]
    return f"已设置 {label} 09:00 提醒：{content}（只提醒，不动资金）。"


def _ground_payee_by_memo(conn, user_id: int, text: str, intent: dict) -> None:
    """“给上次备注房租的那个人转100”：按交易备注/联系人反查收款人。"""
    slots = intent.get("slots") if isinstance(intent.get("slots"), dict) else {}
    t = text or ""
    m = re.search(r"备注[为是]?\s*([\u4e00-\u9fa5A-Za-z0-9]{1,12}?)(?:的那个人|的|转|$)", t)
    if not m:
        return
    keyword = m.group(1).removesuffix("的那个人").removesuffix("的").strip()
    if not keyword:
        return
    row = conn.execute(
        "SELECT counterparty FROM transactions WHERE user_id=? AND memo LIKE ? "
        "ORDER BY id DESC LIMIT 1", (user_id, f"%{keyword}%")).fetchone()
    name = str(row["counterparty"]) if row else ""
    if not name and keyword == "房租":
        name = "房东"
    if not name:
        return
    rows = bankcore.find_contact(conn, user_id, name)
    if len(rows) == 1:
        slots["payee"] = rows[0]["name"]
    else:
        slots["payee"] = name
    intent["slots"] = slots
    intent["missing"] = [x for x in (intent.get("missing") or []) if x != "payee"]


def _maybe_conditional_dag(conn, session_id: str, user_id: int, text: str):
    spec = conditional.parse(text)
    if not spec:
        return None
    evaluated = conditional.evaluate(conn, user_id, spec)
    branch = spec["then"] if evaluated["ok"] else spec.get("else")
    lines = []
    for item in evaluated["results"]:
        mark = "条件成立" if item["passed"] else "条件不成立"
        lines.append(f"{item['text']}（{item['op']} {item['threshold_cents'] / 100:.2f} 元，{mark}）")
    if not branch:
        reply = "；".join(lines) + "。条件不成立，未执行后续操作。"
        return {"status": "done", "reply": reply, "events": [], "pending": None,
                "quick_replies": []}
    nested = handle_chat(conn, session_id, branch)
    pending = nested.get("pending") or {}
    intent_id = pending.get("intent_id")
    if intent_id:
        try:
            row = conn.execute("SELECT plan_json FROM pending_intents WHERE id=?", (intent_id,)).fetchone()
            if row:
                plan = json.loads(row["plan_json"])
                query_nodes = conditional.plan_nodes(spec)
                existing_tools = {n.get("tool") for n in plan.get("nodes") or []}
                # 已存在同工具查询节点时复用，不再重复插
                ids = []
                for q in query_nodes:
                    found = next((n for n in plan["nodes"] if n.get("tool") == q["tool"]), None)
                    if found:
                        ids.append(found["id"])
                    else:
                        plan["nodes"].insert(0, q)
                        ids.append(q["id"])
                action = plan["nodes"][-1]
                if evaluated["ok"]:
                    action["condition"] = conditional.condition_metadata(spec)
                action["depends_on"] = list(dict.fromkeys(list(action.get("depends_on") or []) + ids))
                plan["conditional"] = {"results": evaluated["results"], "branch": "then" if evaluated["ok"] else "else"}
                conn.execute("UPDATE pending_intents SET plan_json=? WHERE id=?",
                             (json.dumps(plan, ensure_ascii=False), intent_id))
                for event in nested.get("events") or []:
                    if event.get("type") == "plan":
                        event.setdefault("data", {})["plan"] = plan
                nested["plan"] = plan
        except Exception:  # noqa: BLE001
            pass
    prefix = "；".join(lines) + f"。条件{'成立' if evaluated['ok'] else '不成立'}，"
    nested["reply"] = prefix + str(nested.get("reply") or "")
    return nested


def _maybe_conditional(conn, session_id: str, user_id: int, text: str):
    """简单条件式：如果(本月/上月)账单超过 X，就转 Y 给 Z。"""
    t = text or ""
    if "如果" not in t or not re.search(r"(超过|超|大于)", t) or not re.search(r"(转账|转|打|汇)", t):
        return None
    m_threshold = re.search(r"(?:超过|超|大于)\s*(\d+(?:\.\d+)?)", t)
    m_amount = re.search(r"(?:转账|转|打|汇)\s*(\d+(?:\.\d+)?)", t)
    if not m_threshold or not m_amount:
        return None
    m_payee = re.search(r"(?:给|到)\s*([\u4e00-\u9fffA-Za-z]{1,10})", t)
    payee = m_payee.group(1) if m_payee else ""
    range_key = "last_month" if re.search(r"上个月|上月", t) else "this_month"
    data = tools.tool_analyze_spending(conn, user_id, {"range": range_key})
    total = int((data.get("data") or {}).get("total_cents") or 0)
    threshold = int(round(float(m_threshold.group(1)) * 100))
    amount = int(round(float(m_amount.group(1)) * 100))
    label = (data.get("data") or {}).get("range_label") or "本月"
    if total <= threshold:
        return {"reply": f"{label}支出 {_money(total)} 元，没有超过 {_money(threshold)} 元，"
                         f"条件不成立，未发起转账。"}
    if not payee or "余额宝" in payee:
        return {"reply": f"{label}支出 {_money(total)} 元，已超过 {_money(threshold)} 元；"
                         f"但「{payee or '该对象'}」不是可用收款人，请换成联系人后我再转。"}
    nested = handle_chat(conn, session_id, f"给{payee}转{m_amount.group(1)}")
    prefix = (f"{label}支出 {_money(total)} 元，已超过 {_money(threshold)} 元，"
              f"条件成立，按条件发起转账：")
    # 把查询节点和 condition 真正写进计划（确认后按 DAG 执行）
    try:
        pending = nested.get("pending") or {}
        intent_id = pending.get("intent_id")
        row = conn.execute("SELECT plan_json FROM pending_intents WHERE id=?", (intent_id,)).fetchone()
        if row:
            plan = json.loads(row["plan_json"])
            if not any(n.get("tool") == "analyze_spending" for n in plan.get("nodes") or []):
                query = {"id": "n0", "tool": "analyze_spending",
                         "args": {"range": range_key}, "provenance": {},
                         "depends_on": [], "risk_hint": "green"}
                plan["nodes"].insert(0, query)
                action = plan["nodes"][-1]
                action["condition"] = {"node": "n0", "field": "total_cents",
                                       "op": ">", "value": threshold}
                plan["conditional"] = {"query": "n0", "summary": prefix}
                conn.execute("UPDATE pending_intents SET plan_json=? WHERE id=?",
                             (json.dumps(plan, ensure_ascii=False), intent_id))
                for event in nested.get("events") or []:
                    if event.get("type") == "plan":
                        event.setdefault("data", {})["plan"] = plan
                        event["data"]["summary"] = "查账单 → 判断条件 → " + str(event["data"].get("summary") or "")
            nested["plan"] = plan
    except Exception:  # noqa: BLE001
        pass
    nested["reply"] = prefix + "\n" + str(nested.get("reply") or "")
    return nested

def _maybe_ordinal_cancel(conn, session_id: str, text: str):
    """“第一个退掉”：按上一轮订阅列表的序号，直接发起退订。"""
    short = re.sub(r"\s+", "", text or "")
    m = re.match(r"^第?([一二三123])个?(?:退掉|取消|关掉|不要)", short)
    if not m:
        return None
    index = {"一": 0, "1": 0, "二": 1, "2": 1, "三": 2, "3": 2}[m.group(1)]
    ents = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
    last = ents.get("last_result") or {}
    if last.get("tool") != "get_subscriptions":
        return None
    items = (last.get("data") or {}).get("items") or []
    if index >= len(items):
        return None
    merchant = str(items[index].get("merchant") or "")
    if not merchant:
        return None
    return handle_chat(conn, session_id, f"退掉{merchant}")

def _maybe_product_consult(text: str) -> str | None:
    t = text or ""
    if "余额宝" in t and re.search(r"(安全|风险|保本|怎么样|可靠)", t):
        return ("余额宝本质是货币基金，不保本、收益浮动；本演示未接入余额宝，"
                "账户余额请看「我还有多少钱」。")
    if re.search(r"(理财|基金).{0,6}(赎回|到账|多久)", t):
        return ("理财赎回一般 T+1 个工作日到账（演示口径），具体以产品说明书为准；"
                "不适用转账手续费规则。")
    return None


def _maybe_simple_query(conn, user_id: int, text: str) -> str | None:
    t = re.sub(r"\s+", "", text or "")
    if re.fullmatch(r"(我的?钱呢|钱呢|我还有多少钱)", t):
        acc = bankcore.main_account(conn, user_id)
        total = bankcore.get_balance(conn, acc)
        frozen = bankcore.frozen_total(conn, acc) + int(
            conn.execute("SELECT frozen FROM accounts WHERE id=?", (acc,)).fetchone()["frozen"] or 0)
        if frozen > 0:
            return (f"当前可用余额 {_money(total - frozen)} 元（总余额 {_money(total)} 元，"
                    f"其中 {_money(frozen)} 元已被计划锁定）。")
        return f"当前可用余额 {_money(total - frozen)} 元。"
    if re.fullmatch(r"(随便看看|随便|看看)", t):
        return "想看哪一类？余额、本月账单、最近流水、订阅、理财，直接说一个就行。"
    return None


def _third_party_reply(text: str) -> str | None:
    t = text or ""
    if re.search(r"(别人|他人|客户|同事|朋友|同学)的?(余额|流水|账户|手机号|身份证|资料)", t):
        return "我只能查询你本人的账户信息。要看自己的余额，可以说「查余额」。"
    if re.search(r"(?:告诉|查|查询|看看|看一下|帮我查)\s*(?:我)?\s*(?!我|你|他|她|它|与|和|跟|同|及|妈|爸|上|下|这|那|本|去|今|明|昨|每|最|近)[\u4e00-\u9fa5]{2,4}的(余额|流水|账户|手机号|身份证)", t):
        return "我只能查询你本人的账户信息。要看自己的余额，可以说「查余额」。"
    if re.search(r"(张三|李四|王五|赵六|王秀兰|客户|同事|朋友|同学|他人|别人|对方)的?(余额|流水|账户|手机号|身份证)", t):
        return "我只能查询你本人的账户信息。要看自己的余额，可以说「查余额」。"
    for m in re.finditer(r"([\u4e00-\u9fa5]{1,4})的(余额|流水|账户|手机号|身份证)", t):
        subject = m.group(1)
        if any(token in subject for token in ("我", "你", "他", "她", "它", "与", "和", "跟", "同", "及", "妈", "爸", "老婆", "老公", "家人", "孩子", "女儿", "儿子", "房东", "上", "下", "这", "那", "本", "去", "今", "明", "昨", "每", "最", "近", "前", "后", "周", "月", "年", "天", "期", "次", "笔")):
            continue
        return "我只能查询你本人的账户信息。要看自己的余额，可以说「查余额」。"
    if re.search(r"(导出|下载).{0,6}(客户|所有用户|全部用户)|后台.{0,4}数据库|数据库里有什么", t):
        return "我不能提供系统内部数据或他人信息；只能查询你本人的账户。"
    return None


def _maybe_correction(conn, session_id: str, text: str):
    short = re.sub(r"[，。！？!?\s]", "", text or "")
    if not re.match(r"^(不对|不是|错了|搞错了|重新来)", short):
        return None
    row = conn.execute(
        "SELECT * FROM pending_intents WHERE session_id=? AND status='awaiting_confirm' "
        "ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
    if row:
        card = json.loads(row["card_json"] or "{}")
        conn.execute("UPDATE pending_intents SET status='cancelled' WHERE id=?", (row["id"],))
        m = re.search(r"是([\u4e00-\u9fa5A-Za-z]{1,8})", short)
        amount = str(card.get("amount") or "").replace(",", "")
        if m and amount and card.get("action") in ("create_transfer", "submit_transfer"):
            return handle_chat(conn, session_id, f"给{m.group(1)}转{amount}")
        return {"status": "done", "reply": "好，已撤销刚才的请求。重新说一次你想办什么？",
                "events": [], "pending": None}
    return {"status": "done", "reply": "好，重新来。你想办什么？", "events": [], "pending": None}


def _maybe_default_transfer(conn, session_id: str, text: str):
    m = re.match(r"^给([\u4e00-\u9fa5A-Za-z]{1,8})转(?:点|些)?(?:钱|账)?[。！!]?$", re.sub(r"\s+", "", text or ""))
    if not m:
        return None
    payee = m.group(1)
    row = None
    for variant in (payee,):
        found = bankcore.find_contact(conn, 1, variant)
        name = found[0]["name"] if len(found) == 1 else payee
        row = conn.execute(
            "SELECT amount FROM transactions WHERE user_id=1 AND counterparty=? "
            "AND type IN ('transfer','transfer_out') ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        if row:
            break
    if not row:
        return None
    amount = int(row["amount"])
    nested = handle_chat(conn, session_id, f"给{payee}转{amount / 100:.0f}")
    card = (nested.get("pending") or {}).get("card")
    if card:
        note = f"上次给{payee}转的金额"
        if ["金额来源", note] not in card.get("fields", []):
            card.setdefault("fields", []).append(["金额来源", note])
            card.setdefault("params", {})["fields"] = card["fields"]
            new_hash = _hash_params(card["params"])
            card["params_hash"] = new_hash
            conn.execute("UPDATE pending_intents SET card_json=?, params_hash=? WHERE id=?",
                         (json.dumps(card, ensure_ascii=False), new_hash, card["intent_id"]))
        nested["reply"] = f"上次给{payee}转的是 {_money(amount)} 元，默认按这个金额；可改。\n" + str(nested.get("reply") or "")
    return nested


def _transfer_risk_note(conn, user_id: int, contact: dict, amount: int) -> str | None:
    if not contact or amount <= 0:
        return None
    name = contact.get("name") or ""
    seen = conn.execute(
        "SELECT 1 FROM transactions WHERE user_id=? AND counterparty=? LIMIT 1", (user_id, name)).fetchone()
    if not seen:
        short = f"首次向「{name}」转账"
        if amount > 50_000:
            return (short + f"；最近 30 天无往来，金额 {_money(amount)} 元，请确认已核实身份").rstrip("。")
        return short
    hour = config.now_dt().hour
    if (hour >= 23 or hour < 6) and amount > 100_000:
        return "凌晨时段大额转出，请确认本人操作"
    return None


def _maybe_aa_clarify(conn, user_id: int, text: str):
    t = text or ""
    if not re.search(r"(?<![A-Za-z])AA(?![A-Za-z])|各付|各出|平摊|均摊|分摊|人均|每人", t, re.I):
        return None
    contacts = bankcore.list_contacts(conn, user_id)
    mentioned = [c for c in contacts if (c.get("alias") and str(c["alias"]) in t) or (c.get("name") and str(c["name"]) in t)]
    if mentioned:
        return None
    names = [str(c.get("alias") or c.get("name")) for c in contacts[:5]]
    reply = "向谁收？我不知道这几位是谁，不能凭空生成收款请求。"
    if names:
        reply += "最近联系人：" + "、".join(names) + "；也可以直接说「和妈、张三 AA」。"
    return {"status": "done", "reply": reply, "events": [], "pending": None,
            "quick_replies": names}


def _red_reason(conn, user_id: int, plan: dict, level: dict, amount_text: str = "") -> str:
    if any(n.get("tool") in ("create_transfer", "submit_transfer") for n in plan.get("nodes") or []):
        daily = bankcore.get_daily_used(conn, user_id)
        limit = config.LIMIT_DAILY
        if daily > limit:
            extra = f"这笔 {amount_text} 元" if amount_text else "这笔转账"
            return (f"今日累计已转账 {_money(daily)} 元，超过 {_money(limit)} 元免密额度，"
                    f"因此{extra}也需强验证。")
    return str(level.get("reason") or "").rstrip("。")


def _parse_scheduled_when(text: str) -> str | None:
    """把“下个月1号/明天/下周五/11月15日”转成 ISO 时间（09:00）。"""
    from datetime import date as _date, datetime as _dt, timedelta as _td
    now = config.now_dt()
    t = text or ""
    day = None
    m = re.search(r"下个月\s*(\d{1,2})?\s*[号日]?", t)
    if m:
        day = int(m.group(1) or 1)
        year, month = now.year, now.month + 1
        if month > 12:
            year, month = year + 1, 1
        try:
            target = _date(year, month, day)
        except ValueError:
            return None
    elif re.search(r"明天", t):
        target = now.date() + _td(days=1)
    elif re.search(r"后天", t):
        target = now.date() + _td(days=2)
    elif re.search(r"下周[一二三四五六日天]", t):
        which = re.search(r"下周([一二三四五六日天])", t).group(1)
        idx = "一二三四五六日天".index(which)
        monday = now.date() - _td(days=now.weekday()) + _td(days=7)
        target = monday + _td(days=idx)
    elif re.search(r"每月\s*(\d{1,2})\s*[号日]", t):
        day = int(re.search(r"每月\s*(\d{1,2})", t).group(1))
        year, month = now.year, now.month
        if day <= now.day:
            month += 1
            if month > 12:
                year, month = year + 1, 1
        try:
            target = _date(year, month, day)
        except ValueError:
            return None
    elif re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]", t):
        mm, dd = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})", t).groups()
        try:
            target = _date(now.year, int(mm), int(dd))
        except ValueError:
            return None
        if target < now.date():
            target = target.replace(year=target.year + 1)
    else:
        return None
    return _dt.combine(target, _dt.min.time()).replace(hour=9).isoformat()


def handle_chat(conn, session_id: str, text: str, channel: str = "app",
                user_id: int = config.DEFAULT_USER_ID, device_id: str | None = None,
                act_as: str | None = None) -> dict:
    trace_id = "tr_" + secrets.token_hex(4)
    events: list = []
    audit = trust.AuditLog(conn)
    identity.bind_session(conn, session_id, device_id, user_id) if device_id else None
    session = bankcore.get_or_create_session(conn, session_id, user_id, channel)
    bankcore.update_session(conn, session_id, state=session.get("state") or "NORMAL")
    _circuit_unlock_if_due(conn, session_id, audit, trace_id)
    session = bankcore.get_session(conn, session_id) or session

    audit.append(trace_id, actor="user", action="message", payload={"text": text[:500]}, decision="ingest")
    if act_as:
        scope = "small" if re.search(r"(转|挂失|退|额度|申购|赎回|还款)", text) else "query"
        author = identity.verify_delegation(conn, user_id, act_as, scope)
        audit.append(trace_id, actor="agent", action="delegation.used",
                     payload={"delegate": act_as, "scope": scope, "auth_id": author["id"]},
                     decision="allowed")
    text = lexicon.canonicalize(text)

    probe = _security_probe(text)
    if probe:
        denial = ("检测到越权/提示注入/伪造系统消息，已拒绝本次请求（" + probe + "）。"
                  "需要办理请按正常流程说，我不会跳过确认或验证。")
        audit.append(trace_id, actor="agent", action="guard.security_probe",
                     payload={"category": probe, "text": text[:120]}, decision="denied")
        _ev(events, "policy", level="red", rule_id="R-D-GUARD",
            reason="安全探针命中：" + probe)
        _ev(events, "result", reply=denial, level="red", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "denied", events, denial, audit)

    injection = trust.scan_injection(text)
    if injection.get("hit"):
        denial = ("检测到疑似提示注入/权限伪装，已拦截本次请求。"
                  "我不会因为一句话跳过确认、验证或改写系统规则；需要办理请按正常流程说。")
        audit.append(trace_id, actor="agent", action="guard.injection",
                     payload={"patterns": injection.get("patterns"), "text": text[:120]},
                     decision="denied")
        _ev(events, "policy", level="red", rule_id="R-D-INJECT",
            reason="提示注入/角色伪装命中：" + ",".join(injection.get("patterns") or []))
        _ev(events, "result", reply=denial, level="red", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "denied", events, denial, audit)

    if re.search(r"(改|修改|设置|更换|换).{0,3}密码|新密码", text):
        denial = ("这个操作我不能执行：密码修改不提供工具，Agent 只做安全跳转引导"
                  "（文档 §6.2.1 恒 deny）。如需重置，请说「重置密码」，我会发送一次性链接。")
        audit.append(trace_id, actor="agent", action="guard.password_change",
                     payload={"text": text[:60]}, decision="denied")
        _ev(events, "policy", level="red", rule_id="R-D-01",
            reason="密码修改恒拒绝，只提供安全重置引导")
        _ev(events, "result", reply=denial, level="red", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "denied", events, denial, audit)

    ordinal_cancel = _maybe_ordinal_cancel(conn, session_id, text)
    if ordinal_cancel is not None:
        return ordinal_cancel

    modified = _maybe_modify_pending(conn, session_id, text)
    if modified is not None:
        return modified

    pending_row = conn.execute(
        "SELECT id, token, status, stepup_json FROM pending_intents WHERE session_id=? "
        "AND status IN ('awaiting_confirm','awaiting_stepup') ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    short = re.sub(r"\s+", "", text or "")
    if pending_row and pending_row["status"] == "awaiting_confirm" and short in (
            "确认", "确定", "确认执行", "同意", "可以", "办吧"):
        return handle_confirm(conn, session_id, pending_row["id"], pending_row["token"], user_id)
    if pending_row and pending_row["status"] == "awaiting_stepup" and short in ("确认", "确定", "继续"):
        return _finish(conn, trace_id, "awaiting_stepup", events,
                       "本次需要强验证，请直接回复 6 位验证码（演示 123456），不是回复「确认」。",
                       audit, pending={"intent_id": pending_row["id"], "level": "red",
                                      "challenge_id": json.loads(pending_row["stepup_json"] or "{}").get("challenge_id")})
    if pending_row and pending_row["status"] == "awaiting_stepup" and re.fullmatch(r"\d{6}", short):
        stepup_data = json.loads(pending_row["stepup_json"] or "{}")
        challenge_id = str(stepup_data.get("challenge_id") or pending_row["id"])
        return handle_stepup(conn, session_id, challenge_id, short, user_id)

    third_party = _third_party_reply(text)
    if third_party:
        audit.append(trace_id, actor="agent", action="guard.third_party_query",
                     payload={"text": text[:80]}, decision="refused")
        _ev(events, "policy", level="green", rule_id="R-G-THIRD-PARTY",
            reason="只允许查询本人账户")
        _ev(events, "result", reply=third_party, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, third_party, audit)

    correction = _maybe_correction(conn, session_id, text)
    if correction is not None:
        return correction

    default_transfer = _maybe_default_transfer(conn, session_id, text)
    if default_transfer is not None:
        return default_transfer

    aa_clarify = _maybe_aa_clarify(conn, user_id, text)
    if aa_clarify is not None:
        return aa_clarify

    if re.search(r"如果|若|要是", text):
        early_conditional = _maybe_conditional_dag(conn, session_id, user_id, text)
        if early_conditional is not None:
            return early_conditional

    annual = _maybe_annual_bill(conn, user_id, text)
    if annual:
        audit.append(trace_id, actor="agent", action="bill.annual", payload={"text": text[:40]},
                     decision="answered")
        _ev(events, "result", reply=annual, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, annual, audit)

    wealth_advice = _maybe_wealth_advice(conn, user_id, text)
    if wealth_advice:
        audit.append(trace_id, actor="agent", action="wealth.advice", payload={"text": text[:40]},
                     decision="answered")
        _ev(events, "result", reply=wealth_advice, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, wealth_advice, audit)

    renewal = _maybe_renewal_reminder(conn, user_id, text)
    if renewal:
        audit.append(trace_id, actor="agent", action="reminder.renewal", payload={"text": text[:40]},
                     decision="answered" if "已设置" in renewal else "clarify")
        _ev(events, "result", reply=renewal, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, renewal, audit)

    simple_reply = _maybe_simple_query(conn, user_id, text)
    if simple_reply:
        audit.append(trace_id, actor="agent", action="query.simple", payload={"text": text[:40]},
                     decision="answered")
        _ev(events, "result", reply=simple_reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, simple_reply, audit)

    product_consult = _maybe_product_consult(text)
    if product_consult:
        audit.append(trace_id, actor="agent", action="consultation.product",
                     payload={"text": text[:60]}, decision="answered")
        _ev(events, "result", reply=product_consult, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, product_consult, audit)

    consultation = _consultation_reply(conn, user_id, text)
    if consultation:
        audit.append(trace_id, actor="agent", action="consultation.answer",
                     payload={"text": text[:80]}, decision="answered")
        _ev(events, "policy", level="green", rule_id="R-G-CONSULT",
            reason="咨询/模态识别，只回答不办理")
        _ev(events, "result", reply=consultation, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, consultation, audit)

    negation = _negation_reply(conn, session_id, text)
    if negation:
        audit.append(trace_id, actor="agent", action="guard.negation",
                     payload={"text": text[:80]}, decision="cancelled")
        _ev(events, "policy", level="green", rule_id="R-G-NEGATION",
            reason="否定句不执行操作")
        _ev(events, "result", reply=negation, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, negation, audit)

    if _is_cancel(text) and not re.search(r"(转账|汇款|那笔|刚才那笔)", text):
        return _handle_undo(conn, session_id, trace_id, events, audit, user_id)

    entities = json.loads(session.get("recent_entities") or "{}")
    proposal = entities.get("proposal") or {}
    forced_intent = None
    semantic_pending = entities.get("semantic_pending")
    if semantic_pending:
        short_reply = re.sub(r"\s+", "", text or "")
        if short_reply in ("对", "是", "嗯", "没错", "继续", "是的"):
            forced_intent = semantic_pending
            entities.pop("semantic_pending", None)
            bankcore.update_session(conn, session_id, recent_entities=entities)
        elif short_reply in ("不是", "不对", "算了"):
            entities.pop("semantic_pending", None)
            bankcore.update_session(conn, session_id, recent_entities=entities)

    remind_at, remind_md = _parse_reminder_day(text)
    revised = _maybe_revise_proposal_budget(conn, session_id, text, user_id, trace_id, events, audit)
    if revised is not None:
        return revised

    if proposal and remind_at and re.search(r"(提醒|通知|叫我|改到|改成|提前到|定在)", text):
        proposal["remind_at"] = remind_at
        entities["proposal"] = proposal
        bankcore.update_session(conn, session_id, recent_entities=entities)
        budget = _int(proposal.get("budget") or 0)
        reply = (f"好，提醒改到 {remind_md[0]} 月 {remind_md[1]} 日 09:00。"
                 f"预算 {_money(budget)} 元先不变；确认方案后我再锁预算、建计划。要按这个来吗？")
        audit.append(trace_id, actor="agent", action="proposal.reminder_changed",
                     payload={"remind_at": remind_at}, decision="awaiting_yes")
        _ev(events, "policy", level="green", rule_id="R-G-PROPOSAL",
            reason="只调整提醒时间，未执行资金操作")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    if (not proposal and remind_at and re.search(r"(提醒|通知|叫我)", text)
            and not re.search(r"(每|生日|纪念日)", text)):
        goal = re.sub(
            r"(下个?月\s*\d{1,2}\s*[号日]|下(?:周|星期)[一二三四五六日天]|大后天|后天|明天|"
            r"\d{1,2}\s*月\s*\d{1,2}\s*[号日]|\d{1,2}\s*[号日]|月底)", "", text)
        goal = goal.strip("，,。 ；;")
        goal = re.sub(r"^(?:(?:帮我约|帮我|约|记得|到时候|提醒我|提醒|叫我|喊我|通知我))+", "", goal)
        goal = goal.strip("，,。 ") or "定时提醒"
        plan_data = bankcore.create_standing_plan(
            conn, user_id, goal=goal,
            trigger_rule={"type": "at", "at": remind_at, "time": "09:00"},
            actions=[{"tool": "notify", "content": goal}], budget_cents=0,
        )
        reply = (f"已设置 {remind_md[0]} 月 {remind_md[1]} 日 09:00 提醒：{goal}"
                 f"（只提醒，不动资金）。")
        audit.append(trace_id, actor="agent", action="reminder.created",
                     payload={"goal": goal, "remind_at": remind_at,
                              "plan_id": plan_data.get("plan_id")}, decision="executed")
        _ev(events, "policy", level="green", rule_id="R-G-REMINDER",
            reason="纯提醒，不涉及资金操作")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    if (re.search(r"(提醒我|叫我|喊我|别忘了)", text) and not remind_at
            and len(text.strip()) <= 12
            and not re.search(r"(每|生日|纪念日|计划)", text)):
        reply = "好的，具体哪天提醒你？可以说「15号」「下个月1号」或「下周三」。"
        audit.append(trace_id, actor="agent", action="reminder.need_date",
                     payload={"text": text[:30]}, decision="awaiting_date")
        _ev(events, "policy", level="green", rule_id="R-G-REMINDER",
            reason="提醒时间不明确，先补日期")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    affirm_words = ("按这个", "就这个", "就这样", "没问题", "可以的", "可以", "好的", "好", "行", "嗯")
    short_yes = {"来", "整", "搞", "安排", "开始", "来吧", "搞起", "行吧", "就它", "嗯嗯", "可以可以", "安排上"}
    from_proposal = bool(proposal) and (
        dialogue.match(text) == "affirm"
        or text.strip() in short_yes
        or any(w in text for w in affirm_words))
    if from_proposal:
        entities.pop("proposal", None)
        bankcore.update_session(conn, session_id, recent_entities=entities)
    elif proposal and len(text.strip()) <= 4 and not re.search(r"\d", text):
        # 方案还挂着、用户只回了两三个字（"来""好呀""冲"）→ 先确认，别让模型自由发挥
        tips = "；".join(f"{k} {v}" for k, v in [
            ("要按这个方案来吗？", "说「可以」或「算了」即可"),
        ])
        reply = (f"方案还等你确认：鲜花 300 + 蛋糕 200 + 备用 500，11-16 提醒你下单。\n{tips}")
        audit.append(trace_id, actor="agent", action="proposal.reconfirmed",
                     payload={"text": text[:20]}, decision="awaiting_yes")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    from_context = False
    context_intent = None
    if not from_proposal:
        context_intent = _context_carry(conn, user_id, text, entities)
        from_context = context_intent is not None

    from_clarify = False
    chosen_intent = None
    choice = clarify.pick(entities.get("clarify"), text)
    if choice:
        entities.pop("clarify", None)
        bankcore.update_session(conn, session_id, recent_entities=entities)
        audit.append(trace_id, actor="user", action="clarify.chosen",
                     payload={"choice": choice.get("key"), "text": text[:20]}, decision="resolved")
        if choice.get("intent") is None:
            reply = dialogue.reply_for("handoff")
            _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
            return _finish(conn, trace_id, "done", events, reply, audit)
        chosen_intent = dict(choice["intent"])
        chosen_intent.update({"missing": [], "ambiguity": [], "spans": {}, "confidence": 0.9,
                              "source": "clarify"})
        from_clarify = True

    if not (from_proposal or from_clarify or from_context):
        compound = _split_compound(text)
        if compound:
            first, second = compound
            for _c in bankcore.list_contacts(conn, user_id):
                for _token in (_c.get("alias"), _c.get("name")):
                    if _token and str(_token) in first:
                        second = re.sub(r"他|她|对方", str(_token), second, count=1)
                        break
            if not _WRITE_HINT.search(first):
                first_res = handle_chat(conn, session_id, first)
                second_res = handle_chat(conn, session_id, second)
                prefix = str(first_res.get("reply") or "").strip()
                body = str(second_res.get("reply") or "").strip()
                if prefix:
                    second_res["reply"] = prefix + "\n\n接着办了第二件：\n" + body
                return second_res
            ents = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
            ents["pending_task"] = second
            bankcore.update_session(conn, session_id, recent_entities=ents)
            text = first

    pending_row = conn.execute(
        "SELECT card_json, status FROM pending_intents WHERE session_id=? "
        "AND status IN ('awaiting_confirm','awaiting_stepup') ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    kind = None if (from_proposal or from_clarify or from_context) else dialogue.match(text)
    if kind == "handoff":
        ticket = _create_ticket(conn, session_id, user_id, text, trace_id)
        reply = (f"已转人工，工单号 {ticket['ticket_no']}。\n"
                 f"交接摘要：{ticket['summary']}\n坐席会带着这段上下文接入，不用重复描述。")
        audit.append(trace_id, actor="agent", action="handoff",
                     payload={"ticket_no": ticket["ticket_no"], "summary": ticket["summary"]},
                     decision="transferred")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)
    if kind:
        pending_level = None
        if pending_row:
            pending_level = json.loads(pending_row["card_json"] or "{}").get("level")
        reply = dialogue.reply_for(kind, {
            "has_pending": bool(pending_row),
            "pending_level": pending_level,
            "last_intent": entities.get("last_intent"),
        })
        audit.append(trace_id, actor="agent", action="dialogue.reply",
                     payload={"kind": kind, "text": text[:100]}, decision="ok")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    follow_reply = _maybe_followup(conn, session_id, text)
    if follow_reply:
        audit.append(trace_id, actor="agent", action="followup.answer",
                     payload={"text": text[:40]}, decision="answered")
        _ev(events, "policy", level="green", rule_id="R-G-DIRECT", reason="上下文追问，免确认")
        _ev(events, "result", reply=follow_reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, follow_reply, audit)

    conditional_result = _maybe_conditional_dag(conn, session_id, user_id, text)
    if conditional_result is not None:
        if isinstance(conditional_result, dict) and "status" in conditional_result:
            return conditional_result
        reply = conditional_result.get("reply") if isinstance(conditional_result, dict) else str(conditional_result)
        audit.append(trace_id, actor="agent", action="conditional.evaluated",
                     payload={"text": text[:80]}, decision="evaluated")
        _ev(events, "policy", level="green", rule_id="R-G-CONDITIONAL",
            reason="条件式：先查账单，再决定是否发起转账")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    export_reply = _maybe_export_query(text)
    if export_reply:
        audit.append(trace_id, actor="agent", action="export.answer", payload={"text": text[:40]},
                     decision="answered")
        _ev(events, "result", reply=export_reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, export_reply, audit)

    if re.search(r"(今天有什么提醒|今日提醒|主动提醒|待办|到期提醒|还款提醒|有什么要提醒)", text):
        proactive_reply = proactive.summary(conn, user_id)
        audit.append(trace_id, actor="agent", action="proactive.summary",
                     payload={"text": text[:40]}, decision="answered")
        _ev(events, "policy", level="green", rule_id="R-G-PROACTIVE", reason="主动服务汇总，免确认")
        _ev(events, "result", reply=proactive_reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, proactive_reply, audit)

    action_like = re.search(r"(转账|汇款|付款|还款|挂失|退订|申购|赎回|给我妈转|给.*转\s*\d)", text)
    kb_reply = None if action_like else knowledge.answer(text, config.now_dt().date(), conn)
    if kb_reply:
        audit.append(trace_id, actor="agent", action="knowledge.answer",
                     payload={"text": text[:40]}, decision="answered")
        _ev(events, "policy", level="green", rule_id="R-G-KNOWLEDGE", reason="知识库回答，带来源与生效日期")
        _ev(events, "result", reply=kb_reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, kb_reply, audit)

    insight_reply = insight.answer(conn, user_id, text, session_id)
    if insight_reply:
        audit.append(trace_id, actor="agent", action="insight.answer",
                     payload={"text": text[:40]}, decision="answered")
        _ev(events, "policy", level="green", rule_id="R-G-DIRECT", reason="业务信息查询，免确认")
        _ev(events, "result", reply=insight_reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, insight_reply, audit)

    wealth_reply = _maybe_wealth_query(conn, user_id, text)
    if wealth_reply:
        audit.append(trace_id, actor="agent", action="wealth.query",
                     payload={"text": text[:40]}, decision="answered")
        _ev(events, "policy", level="green", rule_id="R-G-DIRECT", reason="理财信息查询，免确认")
        _ev(events, "result", reply=wealth_reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, wealth_reply, audit)

    bal_reply = _maybe_balance_query(conn, user_id, text)
    if bal_reply:
        audit.append(trace_id, actor="agent", action="account.balance",
                     payload={"text": text[:40]}, decision="answered")
        _ev(events, "policy", level="green", rule_id="R-G-DIRECT", reason="只读查询直答，免确认")
        _ev(events, "result", reply=bal_reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, bal_reply, audit)

    detail = _maybe_subscription_detail(conn, user_id, text)
    if detail:
        audit.append(trace_id, actor="agent", action="subscription.detail",
                     payload={"merchant": detail["merchant"], "amount_cents": detail["amount_cents"]},
                     decision="answered")
        _ev(events, "policy", level="green", rule_id="R-G-DIRECT", reason="订阅信息查询，免确认")
        _ev(events, "result", reply=detail["reply"], level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, detail["reply"], audit)

    _ev(events, "thinking", stage="nlu")
    ctx = {
        "user_id": user_id, "channel": channel,
        "recent_entities": json.loads(session.get("recent_entities") or "{}"),
        "last_intent": (json.loads(session.get("recent_entities") or "{}").get("last_intent")),
    }
    try:
        if forced_intent:
            intent = forced_intent
        elif from_proposal:
            intent = {
                "name": "standing_plan",
                "slots": {"payee": proposal.get("payee"), "budget": proposal.get("budget"),
                          "remind_at": proposal.get("remind_at"),
                          "goal": "生日准备" if proposal.get("kind") == "birthday" else "纪念日安排"},
                "missing": [], "ambiguity": [], "spans": {}, "confidence": 0.99, "source": "proposal",
            }
        elif from_clarify:
            intent = chosen_intent
        elif from_context:
            intent = context_intent
        else:
            tx_intent = _maybe_transaction_query(conn, user_id, text)
            if tx_intent:
                intent = tx_intent
            else:
                intent = _command_intent(conn, user_id, text, ctx, audit, trace_id)
            if intent is None:
                intent = understanding.parse(text, ctx, bankcore.list_contacts(conn, user_id)) or _resolve_awaiting(conn, session, text)
        if intent is None and not from_proposal and not from_clarify and not from_context:
            intent = None
            try:
                det = nlu.parse(text, ctx)
            except Exception:  # noqa: BLE001
                det = None
            det_ok = (det and det.get("name") not in (None, "unknown")
                      and float(det.get("confidence") or 0) >= 0.7)
            if det_ok:
                det["source"] = "rules"
                intent = det
                audit.append(trace_id, actor="agent", action="brain.nlu",
                             payload={"ok": True, "by": "rules", "confidence": det.get("confidence")},
                             decision="rules")
            elif llm.enabled():
                t0 = time.perf_counter()
                intent, meta = llm.parse_intent(text, ctx)
                audit.append(trace_id, actor="agent", action="brain.nlu",
                             payload={"ok": bool(intent), "ms": int((time.perf_counter() - t0) * 1000), **meta},
                             decision="llm" if intent else "fallback")
                if intent is None:
                    intent = det
            else:
                intent = det
        elif intent is not None and not from_proposal and not from_clarify and not from_context:
            ents = json.loads(session.get("recent_entities") or "{}")
            ents.pop("awaiting", None)
            bankcore.update_session(conn, session_id, recent_entities=ents)
    except Exception as e:  # noqa: BLE001
        intent = {"name": "unknown", "slots": {}, "missing": [], "ambiguity": [], "spans": {}, "confidence": 0.0,
                  "error": str(e)}
    audit.append(trace_id, actor="agent", action="nlu.result",
                 payload={"intent": intent.get("name"), "slots": intent.get("slots"),
                          "confidence": intent.get("confidence")}, decision="parsed")

    if intent.get("name") == "unknown":
        alt = _entity_intent(conn, user_id, text)
        if alt:
            intent = alt
            audit.append(trace_id, actor="agent", action="entity.fallback",
                         payload={"merchant": alt["slots"].get("merchant")}, decision="resolved")

    if intent.get("name") == "unknown" and config.SCOPE_STRICT and any(
            str(a).lower() == "out_of_scope" for a in (intent.get("ambiguity") or [])):
        reply = dialogue.reply_for("offdomain")
        audit.append(trace_id, actor="agent", action="guard.out_of_scope",
                     payload={"text": text[:100]}, decision="refused")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    if intent.get("name") == "unknown":
        fuzzy = None
        if semantic.available():
            fuzzy = semantic.match(text, bankcore.list_contacts(conn, user_id))
        if fuzzy is None:
            fuzzy = lexicon.match(text, bankcore.list_contacts(conn, user_id))
        if fuzzy:
            intent = fuzzy
            audit.append(trace_id, actor="agent", action=fuzzy.get("source", "lexicon") + ".match",
                         payload={"intent": fuzzy["name"], "confidence": fuzzy["confidence"],
                                  "similarity": fuzzy.get("similarity")}, decision="resolved")
            if fuzzy.get("source") == "semantic" and fuzzy.get("needs_confirm"):
                entities["semantic_pending"] = fuzzy
                bankcore.update_session(conn, session_id, recent_entities=entities)
                summary = _semantic_summary(fuzzy)
                reply = (f"我理解你是想「{summary}」，对吗？\n"
                         f"回复「对」继续，或说「不是」我重新问。")
                audit.append(trace_id, actor="agent", action="semantic.reconfirm",
                             payload={"intent": fuzzy["name"], "similarity": fuzzy.get("similarity")},
                             decision="awaiting_yes")
                _ev(events, "policy", level="green", rule_id="R-G-SEMANTIC",
                    reason="语义中置信度，先复述确认")
                _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
                return _finish(conn, trace_id, "done", events, reply, audit)
            _ev(events, "policy", level="green", rule_id="R-G-SEMANTIC",
                reason="规则未命中，" + ("语义模型" if fuzzy.get("source") == "semantic" else "本地示例库") + "匹配成功")

    if intent.get("name") == "unknown":
        cands = clarify.build(text, ctx, entities.get("last_intent"))
        ents = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
        ents["clarify"] = {"candidates": cands}
        bankcore.update_session(conn, session_id, recent_entities=ents)
        reply = clarify.render(cands)
        audit.append(trace_id, actor="agent", action="clarify.offered",
                     payload={"keys": [c["key"] for c in cands]}, decision="awaiting_choice")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    if intent.get("name") == "handoff":
        ticket = _create_ticket(conn, session_id, user_id, text, trace_id)
        reply = (f"已转人工，工单号 {ticket['ticket_no']}。\n"
                 f"交接摘要：{ticket['summary']}\n坐席会带着这段上下文接入，不用重复描述。")
        audit.append(trace_id, actor="agent", action="handoff",
                     payload={"ticket_no": ticket["ticket_no"], "summary": ticket["summary"]},
                     decision="transferred")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    intent = _ground_payee(conn, user_id, text, intent)
    intent = _ground_product(conn, text, intent)

    if (not from_proposal and intent.get("name") == "standing_plan"
            and ("生日" in text or "纪念日" in text)
            and not any(w in text for w in ("直接", "别问", "不用问", "按这个", "就这个"))):
        return _offer_plan_proposal(conn, session_id, trace_id, events, audit, intent, text, user_id)

    if config.SCOPE_STRICT and intent.get("name") == "unknown" and any(
            str(a).lower() == "out_of_scope" for a in (intent.get("ambiguity") or [])):
        reply = dialogue.reply_for("offdomain")
        audit.append(trace_id, actor="agent", action="guard.out_of_scope",
                     payload={"text": text[:100]}, decision="refused")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    if intent.get("name") == "unknown":
        reply = dialogue.fallback_reply()
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    if intent.get("missing"):
        slots = intent.get("slots") or {}
        critical = [m for m in intent["missing"] if m in CRITICAL_MISSING]
        if critical:
            intent["missing"] = critical
            if intent.get("name") == "transfer" and "amount" in critical:
                if slots.get("payee"):
                    payee_name = str(slots.get("payee") or "").lstrip("我")
                    ask = (f"好的，转多少给{payee_name}？"
                           if payee_name and not re.search(r"多少|几|钱|什么", payee_name)
                           else "好的，转给谁、转多少？比如「转 500 给我妈」。")
                else:
                    ask = "好的，转给谁、转多少？比如「转 500 给我妈」。"
            else:
                slot = critical[0]
                ask = {"amount": "你想转多少？", "payee": "转给谁？", "range": "想看哪个时间段？",
                       "merchant": "你要退哪一项？说名字就行。", "product": "你要买哪一只？",
                       "budget": "预算大概多少？"}.get(slot, f"还差一个信息：{slot}")
            _ev(events, "result", reply=ask, level="green", audit_url=f"/audit/{trace_id}")
            return _finish(conn, trace_id, "done", events, ask, audit)

    try:
        if intent.get("name") == "transfer":
            _ground_payee_by_memo(conn, user_id, text, intent)
        if intent.get("name") == "scheduled_transfer" and not (intent.get("slots") or {}).get("run_at"):
            when_text = str((intent.get("slots") or {}).get("when") or text)
            run_at = _parse_scheduled_when(when_text)
            if run_at:
                intent.setdefault("slots", {})["run_at"] = run_at
                intent["missing"] = [m for m in (intent.get("missing") or []) if m != "when"]
        if (intent.get("name") == "service"
                and (intent.get("slots") or {}).get("action") == "reverse"
                and not (intent.get("slots") or {}).get("txn_id")):
            row = conn.execute(
                "SELECT id FROM transactions WHERE user_id=? AND status='succeeded' "
                "AND reversed_by IS NULL AND type IN ('transfer','transfer_out') "
                "ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
            if row:
                intent.setdefault("slots", {})["txn_id"] = int(row["id"])
                intent["missing"] = [m for m in (intent.get("missing") or []) if m != "txn_id"]
        plan = None
        used_llm_plan = False
        if llm.enabled() and str(intent.get("source") or "") == "llm":
            used_llm_plan = True
            t0 = time.perf_counter()
            plan, pmeta = llm.build_plan(intent, ctx)
            if plan:
                problems = validator.validate(plan, specs.TOOL_SPECS)
                if not problems and not _plan_matches_intent(intent, plan):
                    problems = [{"code": "E_INTENT_MISMATCH", "message": "计划与意图不一致"}]
                audit.append(trace_id, actor="agent", action="brain.plan",
                             payload={"ok": not problems, "ms": int((time.perf_counter() - t0) * 1000),
                                      "errors": [p.get("code") for p in problems][:3], **pmeta},
                             decision="llm" if not problems else "fallback")
                if problems:
                    plan = None
        if plan is None:
            source = str(intent.get("source") or "")
            # 模型意图可信、只是计划不合格 → 用"模型意图 + 规则模板"，不要反过来重解析把意图换掉
            if used_llm_plan and source == "llm" and intent.get("name") not in (None, "unknown"):
                if (intent.get("name") == "service"
                        and (intent.get("slots") or {}).get("action") == "reverse"
                        and not (intent.get("slots") or {}).get("txn_id")):
                    row = conn.execute(
                        "SELECT id FROM transactions WHERE user_id=? AND status='succeeded' "
                        "AND reversed_by IS NULL AND type IN ('transfer','transfer_out') "
                        "ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
                    if row:
                        intent.setdefault("slots", {})["txn_id"] = int(row["id"])
                        intent["missing"] = [m for m in (intent.get("missing") or []) if m != "txn_id"]
                plan = planner.build_plan(intent, ctx)
            else:
                if used_llm_plan and not source.startswith(("rules_query", "understanding")):
                    fallback_intent = intent
                    try:
                        ri = nlu.parse(text, ctx)
                        if ri.get("name") != "unknown":
                            fallback_intent = _ground_payee(conn, user_id, text, ri)
                    except Exception:  # noqa: BLE001
                        pass
                    intent = fallback_intent
                plan = planner.build_plan(intent, ctx)
    except Exception as e:  # noqa: BLE001
        msg = f"我没能生成可靠的操作计划（{e}），请换个说法。"
        _ev(events, "error", code="E_PLAN", message=msg)
        return _finish(conn, trace_id, "error", events, msg, audit)

    plan = _enrich_plan(conn, user_id, intent, plan, text)

    if (intent.get("name") == "card"
            and (intent.get("slots") or {}).get("action") == "limit"
            and "new_limit" in (intent.get("missing") or [])):
        reply = "要设置多少额度？比如「把这张卡的单笔限额设为 5000」。"
        audit.append(trace_id, actor="agent", action="plan.clarify",
                     payload={"goal": "card_limit"}, decision="need_more")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    if not plan.get("nodes"):
        if (intent.get("name") == "card"
                and (intent.get("slots") or {}).get("action") == "limit"
                and "new_limit" in (intent.get("missing") or [])):
            reply = "要设置多少额度？比如「把这张卡的单笔限额设为 5000」。"
        elif intent.get("name") == "transfer":
            reply = "好的，转给谁、转多少？比如「转 500 给我妈」。"
        else:
            reply = plan.get("goal") or "还需要一点信息，能再说具体点吗？"
        audit.append(trace_id, actor="agent", action="plan.clarify",
                     payload={"goal": plan.get("goal")}, decision="need_more")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    missing = _missing_payee(conn, user_id, plan)
    if missing is not None:
        if missing:
            reply = (f"我没找到「{missing}」这个收款人。\n"
                     f"可以换成已有联系人（比如：妈、老婆、房东、张三），"
                     f"或者告诉我手机号/卡号后四位，我再帮你找。")
        else:
            reply = ("我没听清转给谁。可以说名字或备注，比如「转 500 给我妈」。")
        audit.append(trace_id, actor="agent", action="clarify.payee", payload={"payee": missing},
                     decision="clarify")
        _ev(events, "result", reply=reply, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, reply, audit)

    audit.append(trace_id, actor="agent", action="plan.created",
                 payload={"plan": plan, "flow": specs.flow_of(plan["nodes"][-1]["tool"])}, decision="planned")
    _ev(events, "plan", plan=plan, summary=_plan_summary(plan))

    errs = validator.validate(plan, specs.TOOL_SPECS)
    if errs:
        audit.append(trace_id, actor="plan", action="validate.failed", payload={"errors": errs}, decision="rejected")
        codes = [str(e.get("code", "")) for e in errs]
        if any(c.startswith(("E_CAP", "E_PLAN_OUTFLOW")) for c in codes):
            msg = ("这笔金额超过了我能办的上限（单笔/单日 5 万元）。\n"
                   "可以改小一点，或者分几笔；大额建议走柜面或人工审核。")
        elif any(c.startswith("E_TOOL") for c in codes):
            msg = "这个操作我暂时不支持，换一种方式试试？"
        elif any(c.startswith("E_TAINTED") for c in codes):
            msg = "这个请求里有些内容我没法确认来源，为了安全我先不执行。请直接说明要办什么。"
        else:
            msg = "这个操作没过安全校验，请调整后再说一次。"
        _ev(events, "error", code=codes[0] if codes else "E_PLAN", message=msg)
        return _finish(conn, trace_id, "error", events, msg, audit)
    audit.append(trace_id, actor="plan", action="validate.passed", decision="valid")

    level = evaluate(conn, user_id, plan, session)
    audit.append(trace_id, actor="policy", action="policy.decide",
                 payload={"level": level["level"], "rule_id": level["rule_id"],
                          "reason": level["reason"], "nodes": [n.get("level") for n in level["nodes"]]},
                 decision=level["level"])
    _ev(events, "policy", level=level["level"], rule_id=level["rule_id"], reason=level["reason"])

    # 记住本轮意图，供"再转一次"使用
    entities = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
    entities["last_intent"] = {"name": intent.get("name"), "slots": intent.get("slots")}
    bankcore.update_session(conn, session_id, recent_entities=entities)

    if level["level"] == "deny":
        streak, state = _circuit_bump(conn, session_id, level.get("rule_id") or "deny")
        audit.append(trace_id, actor="system", action="circuit.fail",
                     payload={"streak": streak, "state": state, "rule_id": level.get("rule_id")},
                     decision="counted")
        msg = f"这个操作我不能执行：{level['reason'] or '按当前策略被拒绝'}。"
        _ev(events, "result", reply=msg, level="deny", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "denied", events, msg, audit)

    if level["level"] == "green":
        out = _execute_and_reply(conn, session_id, trace_id, plan, intent, level, events, audit, user_id)
        return _run_pending_task(conn, session_id, out)

    if level["level"] == "red" and not _red_needs_confirm(plan):
        summary_params = _confirm_params(conn, user_id, plan, level, intent)
        summary = "；".join(f"{label} {value}" for label, value in (summary_params.get("fields") or [])).rstrip("。")
        if summary_params.get("amount") and "锁定预算" not in summary and "还款金额" not in summary:
            summary = f"金额 {summary_params['amount']} 元；" + summary
        challenge = "ch_" + secrets.token_hex(4)
        stepup = {"challenge_id": challenge, "factors": ["sms", "face"],
                  "hint": f"演示验证码 {config.DEMO_STEPUP_CODE}",
                  "expires_at": config.iso_after(STEPUP_TTL), "expires_in": STEPUP_TTL,
                  "fails": 0,
                  "summary": summary or plan.get("goal", "")}
        intent_id = _save_pending(conn, session_id, user_id, plan, level, "awaiting_stepup",
                                  stepup=stepup, trace_id=trace_id)
        _ev(events, "stepup", challenge_id=challenge, factors=stepup["factors"], hint=stepup["hint"],
            intent_id=intent_id, expires_at=stepup["expires_at"], expires_in=STEPUP_TTL,
            summary=stepup["summary"])
        audit.append(trace_id, actor="policy", action="stepup.issued",
                     payload={"challenge": challenge, "summary": stepup["summary"]},
                     decision="awaiting_stepup")
        reason_text = _red_reason(conn, user_id, plan, level,
                                  str(summary_params.get("amount") or ""))
        reply = (f"本次为红色动作：{stepup['summary']}。将进行短信验证码 + 人脸（模拟）验证；"
                 f"验证通过后立即执行且不可撤销。{reason_text.rstrip('。')}。")
        return _finish(conn, trace_id, "awaiting_stepup", events, reply, audit,
                       pending={"intent_id": intent_id, "challenge_id": challenge,
                                "level": "red", "summary": stepup["summary"]})

    # yellow
    params = _confirm_params(conn, user_id, plan, level, intent)
    intent_id = _save_pending(conn, session_id, user_id, plan, level, "awaiting_confirm",
                              params=params, trace_id=trace_id)
    token = conn.execute("SELECT token FROM pending_intents WHERE id=?", (intent_id,)).fetchone()["token"]
    card = dict(params)
    card["params"] = dict(params)          # 同时提供嵌套形状（契约 §9）与平铺字段，两种客户端都兼容
    card["params_hash"] = _hash_params(params)
    card["expires_at"] = config.iso_after(CONFIRM_TTL)
    card["expires_in"] = CONFIRM_TTL
    card["intent_id"] = intent_id
    card["token"] = token
    _ev(events, "confirm", intent_id=intent_id, card=card, expires_at=card["expires_at"],
        expires_in=CONFIRM_TTL, reason=level["reason"])
    audit.append(trace_id, actor="policy", action="confirm.issued",
                 payload={"intent_id": intent_id, "params_hash": card["params_hash"]},
                 decision="awaiting_confirm")
    pending = {"intent_id": intent_id, "card": card, "level": level["level"]}
    card_text = _card_text(params)
    prompt = card_text if card_text.startswith("请确认") else ("请确认：" + card_text)
    return _finish(conn, trace_id, "awaiting_confirm", events, prompt, audit, pending=pending)


def handle_confirm(conn, session_id: str, intent_id: str, token: str,
                   user_id: int = config.DEFAULT_USER_ID) -> dict:
    trace_id = "tr_" + secrets.token_hex(4)
    events: list = []
    audit = trust.AuditLog(conn)
    row = _load_pending(conn, intent_id)
    if not row:
        return _fail(events, "E_NOT_FOUND", "确认请求不存在或已处理")
    if row.get("session_id") and session_id and row["session_id"] != session_id:
        return _fail(events, "E_CONFIRM_TOKEN", "确认请求与当前会话不匹配")
    trace_id = row.get("trace_id") or trace_id
    if row["status"] != "awaiting_confirm":
        return _fail(events, "E_CONFIRM_USED", "该确认已被处理")
    if row["expires_at"] < config.now_iso():
        conn.execute("UPDATE pending_intents SET status='expired' WHERE id=?", (intent_id,))
        return _fail(events, "E_CONFIRM_EXPIRED", "确认卡已过期，请重新发起")
    if not token or not row["token"] or not hmac.compare_digest(str(row["token"]), str(token)):
        audit.append(trace_id, actor="user", action="confirm.bad_token", decision="rejected")
        return _fail(events, "E_CONFIRM_TOKEN", "确认凭证无效")

    plan = json.loads(row["plan_json"])
    params = json.loads(row["card_json"])
    if _hash_params(params) != row["params_hash"]:
        return _fail(events, "E_PARAM", "参数已变更，请重新确认")

    conn.execute("UPDATE pending_intents SET status='confirmed' WHERE id=?", (intent_id,))
    session = bankcore.get_session(conn, session_id) or bankcore.get_or_create_session(conn, session_id, user_id)
    level = evaluate(conn, user_id, plan, session)   # TOCTOU：执行前重跑策略
    audit.append(trace_id, actor="policy", action="policy.recheck",
                 payload={"level": level["level"], "rule_id": level["rule_id"]}, decision=level["level"])
    _ev(events, "policy", level=level["level"], rule_id=level["rule_id"], reason=level["reason"])

    if level["level"] == "deny":
        conn.execute("UPDATE pending_intents SET status='denied' WHERE id=?", (intent_id,))
        streak, state = _circuit_bump(conn, session_id, level.get("rule_id") or "deny")
        audit.append(trace_id, actor="system", action="circuit.fail",
                     payload={"streak": streak, "state": state, "rule_id": level.get("rule_id")},
                     decision="counted")
        msg = f"状态已变化，该操作现在不能执行：{level['reason']}"
        _ev(events, "result", reply=msg, level="deny", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "denied", events, msg, audit)
    if level["level"] == "red":
        challenge = "ch_" + secrets.token_hex(4)
        stepup = {"challenge_id": challenge, "factors": ["sms", "face"],
                  "hint": f"演示验证码 {config.DEMO_STEPUP_CODE}",
                  "expires_at": config.iso_after(STEPUP_TTL), "fails": 0}
        conn.execute("UPDATE pending_intents SET status='awaiting_stepup', stepup_json=? WHERE id=?",
                     (json.dumps(stepup, ensure_ascii=False), intent_id))
        _ev(events, "stepup", challenge_id=challenge, factors=stepup["factors"], hint=stepup["hint"],
            intent_id=intent_id, expires_at=stepup["expires_at"], expires_in=STEPUP_TTL)
        return _finish(events=events, trace_id=trace_id, status="awaiting_stepup", conn=conn,
                       reply="请完成强验证后执行本次操作：回复 6 位验证码（演示 123456）。", audit=audit,
                       pending={"intent_id": intent_id, "challenge_id": challenge})

    intent = {"name": plan.get("intent") or _intent_from_plan(plan), "slots": {}}
    out = _execute_and_reply(conn, session_id, trace_id, plan, intent, level, events, audit, user_id,
                             intent_id=intent_id)
    return _run_pending_task(conn, session_id, out)


def handle_stepup(conn, session_id: str, challenge_id: str, code: str,
                  user_id: int = config.DEFAULT_USER_ID) -> dict:
    trace_id = "tr_" + secrets.token_hex(4)
    events: list = []
    audit = trust.AuditLog(conn)
    row = conn.execute(
        "SELECT * FROM pending_intents WHERE status='awaiting_stepup' AND session_id=? "
        "ORDER BY created_at DESC LIMIT 1", (session_id,)
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM pending_intents WHERE status='awaiting_stepup' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return _fail(events, "E_NOT_FOUND", "没有待验证的操作")
    stepup = json.loads(row["stepup_json"] or "{}")
    if stepup.get("challenge_id") != challenge_id:
        # 也允许按 challenge 查找
        cand = conn.execute("SELECT * FROM pending_intents WHERE stepup_json LIKE ?",
                            (f"%{challenge_id}%",)).fetchone()
        if not cand:
            return _fail(events, "E_NOT_FOUND", "验证请求不存在")
        row, stepup = cand, json.loads(cand["stepup_json"] or "{}")
    trace_id = dict(row).get("trace_id") or trace_id
    if stepup.get("expires_at", "") < config.now_iso():
        return _fail(events, "E_STEPUP_FAIL", "验证码已过期，请重新发起")
    if str(code).strip() != config.DEMO_STEPUP_CODE:
        fails = int(stepup.get("fails", 0)) + 1
        stepup["fails"] = fails
        conn.execute("UPDATE pending_intents SET stepup_json=? WHERE id=?",
                     (json.dumps(stepup, ensure_ascii=False), row["id"]))
        audit.append(trace_id, actor="user", action="stepup.fail", payload={"fails": fails}, decision="rejected")
        streak, state = _circuit_bump(conn, session_id, "stepup_failed")
        audit.append(trace_id, actor="system", action="circuit.fail",
                     payload={"streak": streak, "state": state, "reason": "stepup_failed"},
                     decision="counted")
        if fails >= config.STEPUP_MAX_FAILS:
            conn.execute("UPDATE pending_intents SET status='locked' WHERE id=?", (row["id"],))
            _ev(events, "error", code="E_STEPUP_LOCKED", message="验证失败次数过多，会话已锁定 15 分钟")
            return _finish(conn, trace_id, "locked", events, "验证失败次数过多，已临时锁定。", audit)
        return _fail(events, "E_STEPUP_FAIL", f"验证码不正确（第 {fails} 次）")

    plan = json.loads(row["plan_json"])
    conn.execute("UPDATE pending_intents SET status='verified' WHERE id=?", (row["id"],))
    session = bankcore.get_session(conn, session_id) or bankcore.get_or_create_session(conn, session_id, user_id)
    entities = json.loads(session.get("recent_entities") or "{}")
    contact = _contact_of(conn, user_id, plan)
    entities["last_stepup"] = {"at": config.now_iso(), "payee": str(contact["id"]) if contact else None}
    bankcore.update_session(conn, session_id, recent_entities=entities)
    audit.append(trace_id, actor="user", action="stepup.pass", decision="verified")
    _ev(events, "step", node_id="stepup", tool="StepUpAuth", ok=True, ms=0)

    level = evaluate(conn, user_id, plan, session)
    intent = {"name": plan.get("intent") or _intent_from_plan(plan), "slots": {}}
    out = _execute_and_reply(conn, session_id, trace_id, plan, intent, level, events, audit, user_id,
                             intent_id=row["id"])
    return _run_pending_task(conn, session_id, out)


def handle_cancel(conn, session_id: str, intent_id: str) -> dict:
    trace_id = "tr_" + secrets.token_hex(4)
    events: list = []
    audit = trust.AuditLog(conn)
    _ev(events, "step", node_id="cancel", tool="user.cancel", ok=True, ms=0)
    _ev(events, "result", reply="已取消，本次操作不会执行。", level="green", audit_url=f"/audit/{trace_id}")
    audit.append(trace_id, actor="user", action="confirm.cancel", payload={"intent_id": intent_id},
                 decision="cancelled")
    audit.append(trace_id, actor="system", action="reply.render", payload={"template": "cancelled"},
                 decision="ok")
    return _finish(conn, trace_id, "done", events, "已取消，本次操作不会执行。", audit)


# ------------------------------------------------------------------ 内部
def _intent_from_plan(plan: dict) -> str:
    tools = {n["tool"] for n in plan.get("nodes", [])}
    if tools & {"create_transfer", "submit_transfer", "create_collection_request"}:
        return "transfer"
    if tools & {"analyze_spending", "detect_anomaly", "get_bill_report"}:
        return "bill_analysis"
    if tools & {"cancel_subscription", "get_subscriptions"}:
        return "subscription"
    if tools & {"purchase_wealth", "redeem_wealth", "get_wealth_products", "get_holdings"}:
        return "wealth"
    if tools & {"report_card_lost", "unlock_card", "adjust_card_limit", "apply_virtual_card"}:
        return "card"
    return "standing_plan"


def _save_pending(conn, session_id, user_id, plan, level, status, params=None, stepup=None,
                  trace_id: str | None = None) -> str:
    intent_id = "it_" + secrets.token_hex(4)
    token = secrets.token_hex(8) if status == "awaiting_confirm" else None
    params = params or {}
    conn.execute(
        "INSERT INTO pending_intents(id,session_id,user_id,plan_json,card_json,params_hash,token,status,"
        "expires_at,stepup_json,created_at,trace_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (intent_id, session_id, user_id, json.dumps(plan, ensure_ascii=False),
         json.dumps(params, ensure_ascii=False), _hash_params(params), token, status,
         config.iso_after(CONFIRM_TTL if status == "awaiting_confirm" else STEPUP_TTL),
         json.dumps(stepup, ensure_ascii=False) if stepup else None, config.now_iso(), trace_id),
    )
    return intent_id


def _load_pending(conn, intent_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM pending_intents WHERE id=?", (intent_id,)).fetchone()
    return dict(row) if row else None


CANCEL_EXACT = {"取消", "撤销", "算了", "不用了", "停", "不要了", "先别", "取消吧", "取消掉", "撤销吧"}
CANCEL_PREFIX = ("算了", "先别", "别弄了", "不要了", "不用了", "停一下", "停下")


def _is_cancel(text: str) -> bool:
    """只在"整句就是撤销意图"时拦截，避免"这三个会员我早就不用了"被误判。"""
    t = (text or "").strip().strip("。！!，,、 ")
    if t in CANCEL_EXACT:
        return True
    return len(t) <= 12 and t.startswith(CANCEL_PREFIX)


def _artifacts(res: dict) -> dict:
    arts = {"txn_ids": [], "freeze_ids": [], "plan_ids": []}
    for r in res.get("results", []):
        if not r.get("ok"):
            continue
        d = r.get("data") or {}
        if r["tool"] in ("create_transfer", "submit_transfer", "order_gift", "purchase_wealth") and d.get("txn_id"):
            arts["txn_ids"].append(int(d["txn_id"]))
        if r["tool"] == "freeze_amount" and d.get("freeze_id"):
            arts["freeze_ids"].append(int(d["freeze_id"]))
        if r["tool"] == "create_standing_plan" and d.get("plan_id"):
            arts["plan_ids"].append(int(d["plan_id"]))
    return arts


def _handle_undo(conn, session_id, trace_id, events, audit, user_id):
    session = bankcore.get_session(conn, session_id)
    entities = json.loads((session or {}).get("recent_entities") or "{}")
    row = conn.execute(
        "SELECT * FROM pending_intents WHERE session_id=? AND status IN ('awaiting_confirm','awaiting_stepup') "
        "ORDER BY created_at DESC LIMIT 1", (session_id,)
    ).fetchone()
    if row:
        conn.execute("UPDATE pending_intents SET status='cancelled' WHERE id=?", (row["id"],))
        _ev(events, "step", node_id="cancel", tool="user.cancel", label="取消待确认操作", ok=True, ms=0)
        msg = "已取消，本次操作不会执行。"
        _ev(events, "result", reply=msg, level="green", audit_url=f"/audit/{trace_id}")
        audit.append(trace_id, actor="user", action="confirm.cancel", payload={"intent_id": row["id"]},
                     decision="cancelled")
        return _finish(conn, trace_id, "done", events, msg, audit)

    last = entities.get("last_exec") or {}
    arts = last.get("artifacts") or {}
    done = []
    for pid in arts.get("plan_ids", []):
        if bankcore.cancel_standing_plan(conn, int(pid)).get("ok"):
            done.append("长期计划")
    for fid in arts.get("freeze_ids", []):
        if bankcore.release_freeze(conn, int(fid)).get("ok"):
            done.append("资金冻结")
    for tid in arts.get("txn_ids", []):
        if bankcore.reverse_transaction(conn, int(tid), idem_key=f"undo-{tid}").get("ok"):
            done.append("转账")
    entities.pop("last_exec", None)
    entities.pop("proposal", None)
    bankcore.update_session(conn, session_id, recent_entities=entities)
    if not done:
        msg = "好，那这次就不办了。需要别的随时说。"
        _ev(events, "result", reply=msg, level="green", audit_url=f"/audit/{trace_id}")
        return _finish(conn, trace_id, "done", events, msg, audit)
    audit.append(trace_id, actor="system", action="plan.compensate", payload={"items": done}, decision="reversed")
    msg = "已撤销：" + "、".join(done) + "。"
    if "转账" in done:
        msg += "原流水保留可查，已标记为冲正。"
    else:
        msg += "相关记录保留可查。"
    _ev(events, "step", node_id="compensate", tool="saga.compensate", label="逆序补偿", ok=True, ms=0)
    _ev(events, "result", reply=msg, level="green", audit_url=f"/audit/{trace_id}")
    return _finish(conn, trace_id, "done", events, msg, audit)


def _card_text(params: dict) -> str:
    action = params.get("action") or ""
    if action == "repay_credit_card":
        return "请确认本次信用卡还款："
    if action == "schedule_transfer":
        return "请确认预约转账："
    if action == "reverse_transfer":
        return "请确认撤销这笔转账："
    if action == "apply_virtual_card":
        return "请确认申请虚拟卡："
    if action == "redeem_wealth":
        return "请确认理财赎回："
    if action == "request_password_reset":
        return "请确认发送密码重置链接（不会修改密码）："
    if action == "repay_loan":
        return "请确认本次还款："
    if action == "risk_assessment":
        return "请确认风险测评结果："
    if action == "create_standing_plan":
        if params.get("kind") == "reminder":
            return "请确认这个定时提醒计划（只提醒，不动资金）："
        return f"请确认长期计划：预算 {params.get('amount') or '—'} 元，触发时间见卡片。"
    if action == "freeze_amount":
        return "请确认锁定这笔预算："
    if action == "create_collection_request":
        return f"向好友发起 AA 收款，人均 {params.get('amount')} 元。"
    if action == "cancel_subscription":
        return "请确认取消这项订阅（当前周期内仍可使用）。"
    if params.get("payee_name"):
        return f"向 {params['payee_name']}（尾号 {params.get('payee_tail','——')}）转账 {params.get('amount')} 元，{params.get('arrival','实时')}到账。"
    if params.get("action") == "cancel_subscription":
        return "取消该订阅（当前周期内仍可使用）。"
    if params.get("card_tail"):
        return f"对尾号 {params['card_tail']} 的卡片执行操作。"
    return "执行该操作。"


def _fail(events: list, code: str, message: str) -> dict:
    _ev(events, "error", code=code, message=message)
    return {"trace_id": "tr_" + secrets.token_hex(4), "status": "error", "events": events,
            "reply": message, "pending": None, "error": {"code": code, "message": message}}


def _quick_replies(reply: str) -> list[str]:
    text = reply or ""
    if "没找到「" in text and "收款人" in text:
        return ["妈", "老婆", "房东", "张三", "用手机号后四位"]
    if "赎回哪一笔" in text:
        return ["第1笔 稳盈90天", "第2笔 现金管理", "全部赎回"]
    if "想取消哪一项" in text:
        return ["视频会员A", "云存储B", "健身C", "全退"]
    return []


def _finish(conn, trace_id, status, events, reply, audit, pending=None) -> dict:
    if re.search(r"(每月约|总利息约|月供|本金|估算|利息|计算).{0,30}(?<![\d.])0\.\d{1,2}\s*元", reply or ""):
        reply = SANITY_AMOUNT_REPLY
        status = "done"
    audit.append(trace_id, actor="system", action="reply.render", payload={"status": status, "reply": reply[:300]},
                 decision="ok")
    quick = _quick_replies(reply)
    for event in events:
        if event.get("type") == "result":
            event.setdefault("data", {})["quick_replies"] = quick
    return {"trace_id": trace_id, "status": status, "events": events, "reply": reply,
            "pending": pending, "quick_replies": quick,
            "brain": {"effective": "rules", "available": False, "has_key": False,
                      "note": "规则模式；切换只影响理解层，不影响安全边界"}}


_RANGE_LADDER = {
    "last_7d": ["last_30d", "last_3m"],
    "last_30d": ["last_3m"],
    "this_month": ["last_3m"],
    "last_month": ["last_3m"],
    "last_3m": ["this_year"],
}
_RANGE_CN = {"last_7d": "近 7 天", "last_30d": "近 30 天", "this_month": "本月",
             "last_month": "上月", "last_3m": "近 3 个月", "this_year": "今年"}


def _items_count(res: dict) -> int | None:
    for r in res.get("results") or []:
        if r.get("tool") == "get_transactions" and r.get("ok"):
            return len((r.get("data") or {}).get("items") or [])
    return None


def _plan_with_args(plan: dict, node_id: str, args: dict) -> dict:
    copy = json.loads(json.dumps(plan))
    for node in copy["nodes"]:
        if node["id"] == node_id:
            node["args"] = args
    return copy


def _maybe_adjust_query(conn, trace_id, plan, intent, res, audit, events, user_id):
    """观察→调整：查询没结果时，先扩大时间范围，再放宽方向，而不是直接说"没有"。"""
    if intent.get("name") != "bill_analysis":
        return res, ""
    if str((intent.get("slots") or {}).get("focus")) != "transactions":
        return res, ""
    if _items_count(res) != 0:
        return res, ""
    node = next((n for n in plan.get("nodes") or [] if n["tool"] == "get_transactions"), None)
    if node is None:
        return res, ""
    base = dict(node.get("args") or {})
    cur = base.get("range")
    for nxt in _RANGE_LADDER.get(cur, []):
        trial = dict(base)
        trial["range"] = nxt
        res2 = executor.execute(conn, _plan_with_args(plan, node["id"], trial), trace_id, audit, user_id)
        found = _items_count(res2)
        if found:
            audit.append(trace_id, actor="agent", action="observe.adjust",
                         payload={"from": cur, "to": nxt, "found": found}, decision="adjusted")
            _ev(events, "observe", note=f"{_RANGE_CN.get(cur, cur)}没有，扩大到{_RANGE_CN.get(nxt, nxt)}")
            return res2, f"{_RANGE_CN.get(cur, cur)}没有找到，我扩大到{_RANGE_CN.get(nxt, nxt)}找到了 {found} 笔。"
    if base.get("direction"):
        trial = dict(base)
        trial.pop("direction", None)
        res2 = executor.execute(conn, _plan_with_args(plan, node["id"], trial), trace_id, audit, user_id)
        found = _items_count(res2)
        if found:
            audit.append(trace_id, actor="agent", action="observe.adjust",
                         payload={"from": cur, "drop_direction": True, "found": found}, decision="adjusted")
            _ev(events, "observe", note="没有符合方向的记录，已放宽收支方向")
            return res2, f"没有符合该收支方向的记录，我把方向也放宽了，找到 {found} 笔。"
    return res, ""


def _context_carry(conn, user_id, text: str, entities: dict) -> dict | None:
    """跨轮指代："那上个月呢" / "换成我妈" / "再查一次" —— 沿上一轮查询改过滤条件。"""
    base = entities.get("last_query") or {}
    slots = dict(base.get("slots") or {})
    if not slots or len((text or "").strip()) > 14:
        return None
    t = text.strip()
    changed = False
    if re.search(r"上个月|上月", t):
        slots["range"] = "last_month"; changed = True
    elif re.search(r"这个月|本月", t):
        slots["range"] = "this_month"; changed = True
    elif re.search(r"上周|上个星期", t):
        slots["range"] = "last_7d"; changed = True
    elif re.search(r"最近三个月|近三个月|近3个月", t):
        slots["range"] = "last_3m"; changed = True
    elif re.search(r"今年", t):
        slots["range"] = "this_year"; changed = True
    m = re.search(r"换成\s*([^\s，。]{1,6})", t)
    if m:
        who = re.sub(r"[呢吧啊呀]$", "", re.sub(r"^[我你]", "", m.group(1))).strip("的")
        for c in bankcore.list_contacts(conn, user_id):
            if who in (c.get("alias"), c.get("name")):
                slots["counterparty"] = who
                changed = True
                break
        else:
            for c in bankcore.list_contacts(conn, user_id):
                if who and (phonetic.same_sound(who, str(c.get("alias") or ""))
                            or phonetic.same_sound(who, str(c.get("name") or ""))):
                    slots["counterparty"] = c.get("alias") or c.get("name")
                    changed = True
                    break
    if re.search(r"再查一次|再来一次|再说一遍|重复一下", t):
        changed = True
    if not changed:
        return None
    return {"name": "bill_analysis", "slots": slots, "missing": [], "ambiguity": [],
            "spans": {}, "confidence": 0.9, "source": "context"}

def _execute_and_reply(conn, session_id, trace_id, plan, intent, level, events, audit, user_id,
                       intent_id=None) -> dict:
    for node in executor.topo_order(plan):
        _ev(events, "step", node_id=node["id"], tool=node["tool"],
            label=TOOL_LABEL.get(node["tool"], node["tool"]), ok=True, ms=0)
    res = executor.execute(conn, plan, trace_id, audit, user_id)
    if res.get("ok"):
        _circuit_reset(conn, session_id)
    res, adjust_note = _maybe_adjust_query(conn, trace_id, plan, intent, res, audit, events, user_id)
    # 用真实耗时刷新 step 事件
    ms_by_node = {r["node_id"]: r["ms"] for r in res["results"]}
    ok_by_node = {r["node_id"]: r["ok"] for r in res["results"]}
    for ev in events:
        if ev["type"] == "step" and ev["data"].get("node_id") in ms_by_node:
            ev["data"]["ms"] = ms_by_node[ev["data"]["node_id"]]
            ev["data"]["ok"] = ok_by_node[ev["data"]["node_id"]]

    if res.get("ok"):
        _remember_result(conn, session_id, trace_id, res)
    ledger = trust.FactLedger()
    ledger.add(_auto_facts(res["results"]))
    for r in res["results"]:
        if r.get("facts"):
            ledger.add(r["facts"])

    if not res["ok"]:
        err = res.get("error") or {"code": "E_INTERNAL", "message": "执行失败"}
        msg = f"操作未完成：{err.get('message')}。"
        if res.get("compensations"):
            msg += "已撤销已执行的部分。"
        _ev(events, "error", code=err.get("code"), message=msg)
        _ev(events, "result", reply=msg, level=level["level"], audit_url=f"/audit/{trace_id}")
        if intent_id:
            conn.execute("UPDATE pending_intents SET status='failed' WHERE id=?", (intent_id,))
        return _finish(conn, trace_id, "error", events, msg, audit)

    tx = next((r for r in res["results"] if r["tool"] == "get_transactions" and r["ok"]), None)
    if tx and not any(r["tool"] == "analyze_spending" for r in res["results"]):
        total = sum(int(i.get("amount_cents") or 0) for i in (tx["data"].get("items") or []))
        ledger.add([{"id": "tx_total", "key": "total", "kind": "money", "value": total, "unit": "CNY"}])
    reply = _compose_reply(intent.get("name"), res, level)
    if adjust_note:
        reply = adjust_note + "\n" + reply
    if res.get("ok") and intent.get("name") == "bill_analysis":
        ents = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
        if (intent.get("slots") or {}).get("focus") == "transactions":
            ents["last_query"] = {"name": "bill_analysis", "slots": dict(intent.get("slots") or {})}
            bankcore.update_session(conn, session_id, recent_entities=ents)
    ok, offenders = trust.postcheck(reply, ledger)
    if not ok:
        audit.append(trace_id, actor="system", action="fact_check_failed", payload={"offenders": offenders[:5]},
                     decision="fallback")
        reply = _fallback_reply(res)
    audit.append(trace_id, actor="system", action="reply.render",
                 payload={"facts": len(ledger.all()), "postcheck": ok}, decision="ok")
    entities = json.loads((bankcore.get_session(conn, session_id) or {}).get("recent_entities") or "{}")
    entities["last_exec"] = {"plan_id": plan.get("plan_id"), "artifacts": _artifacts(res)}
    if (intent or {}).get("name") == "subscription" and "想取消哪一项" in reply:
        items = next((r["data"].get("items") or [] for r in res["results"] if r["tool"] == "get_subscriptions"), [])
        entities["awaiting"] = {"kind": "subscription_choice",
                                "merchants": [i.get("merchant") for i in items[:5] if i.get("merchant")]}
    if any(r["tool"] == "cancel_subscription" and r["ok"] for r in res["results"]):
        entities.pop("awaiting", None)
    for r in res["results"]:
        if not r.get("ok"):
            continue
        data = r.get("data") or {}
        if r["tool"] == "get_subscriptions" and data.get("items"):
            entities["subscriptions"] = data["items"]
        elif r["tool"] == "get_contacts" and data.get("items"):
            entities["contacts"] = data["items"]
        elif r["tool"].startswith("get_wealth_products") and data.get("items"):
            entities["wealth_products"] = data["items"]
    bankcore.update_session(conn, session_id, recent_entities=entities)
    txn_no = next((r["data"].get("txn_no") for r in res["results"] if r["data"].get("txn_no")), None)
    _ev(events, "result", reply=reply, level=level["level"], txn_no=txn_no,
        audit_url=f"/audit/{trace_id}")
    if intent_id:
        conn.execute("UPDATE pending_intents SET status='done' WHERE id=?", (intent_id,))
    return _finish(conn, trace_id, "done", events, reply, audit)


def _auto_facts(results: list[dict]) -> list[dict]:
    """兜底：把 data 里所有 *_cents / *_no 自动登记为事实，防止模板漏报。"""
    facts: list[dict] = []
    idx = 0

    def walk(obj):
        nonlocal idx
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, int) and k.endswith("_cents"):
                    idx += 1
                    facts.append({"id": f"auto{idx}", "key": k, "kind": "money", "value": v, "unit": "CNY"})
                    if v < 0:
                        idx += 1
                        facts.append({"id": f"auto{idx}", "key": k, "kind": "money",
                                      "value": abs(v), "unit": "CNY"})
                elif isinstance(v, str) and (k.endswith("_no") or k in ("ticket_no", "order_no")):
                    idx += 1
                    facts.append({"id": f"auto{idx}", "key": k, "kind": "id", "value": v, "unit": None})
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    for r in results:
        walk(r.get("data") or {})
    return facts


def _compose_reply(intent_name: str, res: dict, level: dict) -> str:
    by_tool = {r["tool"]: r["data"] for r in res["results"] if r["ok"]}

    if "schedule_transfer" in by_tool:
        d = by_tool["schedule_transfer"]
        return (f"已预约向 {d.get('payee', '收款人')} 转账 {_money(d.get('amount_cents', 0))} 元，"
                f"执行时间 {str(d.get('run_at', '')).replace('T', ' ')[:16]}；当前未扣款。")
    if "reverse_transfer" in by_tool:
        d = by_tool["reverse_transfer"]
        return (f"已撤销原转账，反向交易号 {d.get('reversal_txn_no', '')}，"
                f"金额 {_money(d.get('amount_cents', 0))} 元，"
                f"当前余额 {_money(d.get('balance_after_cents', d.get('balance_after', 0)))} 元。")
    if "redeem_wealth" in by_tool:
        d = by_tool["redeem_wealth"]
        return (f"已赎回理财 {_money(d.get('amount_cents', 0))} 元，"
                f"流水号 {d.get('txn_no', '')}，当前余额 {_money(d.get('balance_after', 0))} 元。")
    if "repay_credit_card" in by_tool:
        d = by_tool["repay_credit_card"]
        return (f"已归还信用卡 {_money(d.get('amount_cents', 0))} 元，"
                f"剩余已用额度 {_money(d.get('used_after', 0))} 元，"
                f"流水号 {d.get('txn_no', '')}，当前余额 {_money(d.get('balance_after', 0))} 元。")
    if "repay_loan" in by_tool:
        d = by_tool["repay_loan"]
        return (f"已归还贷款 {_money(d.get('amount_cents', 0))} 元，"
                f"剩余本金 {_money(d.get('remaining_cents', 0))} 元，"
                f"流水号 {d.get('txn_no', '')}，当前余额 {_money(d.get('balance_after', 0))} 元。")
    if "risk_assessment" in by_tool:
        d = by_tool["risk_assessment"]
        return (f"风险测评已完成：{d.get('level')}（{d.get('score')} 分），"
                f"有效期至 {str(d.get('expires_at', ''))[:10]}。")
    if "request_password_reset" in by_tool:
        d = by_tool["request_password_reset"]
        return (f"已生成密码重置凭证 {d.get('reset_no', '')}，"
                f"通过{'邮箱' if d.get('channel') == 'email' else '短信'}发送一次性链接，"
                f"15 分钟内有效；对话中不会收集新密码。")

    if "create_transfer" in by_tool or "submit_transfer" in by_tool:
        d = by_tool.get("create_transfer") or by_tool.get("submit_transfer")
        return (f"已向{d.get('payee_name','收款人')}转账 {_money(d.get('amount_cents',0))} 元，"
                f"交易单号 {d.get('txn_no','')}，当前余额 {_money(d.get('balance_after',0))} 元。")

    if "purchase_wealth" in by_tool:
        d = by_tool["purchase_wealth"]
        return (f"已申购「{d.get('product_name','')}」{_money(d.get('amount_cents',0))} 元，"
                f"订单号 {d.get('txn_no','')}，确认后不可撤单。")

    if "report_card_lost" in by_tool:
        d = by_tool["report_card_lost"]
        return (f"已挂失尾号 {d.get('card_tail','')} 的卡片，工单号 {d.get('ticket_no','')}，"
                f"补卡约需 {d.get('replace_days','3-5 个工作日')}。")

    if "unlock_card" in by_tool:
        d = by_tool["unlock_card"]
        return f"已解除尾号 {d.get('card_tail','')} 的挂失，卡片恢复使用。"

    if "cancel_subscription" in by_tool:
        items = [r["data"] for r in res["results"] if r["tool"] == "cancel_subscription" and r["ok"]]
        names = "、".join(i.get("merchant", "订阅") for i in items)
        return f"已取消 {len(items)} 项自动扣费：{names}。当前周期内仍可使用。"

    if "create_standing_plan" in by_tool:
        d = by_tool["create_standing_plan"]
        frozen = by_tool.get("freeze_amount") or {}
        extra = f"，已锁定预算 {_money(frozen.get('amount_cents', 0))} 元" if frozen.get("amount_cents") else ""
        return f"已创建长期计划「{d.get('goal','')}」{extra}，下次运行时间 {str(d.get('next_run_at',''))[:10]}。"

    if "freeze_amount" in by_tool:
        d = by_tool["freeze_amount"]
        return f"已锁定 {_money(d.get('amount_cents',0))} 元，当前可用余额 {_money(d.get('available_cents',0))} 元。"

    if "create_collection_request" in by_tool:
        d = by_tool["create_collection_request"]
        return (f"已向 {d.get('count',0)} 位好友各发起 {_money(d.get('amount_each_cents',0))} 元收款请求，"
                f"合计 {_money(d.get('total_cents',0))} 元（资金流入）。")

    if "order_gift" in by_tool:
        d = by_tool["order_gift"]
        return f"已下单：{d.get('item','')}，金额 {_money(d.get('amount_cents',0))} 元，由外部商户配送。"

    if intent_name == "card" and "get_accounts" in by_tool:
        cards = (by_tool["get_accounts"] or {}).get("cards") or []
        if cards:
            c = cards[0]
            status = {"active": "正常", "lost": "已挂失", "frozen": "已冻结"}.get(c.get("status"), c.get("status"))
            limit = _int(c.get("daily_limit_cents", c.get("daily_limit")))
            used = _int(c.get("used_cents", c.get("used")))
            return (f"你有 {len(cards)} 张卡：尾号 {c.get('card_tail')} 的信用卡，"
                    f"额度 {_money(limit)} 元，"
                    f"已用 {_money(used)} 元，状态{status}。")

    if "get_accounts" in by_tool:
        d = by_tool["get_accounts"]
        accounts = d.get("accounts") or []
        cur = next((a for a in accounts if a.get("type") == "current"), accounts[0] if accounts else {})
        return f"你当前可用余额为 {_money(cur.get('available_cents', 0))} 元。"

    if "get_transactions" in by_tool and "analyze_spending" not in by_tool:
        d = by_tool["get_transactions"]
        items = d.get("items") or []
        label = d.get("range_label") or "近期"
        who = d.get("counterparty") or ""
        direction = d.get("direction") or ""
        total = sum(int(i.get("amount_cents") or 0) for i in items)
        if not items:
            scope = (who + "的") if who else ""
            flow = {"in": "转入", "out": "转出"}.get(direction, "")
            return f"{label}没有找到{scope}{flow}记录。"
        if direction == "in":
            head = f"{label}，{who or '对方'}共向你转账 {len(items)} 笔，合计 {_money(total)} 元。"
        elif direction == "out":
            head = f"{label}，你共向{who or '对方'}转出 {len(items)} 笔，合计 {_money(total)} 元。"
        elif who:
            head = f"{label}，你和{who}之间共 {len(items)} 笔流水。"
        else:
            head = f"{label}共 {len(items)} 笔。"
        rows = items[:5]
        lines = "\n".join(
            f"· {str(i.get('created_at'))[5:10]} {i.get('counterparty') or ''} "
            f"{'收入' if i.get('direction') == 'in' else '支出'} "
            f"{_money(int(i.get('amount_cents') or 0))} 元"
            for i in rows
        )
        more = f"\n（还有 {len(items) - len(rows)} 笔，回复「全部展示」看完整流水）" if len(items) > len(rows) else ""
        return head + "\n" + lines + more

    if "analyze_spending" in by_tool:
        d = by_tool["analyze_spending"]
        if d.get("category"):
            return (f"{d.get('range_label','本月')}{d['category']}支出合计 "
                    f"{_money(d.get('total_cents',0))} 元，共 {d.get('txn_count',0)} 笔。")
        cats = [c for c in (d.get("by_category") or []) if c.get("category") != "还款"] or (d.get("by_category") or [])
        top = cats[:3]
        parts = "、".join(f"{c['category']} {_money(c['amount_cents'])} 元" for c in top)
        extra = ""
        largest = d.get("largest_txn") or {}
        if largest.get("amount_cents"):
            extra = (f"最大一笔：{largest.get('created_at','')} {largest.get('counterparty','')} "
                     f"{_money(int(largest['amount_cents']))} 元。")
        change = d.get("top_change") or {}
        if change.get("delta_cents"):
            verb = "增加" if change["delta_cents"] > 0 else "减少"
            extra += (f"变化最大：{change.get('category','其他')} {verb} "
                      f"{_money(abs(int(change['delta_cents'])))} 元。")
        return (f"{d.get('range_label','本月')}支出合计 {_money(d.get('total_cents',0))} 元，"
                f"比上月{'多' if (d.get('mom_ratio') or 0) >= 0 else '少'} "
                f"{_money(abs(d.get('total_cents',0) - d.get('prev_total_cents',0)))} 元。前三类：{parts}。{extra}")

    if "get_bill_report" in by_tool:
        d = by_tool["get_bill_report"]
        return (f"月报已生成：{d.get('range_label','本月')}支出 {_money(d.get('total_cents',0))} 元，"
                f"储蓄率 {round((d.get('saving_ratio') or 0) * 100)}%。")

    if "get_subscriptions" in by_tool:
        d = by_tool["get_subscriptions"]
        items = d.get("items") or []
        active = [i for i in items if i.get("status") == "active"]
        cancelled = [i for i in items if i.get("status") == "cancelled"]
        unused = [i for i in active if (i.get("unused_days") or 0) >= 60]
        head = f"在用 {len(active)} 项自动扣费"
        if unused:
            names = "、".join(i["merchant"] for i in unused[:3])
            text = f"{head}，其中 {len(unused)} 项近 6 个月未使用：{names}。"
        else:
            text = head + "。"
        if cancelled:
            names = "、".join(i["merchant"] for i in cancelled[:3])
            text += f" 已取消 {len(cancelled)} 项：{names}。"
        if intent_name == "subscription" and active:
            example = active[0].get("merchant") or "第一项"
            text += f"想取消哪一项？说名字就行，比如「退掉{example}」；也可以说「全关」。"
        return text

    if "detect_anomaly" in by_tool:
        d = by_tool["detect_anomaly"]
        items = d.get("items") or []
        if not items:
            return "本月没有发现需要特别留意的交易。"
        rule_text = {
            "late_night": "凌晨时段的大额消费",
            "same_merchant_1h": "同一商户短时间多笔",
            "outlier": "金额显著高于该商户历史均值",
        }
        first = items[0]
        label = rule_text.get(first.get("rule"), "规则命中")
        merchant = first.get("counterparty") or "某商户"
        amount = _money(_int(first.get("amount_cents")))
        return (f"发现 {len(items)} 笔需要留意，例如 {merchant} {amount} 元（{label}）。\n"
                f"回复「查看详情」可看每一笔的时间、商户和原因。")

    if "get_contacts" in by_tool and "quote_transfer" in by_tool:
        d = by_tool["quote_transfer"]
        return f"已找到 {d.get('payee_name','收款人')}，转账 {_money(d.get('amount_cents',0))} 元。"

    if "notify" in by_tool:
        return "已发送通知。"

    if not by_tool:
        return "我没能完成这笔操作：没有生成任何执行结果。请补充信息或转人工。"
    return "我没拿到可确认的回执，不能声称成功；请查看审计或转人工。"


def _fallback_reply(res: dict) -> str:
    for r in res["results"]:
        if not r.get("ok"):
            continue
        tool, data = r["tool"], r["data"]
        if tool in ("create_transfer", "submit_transfer"):
            return f"转账已提交，交易单号 {data.get('txn_no','')}。"
        if tool == "redeem_wealth":
            return (f"已赎回理财 {_money(data.get('amount_cents', 0))} 元，"
                    f"流水号 {data.get('txn_no', '')}，当前余额 {_money(data.get('balance_after', 0))} 元。")
        if tool == "repay_credit_card":
            return (f"已归还信用卡 {_money(data.get('amount_cents', 0))} 元，"
                    f"剩余已用额度 {_money(data.get('used_after', 0))} 元，"
                    f"流水号 {data.get('txn_no', '')}，当前余额 {_money(data.get('balance_after', 0))} 元。")
        if tool == "repay_loan":
            return (f"已归还贷款 {_money(data.get('amount_cents', 0))} 元，"
                    f"剩余本金 {_money(data.get('remaining_cents', 0))} 元，"
                    f"流水号 {data.get('txn_no', '')}，当前余额 {_money(data.get('balance_after', 0))} 元。")
        if tool == "risk_assessment":
            return (f"风险测评已完成：{data.get('level')}（{data.get('score')} 分），"
                    f"有效期至 {str(data.get('expires_at', ''))[:10]}。")
        if tool == "request_password_reset":
            return (f"已生成密码重置凭证 {data.get('reset_no', '')}，"
                    f"通过{'邮箱' if data.get('channel') == 'email' else '短信'}发送一次性链接，"
                    f"15 分钟内有效；对话中不会收集新密码。")
    return "我没拿到可确认的回执，不能声称成功；请查看审计或转人工。"


def get_audit(conn, trace_id: str) -> dict:
    log = trust.AuditLog(conn)
    events = log.list(trace_id)
    return {"trace_id": trace_id, "events": events, "chain_ok": log.verify_chain(trace_id)}


def get_state(conn, session_id: str) -> dict:
    session = bankcore.get_session(conn, session_id)
    row = conn.execute(
        "SELECT * FROM pending_intents WHERE session_id=? AND status IN ('awaiting_confirm','awaiting_stepup') "
        "ORDER BY created_at DESC LIMIT 1", (session_id,)
    ).fetchone()
    pending = None
    if row:
        card = json.loads(row["card_json"] or "{}")
        ttl = CONFIRM_TTL if row["status"] == "awaiting_confirm" else STEPUP_TTL
        card.setdefault("expires_in", ttl)
        pending = {"intent_id": row["id"], "status": row["status"],
                   "card": card, "expires_at": row["expires_at"], "expires_in": ttl}
    return {"state": (session or {}).get("state", "NORMAL"), "pending": pending}
