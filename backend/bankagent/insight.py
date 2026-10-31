"""业务问答：限额、订阅年费、最近一笔、联系人、到账规则、强验证原因。

这些是"懂业务"的确定性回答，不经过意图理解，也不调模型。
"""

from __future__ import annotations

import json
import re

from . import bankcore, config, tools
from .normalize import cn_to_int, normalize

_CATEGORIES = ("餐饮", "购物", "交通", "娱乐", "居住", "医疗", "教育", "转账")


def _money(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def _parse_money(text: str) -> int | None:
    """把"10万 / 1.2万 / 五千 / 10000"解析成分。"""
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(万|千)?", text)
    if m:
        value = float(m.group(1))
        if m.group(2) == "万":
            value *= 10000
        elif m.group(2) == "千":
            value *= 1000
        return int(round(value * 100))
    m = re.search(r"([零〇一二两三四五六七八九十百千万亿]+)", text)
    if m:
        number = cn_to_int(m.group(1))
        if number:
            return int(number) * 100
    return None


def _parse_term_years(text: str) -> float | None:
    t = str(text or "")
    if "活期" in t:
        return 0.25
    if "半年" in t:
        return 0.5
    m = re.search(r"(\d+)\s*个?月", t)
    if m:
        return int(m.group(1)) / 12
    m = re.search(r"([一二两三四五六七八九十]+)\s*个?月", t)
    if m:
        months = cn_to_int(m.group(1))
        if months:
            return months / 12
    m = re.search(r"(\d+)\s*年", t)
    if m:
        return float(m.group(1))
    m = re.search(r"([一二两三四五六七八九十]+)\s*年", t)
    if m:
        years = cn_to_int(m.group(1))
        if years:
            return float(years)
    return None


def _deposit(text: str) -> str | None:
    if not re.search(r"(存|存款|利息)", text) or re.search(r"(贷|借|分期)", text):
        return None
    amount = _parse_money(text)
    years = _parse_term_years(text) or 1.0
    if not amount:
        return None
    rates = [(0.25, 0.002), (0.5, 0.013), (1, 0.0145), (2, 0.0165), (3, 0.0195), (5, 0.02)]
    rate = min(rates, key=lambda item: abs(item[0] - years))[1]
    interest = amount * rate * years
    return (f"按演示利率，{_money(amount)} 元存 {years:g} 年，"
            f"年利率约 {rate * 100:.2f}%，到期利息约 {_money(round(interest))} 元，"
            f"本息合计 {_money(round(amount + interest))} 元。")


def _loan(text: str) -> str | None:
    if re.search(r"(帮|我要|要|给我|替我).{0,4}(还房贷|还贷款|还款)", text):
        return None
    if re.search(r"(还房贷|还贷款|还款)", text) and not re.search(r"(利息|月供|每月还|还多少|试算|计算|利率)", text):
        return None
    if not re.search(r"(贷款|借款|借|月供|房贷|车贷|每月还)", text):
        return None
    if not re.search(r"(利息|月供|每月还|还多少|试算|计算|利率)", text):
        return None
    amount = _parse_money(text)
    years = _parse_term_years(text) or 1.0
    if not amount:
        return None
    months = max(1, int(round(years * 12)))
    annual = 0.0345
    monthly = annual / 12
    factor = (1 + monthly) ** months
    payment = amount * monthly * factor / (factor - 1) if monthly else amount / months
    total = payment * months
    return (f"按年利率 3.45%、{months} 期等额本息估算："
            f"每月约 {_money(round(payment))} 元，总利息约 {_money(round(total - amount))} 元。")


def _installment(text: str) -> str | None:
    if not re.search(r"(分期|分\s*\d+\s*期)", text):
        return None
    m = re.search(r"(\d+)\s*期", text)
    if not m:
        return None
    periods = max(1, min(int(m.group(1)), 36))
    amount = _parse_money(text)
    if not amount:
        return None
    fee_rate = 0.006
    fee = amount * fee_rate * periods
    monthly = amount / periods + fee / periods
    return (f"{_money(amount)} 元分 {periods} 期（演示费率 0.6%/期）："
            f"每期约 {_money(round(monthly))} 元，手续费合计约 {_money(round(fee))} 元。")


def _budget(conn, user_id: int, text: str) -> str | None:
    m = re.search(
        rf"(?:设置|把|将)?\s*(?:({'|'.join(_CATEGORIES)}))?预算"
        rf"(?:设为|设置成|改成|定成|是)?\s*([0-9]+(?:\.[0-9]+)?)\s*(万)?\s*(?:元|块)?",
        text,
    )
    if m:
        category = m.group(1) or "总"
        amount = float(m.group(2)) * (10000 if m.group(3) else 1)
        cents = int(round(amount * 100))
        conn.execute(
            "INSERT INTO budgets(user_id,category,amount_cents,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,category) DO UPDATE SET amount_cents=excluded.amount_cents, "
            "updated_at=excluded.updated_at",
            (user_id, category, cents, config.now_iso()),
        )
        return f"已把「{category}」本月预算设为 {_money(cents)} 元。"

    if re.search(r"预算", text) and re.search(r"(还剩|剩余|情况|多少|用了|超)", text):
        rows = conn.execute(
            "SELECT category, amount_cents FROM budgets WHERE user_id=? ORDER BY category",
            (user_id,),
        ).fetchall()
        if not rows:
            return "还没有设置预算。可以说「把餐饮预算设为 1500 元」。"
        spend = tools.tool_analyze_spending(conn, user_id, {"range": "this_month"})
        data = spend.get("data") or {}
        by_cat = {c["category"]: int(c["amount_cents"]) for c in (data.get("by_category") or [])}
        total_spent = int(data.get("total_cents") or 0)
        lines = []
        for row in rows:
            category = row["category"]
            budget = int(row["amount_cents"])
            used = total_spent if category == "总" else by_cat.get(category, 0)
            left = budget - used
            state = f"还剩 {_money(left)} 元" if left >= 0 else f"超支 {_money(-left)} 元"
            lines.append(f"· {category}：预算 {_money(budget)} 元，已用 {_money(used)} 元，{state}")
        return "本月预算情况：\n" + "\n".join(lines)
    return None


def _search(conn, user_id: int, text: str) -> str | None:
    if not re.search(r"(搜|查|找).{0,8}(交易|流水|消费|记录)", text):
        return None
    rows = bankcore.list_transactions(conn, user_id, limit=500)
    names = {str(r.get("counterparty") or "") for r in rows if r.get("counterparty")}
    keyword = next((name for name in names if len(name) >= 2 and name in text), None)
    if not keyword:
        return None
    matched = [r for r in rows if r.get("counterparty") == keyword]
    total = sum(int(r.get("amount_cents") or 0) for r in matched)
    lines = [f"· {str(r.get('created_at'))[:10]} {r.get('counterparty') or ''} {_money(int(r.get('amount_cents') or 0))} 元"
             for r in matched[:8]]
    more = f"\n（还有 {len(matched) - 8} 笔）" if len(matched) > 8 else ""
    return (f"找到与「{keyword}」相关的 {len(matched)} 笔交易，合计 {_money(total)} 元：\n"
            + "\n".join(lines) + more)


def _daily_used(conn, user_id: int) -> int:
    row = conn.execute(
        "SELECT used FROM daily_quota WHERE user_id=? AND biz_date=?",
        (user_id, config.now_dt().date().isoformat()),
    ).fetchone()
    return int(row["used"]) if row else 0


def _contacts(conn, user_id: int) -> str:
    items = bankcore.list_contacts(conn, user_id)
    if not items:
        return "你的通讯录里还没有联系人。"
    parts = []
    for c in items:
        alias = c.get("alias")
        parts.append(f"{alias}（{c['name']}）" if alias else c["name"])
    return f"你有 {len(items)} 个常用联系人：" + "、".join(parts) + "。"


def _quota(conn, user_id: int) -> str:
    used = _daily_used(conn, user_id)
    limit = config.LIMIT_DAILY
    if used > limit:
        return (f"今日免密额度已用完（已用 {_money(used)} / 限额 {_money(limit)} 元，"
                f"超出 {_money(used - limit)} 元），后续转账需强验证。\n"
                f"单笔硬上限 50,000 元。")
    left = max(0, limit - used)
    return (f"今日免密额度 {_money(limit)} 元，已用 {_money(used)} 元，还剩 {_money(left)} 元。\n"
            f"单笔超过 {_money(limit)} 元、或给新收款人转账，会升级为强验证；单笔硬上限 50,000 元。")


def _annual_subs(conn, user_id: int) -> str:
    subs = [s for s in bankcore.list_subscriptions(conn, user_id) if s.get("status") == "active"]
    if not subs:
        return "你当前没有生效中的自动扣费。"
    total = 0
    for s in subs:
        cycle = int(s.get("cycle_days") or 30)
        total += int(s["amount_cents"]) * 365 / max(cycle, 1)
    detail = "、".join(f"{s['merchant']}（{_money(int(s['amount_cents']))} 元/{int(s.get('cycle_days') or 30)} 天）"
                       for s in subs[:4])
    return f"按当前 {len(subs)} 项订阅折算，一年大约 {_money(round(total))} 元。\n前几项：{detail}。"


def _latest(conn, user_id: int) -> str:
    rows = bankcore.list_transactions(conn, user_id, limit=1)
    if not rows:
        return "还没有交易记录。"
    t = rows[0]
    direction = "收入" if t.get("direction") == "in" else "支出"
    return (f"最近一笔：{str(t.get('created_at'))[:10]} {t.get('counterparty') or ''} "
            f"{direction} {_money(int(t.get('amount_cents') or 0))} 元，"
            f"状态 {'成功' if t.get('status') == 'succeeded' else t.get('status')}。"
            f"需要看完整决策链，点结果卡下方的「查看审计」。")


_TERM_RE = re.compile(r"(活期|半年|一年|两年|三年|五年|\d+\s*年|\d+\s*个月|\d+\s*期)")
_FOLLOW_RE = re.compile(
    r"\s*(?:改成|换成|存|借|分|要|那)?\s*(活期|半年|一年|两年|三年|五年|\d+\s*年|\d+\s*个月|\d+\s*期)\s*(?:期|呢|吧)?\s*[？?]?\s*"
)


def _save_calc(conn, session_id: str | None, kind: str, text: str) -> None:
    if not session_id:
        return
    sess = bankcore.get_session(conn, session_id) or {}
    ents = json.loads(sess.get("recent_entities") or "{}")
    ents["last_calc"] = {"kind": kind, "text": text}
    bankcore.update_session(conn, session_id, recent_entities=ents)


def _rewrite_calc(last_text: str, follow: str) -> str:
    base = _TERM_RE.sub("", last_text or "").strip()
    return (base + " " + follow).strip()



def _loan_status(conn, user_id: int, text: str) -> str | None:
    if not re.search(r"(房贷|贷款).{0,6}(进度|计划|剩|还|多少期)|还款计划|还款进度", text):
        return None
    loan = bankcore.loan_view(conn, user_id)
    if not loan:
        return "当前没有生效中的贷款。"
    return (f"房贷（演示）：剩余本金 {_money(loan['remaining_cents'])} 元，"
            f"已还 {loan['paid_periods']}/{loan['term_months']} 期，"
            f"月供 {_money(loan['monthly_cents'])} 元，"
            f"下次还款 {loan['next_due']}。")

def _credit_card(conn, user_id: int, text: str) -> str | None:
    if re.search(r"(还|归还).{0,4}信用卡|信用卡.{0,4}还款", text) and re.search(r"\d", text):
        return None
    if not re.search(r"信用卡|最低还款|本期账单|信用卡账单", text):
        return None
    card = bankcore.credit_card_view(conn, user_id)
    if not card:
        return "你当前没有信用卡。"
    return (f"信用卡（尾号 {card['card_tail']}）：本期账单 {_money(card['bill_cents'])} 元，"
            f"最低还款 {_money(card['minimum_cents'])} 元；额度 {_money(card['limit_cents'])} 元，"
            f"已用 {_money(card['used_cents'])} 元，可用 {_money(card['available_cents'])} 元。")

def _points(conn, user_id: int, text: str) -> str | None:
    if "积分" not in text:
        return None
    points = bankcore.get_points(conn, user_id)
    return (f"当前积分 {points['balance']:,} 分；其中 {points['expiring']:,} 分将于 "
            f"{points['expire_at']} 到期。演示环境暂不支持积分兑换。")

def _branches(text: str) -> str | None:
    if not re.search(r"(网点|排队|支行|营业厅)", text):
        return None
    return ("附近网点（演示）：\n"
            "· 城西支行 0.8km，排队 4 人，预计 12 分钟\n"
            "· 中心支行 1.9km，排队 9 人，预计 25 分钟\n"
            "· 高新支行 3.2km，排队 2 人，预计 6 分钟")

def _rates(text: str) -> str | None:
    if not re.search(r"(汇率|牌价|美元|欧元|日元)", text):
        return None
    return ("今日参考汇率（演示）：USD/CNY 7.1200，EUR/CNY 7.7800，JPY/CNY 0.0480；"
            "结售汇点差 0.1%，以实时牌价为准。")

def answer(conn, user_id: int, text: str, session_id: str | None = None) -> str | None:
    t = (text or "").strip()
    if session_id and _FOLLOW_RE.fullmatch(t):
        sess = bankcore.get_session(conn, session_id) or {}
        ents = json.loads(sess.get("recent_entities") or "{}")
        last = ents.get("last_calc") or {}
        if last.get("text"):
            t = _rewrite_calc(str(last["text"]), t)
        else:
            return "先告诉我金额和期限，例如「10万存一年利息多少」。"
    reply = _budget(conn, user_id, t)
    if reply:
        return reply
    reply = _search(conn, user_id, t)
    if reply:
        return reply
    for kind, calc in (("installment", _installment), ("loan", _loan), ("deposit", _deposit)):
        reply = calc(t)
        if reply:
            _save_calc(conn, session_id, kind, t)
            return reply
    for read_only in (_loan_status, _credit_card, _points, _branches, _rates):
        reply = (read_only(conn, user_id, t)
                 if read_only in (_loan_status, _credit_card, _points) else read_only(t))
        if reply:
            return reply
    if (re.search(r"(还能转多少|转账额度|剩余额度|今天.*额度|限额|额度.*(还剩|剩余|多少))", t)
            and not re.search(r"(把|调|改|设置).{0,6}(限额|额度)", t)):
        return _quota(conn, user_id)
    if re.search(r"(一年|全年).*(订阅|会员|扣费)|订阅.*(一年|全年)", t):
        return _annual_subs(conn, user_id)
    if (re.search(r"(刚才那笔|上一笔|最近一笔|到哪了|交易进度)", t)
            and not re.search(r"(撤销|取消|冲正|退回).{0,8}(转账|转的|汇款|那笔)", t)):
        return _latest(conn, user_id)
    if re.search(r"(我的)?联系人|收款人有哪些|通讯录", t):
        return _contacts(conn, user_id)
    if re.search(r"为什么.*(强验证|验证|锁定|确认)", t):
        return ("大额转账、新收款人、夜间或 IM 渠道的操作，会被升级为红色强验证；"
                "验证方式为短信 + 人脸/设备指纹。目的是在手机被盗、被胁迫或误操作时保护资金，"
                "强验证失败会临时锁定会话，查询不受影响。")
    if re.search(r"(手续费|到账时间|多久到账|多长时间到账)", t):
        return ("本演示中行内及常用收款人实时到账，不收取手续费；"
                "真实银行跨行转账通常 2 小时内到账，具体以柜台/APP 公示为准。")
    return None
