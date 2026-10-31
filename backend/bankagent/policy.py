"""策略引擎（Policy Engine）：权限矩阵 + 升降级信号 + 硬拒绝。

规则即策略资产，可单测：
    本模块是**纯函数**——只读 ``config`` / ``specs`` 里的常量，不 import bankcore / db，
    不读墙上时钟、不写审计、不抛业务异常，因此可以对着文档 §8.3 的 40 条边界用例
    逐条回归；``bankagent/rules/policy.rego`` 是同一套规则的一一对应镜像，答辩时
    用它证明"权限是能 diff、能单测、能评审的策略资产，不是散落在 if-else 里的魔数"。

裁决顺序（优先级从高到低，与文档 §6.2.1 / §6.2.2 / §6.2.3 一致）：

    0. 硬拒绝 deny     —— 密码类 / 未登记动作 / 系统内部动作；
    1. base_level      —— 只查矩阵（``specs.TOOL_SPECS`` 的 group）+ 金额类硬红；
    2. escalations     —— im_channel / night / new_device / high_risk，**只作用于 base=yellow**；
    3. discount        —— 强验证 300s 内且同一收款人 → 抵消 1 个升级项，**不抵扣金额类硬红**；
    4. 复核 deny       —— 计划净流出 / 风险分 / 会话锁定 / 转账入参不全 / 单笔硬上限。

几处"故意这么写"的点（评审必问）：

* ``base_level`` 完全不知道风控信号，所以"绿色查询走 IM 渠道"仍是绿色（用例 #30）；
* 金额类硬红先于升级/折价计算，且 base=red 时 ``discount`` 恒为 0，
  因此"强验证后 5 分钟内转 5000 元"恒红（P0 回归，用例 #25）；
* 新收款人**不是**升级信号（它已经是矩阵里的基础条件），否则"新收款人转 500 元"
  会被重复升级成红，与用例 #6 冲突（文档 §6.2.2 边界 1）；
* 缺字段一律按"未提供"处理：不抛异常，但从严（未登记动作 / 转账入参不全 → deny）。

金额单位统一为**分**（``int``）；``reason`` 里的金额只在渲染时格式化为 "1,000.00 元"。
"""

from __future__ import annotations

from typing import Any, Final

from . import config, specs, specs

# ============================================================ 规则编号
# 审计事件里会带 rule_id；改编号等同于改策略，必须同步 rules/policy.rego 与测试。
# deny：硬拒绝
R_D_PASSWORD: Final = "R-D-01"        # 密码类动作：不提供工具，恒 deny
R_D_PLAN_OUTFLOW: Final = "R-D-02"    # 计划净流出超过 5 万硬上限
R_D_RISK_SCORE: Final = "R-D-03"      # 风险评分 > 90
R_D_LOCKED: Final = "R-D-04"          # 会话锁定
R_D_INCOMPLETE: Final = "R-D-05"      # 转账缺 amount / daily_used
R_D_UNKNOWN: Final = "R-D-06"         # 未登记动作（默认拒绝）
R_D_NODE_CAP: Final = "R-D-07"        # 单笔达到 5 万硬上限
R_D_SYSTEM: Final = "R-D-08"          # 系统内部工具不接受用户侧调用
# 金额类硬红（base_level=red，任何降级都不得抵扣）
R_T_DAILY: Final = "R-T-01"           # 转账日累计 > 1000 元
R_T_NEW_PAYEE: Final = "R-T-02"       # 新收款人单笔 > 500 元
R_S_SINGLE: Final = "R-S-01"          # 取消单条订阅 > 200 元
R_S_BATCH: Final = "R-S-02"           # 批量取消订阅
R_P_OUTFLOW: Final = "R-P-01"         # 计划净流出 > 1000 元
R_P_TEMPLATE: Final = "R-P-02"        # 计划模板单笔 > 1000 元
R_M_RED: Final = "R-M-01"             # 矩阵恒红动作
# 基础黄 / 基础绿
R_T_YELLOW: Final = "R-T-03"          # 转账基础黄（未超阈值）
R_S_YELLOW: Final = "R-S-03"          # 取消订阅基础黄
R_Y_ADJUST_DOWN: Final = "R-Y-02"     # 卡片额度下调
R_Y_GENERIC: Final = "R-Y-01"         # 其他黄
R_G_READONLY: Final = "R-G-01"        # 只读查询
# 升降级
R_H_ESCALATED: Final = "R-H-01"       # 升级后为红
R_H_DISCOUNTED: Final = "R-H-02"      # 强验证抵消升级项后仍为黄

# 契约 §6 的 deny 阈值：risk_score > 90。
# config.py 没有对应常量（不在本工作包可改范围），此处具名声明，避免裸魔数。
RISK_SCORE_DENY: Final = 90

# ============================================================ 动作集合
_TRANSFER_ACTIONS: Final[frozenset[str]] = frozenset({"create_transfer", "submit_transfer"})
_PASSWORD_ACTIONS: Final[frozenset[str]] = frozenset(
    {"modify_password", "change_password", "reset_password"}
)
# 额度调整类动作的 amount 是"额度值"而不是"付款金额"，不受单笔 5 万资金上限约束
_LIMIT_EXEMPT_ACTIONS: Final[frozenset[str]] = frozenset(
    {"adjust_card_limit", "adjust_card_limit_up", "adjust_card_limit_down"}
)
# 文档/Rego 里用简化动作名，工具层用 specs 注册名；两者都收，行为一致
_ACTION_ALIASES: Final[dict[str, str]] = {
    "transfer": "create_transfer",
    "subscription_cancel": "cancel_subscription",
    "cancel_subscriptions": "cancel_subscription",
}
_KNOWN_ACTIONS: Final[frozenset[str]] = frozenset(specs.CALLABLE_TOOLS) | frozenset(
    {"adjust_card_limit_up", "adjust_card_limit_down"}
)
_SYSTEM_ACTIONS: Final[frozenset[str]] = frozenset(specs.SYSTEM_TOOLS)

_REQUIRED_FACTORS: Final[dict[str, tuple[str, ...]]] = {
    "red": ("sms", "face_or_ushield"),
    "yellow": ("explicit_confirm",),
    "green": (),
    "deny": (),
}

_FLAG_LABELS: Final[dict[str, str]] = {
    "im_channel": "IM 渠道 +1 级",
    "night": "夜间 23:00–06:00 +1 级",
    "new_device": "设备指纹异常 +1 级",
    "high_risk": "高风险用户 +1 级",
}

_LABELS: Final[dict[str, str]] = {
    "get_accounts": "账户余额查询",
    "get_transactions": "流水查询",
    "analyze_spending": "支出分析",
    "detect_anomaly": "异常交易识别",
    "get_bill_report": "账单报告",
    "get_contacts": "联系人查询",
    "get_subscriptions": "订阅查询",
    "get_wealth_products": "理财查询",
    "get_profile": "用户档案查询",
    "quote_transfer": "转账试算",
    "create_transfer": "小额转账",
    "cancel_subscription": "取消订阅",
    "apply_virtual_card": "申请虚拟卡",
    "create_collection_request": "AA 收款请求",
    "set_reminder": "设置提醒",
    "create_standing_plan": "创建长期计划",
    "freeze_amount": "资金冻结",
    "order_gift": "商户下单",
    "release_freeze": "解除冻结",
    "submit_transfer": "大额转账",
    "report_card_lost": "卡片挂失",
    "unlock_card": "解锁卡片",
    "purchase_wealth": "理财申购",
    "redeem_wealth": "理财赎回",
    "adjust_card_limit": "卡片额度调整",
    "adjust_card_limit_up": "卡片额度上调",
    "adjust_card_limit_down": "卡片额度下调",
    "notify": "系统通知",
}


# ============================================================ 入口
def decide(ctx: dict) -> dict:
    """对单个动作做权限裁决。

    入参 ``ctx``（缺省即未提供；多余字段忽略）：

        action / amount / daily_used / payee_is_new / channel / hour / new_device /
        risk_level / plan_outflow / batch_count / stepup_age_seconds / stepup_payee /
        payee_id / risk_score / session_state

    返回：``{"level", "rule_id", "reason", "required_factors",
    "base_level", "escalations", "discount"}``。

    * ``level`` ∈ green / yellow / red / deny；
    * ``base_level`` 是只看矩阵与金额硬红的等级，便于审计区分"矩阵红"与"升级红"；
    * ``escalations`` / ``discount`` 只在 ``base_level == "yellow"`` 时可能非 0。
    """
    data: dict[str, Any] = ctx if isinstance(ctx, dict) else {}
    raw_action = data.get("action")
    action = _normalize_action(raw_action)

    # ---------- 0) 硬拒绝：优先于一切，连 base_level 都不值得算 ----------
    if action in _PASSWORD_ACTIONS:
        return _result(
            "deny", R_D_PASSWORD,
            "密码修改不提供工具，Agent 只做安全跳转引导（文档 §6.2.1 恒 deny）",
            "deny",
        )
    if action is None:
        return _result("deny", R_D_UNKNOWN, "未提供 action，按未登记动作拒绝", "deny")

    canonical = _ACTION_ALIASES.get(action, action)
    if canonical in _SYSTEM_ACTIONS:
        return _result(
            "deny", R_D_SYSTEM,
            f"{_LABELS.get(canonical, canonical)}属于系统内部动作，不接受用户侧调用",
            "deny",
        )
    if canonical not in _KNOWN_ACTIONS:
        return _result(
            "deny", R_D_UNKNOWN,
            f"未登记动作 {raw_action!r}，默认拒绝（不做任何降级）",
            "deny",
        )

    amount = _as_cents(data.get("amount"))
    daily_used = _as_cents(data.get("daily_used"))
    batch_count = _as_cents(data.get("batch_count"))
    plan_outflow = _as_cents(data.get("plan_outflow"))
    risk_score = _as_cents(data.get("risk_score"))

    # ---------- 1) base_level：只看矩阵 + 金额类硬红 ----------
    base_level, base_rule_id, base_reason = _base_verdict(
        canonical=canonical,
        data=data,
        amount=amount,
        daily_used=daily_used,
        batch_count=batch_count,
        plan_outflow=plan_outflow,
    )

    # ---------- 2) 契约级 deny：优先级高于升降级 ----------
    deny_hit = _deny_verdict(
        canonical=canonical,
        data=data,
        amount=amount,
        daily_used=daily_used,
        plan_outflow=plan_outflow,
        risk_score=risk_score,
    )
    if deny_hit is not None:
        rule_id, reason = deny_hit
        return _result("deny", rule_id, reason, base_level)

    # ---------- 3) 升级 / 降级：只作用于 base=yellow ----------
    level = base_level
    rule_id = base_rule_id
    reason = base_reason
    escalations = 0
    discount = 0
    if base_level == "yellow":
        flags = _escalation_flags(data, amount)
        escalations = len(flags)
        discount = _stepup_credit(data)
        remaining = max(0, escalations - discount)
        if remaining >= 1:
            level = "red"
            rule_id = R_H_ESCALATED
            reason = _escalated_reason(flags, amount, discount, remaining)
        elif escalations > 0 and discount > 0:
            rule_id = R_H_DISCOUNTED
            reason = _discounted_reason(flags, amount, data.get("stepup_age_seconds"))

    return _result(level, rule_id, reason, base_level, escalations, discount)


# ============================================================ 分级
def _base_verdict(
    *,
    canonical: str,
    data: dict,
    amount: int | None,
    daily_used: int | None,
    batch_count: int | None,
    plan_outflow: int | None,
) -> tuple[str, str, str]:
    """只看矩阵与金额类硬红，返回 ``(base_level, rule_id, reason)``。"""
    label = _LABELS.get(canonical, canonical)

    # 只读查询恒绿：不因渠道 / 时段 / 设备 / 计划聚合被升级（用例 #15、#30）
    if canonical in specs.GREEN_TOOLS:
        return "green", R_G_READONLY, f"{label}属于只读查询，免确认"

    # ---- 金额类硬红：任何降级、折价都不得抵扣 ----
    if canonical in _TRANSFER_ACTIONS:
        if amount is not None and daily_used is not None:
            total = daily_used + amount
            if total > config.LIMIT_DAILY:
                return (
                    "red", R_T_DAILY,
                    f"当日累计 {_money(total)}/{_money(config.LIMIT_DAILY)} 元超过免密额度"
                    f"（本次 {_money(amount)} 元，已用 {_money(daily_used)} 元），需强验证",
                )
        if _as_bool(data.get("payee_is_new")) and amount is not None and amount > config.LIMIT_NEW_PAYEE:
            return (
                "red", R_T_NEW_PAYEE,
                f"新收款人单笔 {_money(amount)}/{_money(config.LIMIT_NEW_PAYEE)} 元"
                "超过首笔免验额度，需强验证",
            )
    if canonical == "cancel_subscription":
        if amount is not None and amount > config.LIMIT_SUB_CANCEL:
            return (
                "red", R_S_SINGLE,
                f"单条订阅 {_money(amount)}/{_money(config.LIMIT_SUB_CANCEL)} 元，取消需强验证",
            )
        if batch_count is not None and batch_count > 1:
            return (
                "red", R_S_BATCH,
                f"批量取消 {batch_count} 条订阅，禁止批量直执行，需强验证并逐条确认",
            )
    if canonical == "create_standing_plan" and amount is not None and amount > config.LIMIT_DAILY:
        return (
            "red", R_P_TEMPLATE,
            f"计划模板单笔 {_money(amount)}/{_money(config.LIMIT_DAILY)} 元超过免密额度，需强验证",
        )
    if plan_outflow is not None and plan_outflow > config.LIMIT_PLAN_OUTFLOW:
        return (
            "red", R_P_OUTFLOW,
            f"计划净流出 {_money(plan_outflow)}/{_money(config.LIMIT_PLAN_OUTFLOW)} 元"
            "超过计划免密额度（防拆分），需强验证",
        )

    # ---- 矩阵（specs 唯一真相）----
    if canonical in specs.RED_TOOLS or canonical == "adjust_card_limit_up":
        return "red", R_M_RED, f"{label}属于恒红动作，需短信 + 人脸/U盾双因子验证"
    if canonical in _TRANSFER_ACTIONS:
        total = (daily_used or 0) + (amount or 0)
        return (
            "yellow", R_T_YELLOW,
            f"单笔 {_money(amount or 0)} 元，当日累计 {_money(total)}/"
            f"{_money(config.LIMIT_DAILY)} 元未超免密额度，需显式确认",
        )
    if canonical == "cancel_subscription":
        return (
            "yellow", R_S_YELLOW,
            f"取消单条订阅 {_money(amount or 0)}/{_money(config.LIMIT_SUB_CANCEL)} 元，需逐条确认",
        )
    if canonical == "adjust_card_limit_down":
        return "yellow", R_Y_ADJUST_DOWN, f"{label} {_money(amount or 0)} 元，需显式确认"
    if canonical in specs.YELLOW_TOOLS:
        if amount:
            return "yellow", R_Y_GENERIC, f"{label} {_money(amount)} 元，需显式确认"
        return "yellow", R_Y_GENERIC, f"{label}，需显式确认"

    # 兜底：理论上不可达（decide 已过滤未登记动作），仍从严
    return "deny", R_D_UNKNOWN, f"未登记动作 {canonical!r}，默认拒绝"


def _deny_verdict(
    *,
    canonical: str,
    data: dict,
    amount: int | None,
    daily_used: int | None,
    plan_outflow: int | None,
    risk_score: int | None,
) -> tuple[str, str] | None:
    """契约 §6 的硬拒绝项；返回 ``(rule_id, reason)`` 或 ``None``。"""
    if plan_outflow is not None and plan_outflow > config.HARD_CAP_PER_PLAN:
        return (
            R_D_PLAN_OUTFLOW,
            f"计划净流出 {_money(plan_outflow)}/{_money(config.HARD_CAP_PER_PLAN)} 元"
            "超过计划硬上限，禁止办理",
        )
    if risk_score is not None and risk_score > RISK_SCORE_DENY:
        return R_D_RISK_SCORE, f"风险评分 {risk_score}/{RISK_SCORE_DENY} 超过阈值，禁止自动办理"
    state = str(data.get("session_state") or "").strip().lower()
    if state == "locked" and specs.group_of(canonical) != "green":
        # 熔断只封锁资金类动作；查询类仍可用，避免用户被"锁死"后无法自查
        return R_D_LOCKED, "会话已锁定（连续失败），暂不能办理资金类操作，查询不受影响"
    if canonical in _TRANSFER_ACTIONS and (
        amount is None or amount <= 0 or daily_used is None
    ):
        return (
            R_D_INCOMPLETE,
            f"转账入参不全（amount={amount!r}, daily_used={daily_used!r}），按从严原则拒绝",
        )
    if (
        amount is not None
        and amount >= config.HARD_CAP_PER_NODE
        and canonical not in _LIMIT_EXEMPT_ACTIONS
    ):
        return (
            R_D_NODE_CAP,
            f"单笔 {_money(amount)}/{_money(config.HARD_CAP_PER_NODE)} 元达到单笔硬上限，"
            "需人工柜面办理",
        )
    return None


# ============================================================ 升级 / 降级
def _escalation_flags(data: dict, amount: int | None) -> list[str]:
    """按 6.2.2 计算升级信号（阈值全部来自 config，且只对 base=yellow 生效）。"""
    flags: list[str] = []
    channel = str(data.get("channel") or "").strip().lower()
    if channel == "im" and amount is not None and amount > config.LIMIT_IM_CHANNEL:
        flags.append("im_channel")
    hour = _as_cents(data.get("hour"))
    # 夜间升级只作用于资金转出类动作：锁预算/建计划/发卡不动账，不应因此升红
    night_actions = {"create_transfer", "submit_transfer", "order_gift", "purchase_wealth"}
    if (
        hour is not None
        and str(data.get("action") or "") in night_actions
        and (hour >= config.NIGHT_START_HOUR or hour < config.NIGHT_END_HOUR)
        and amount is not None
        and amount > config.LIMIT_NIGHT
    ):
        flags.append("night")
    if _as_bool(data.get("new_device")):
        flags.append("new_device")
    if str(data.get("risk_level") or "").strip().lower() == "high":
        flags.append("high_risk")
    return flags


def _stepup_credit(data: dict) -> int:
    """强验证折价：300s 内且**同一收款人**才给 1 个折价额度（窗口一次性消费）。"""
    age = _as_cents(data.get("stepup_age_seconds"))
    if age is None or age < 0 or age >= config.STEPUP_TTL_SECONDS:
        return 0
    stepup_payee = data.get("stepup_payee")
    payee_id = data.get("payee_id")
    if stepup_payee is None or payee_id is None:
        return 0
    verified = str(stepup_payee).strip()
    current = str(payee_id).strip()
    if not verified or not current:
        return 0
    return 1 if verified == current else 0


def _escalated_reason(flags: list[str], amount: int | None, discount: int, remaining: int) -> str:
    flags_text = "、".join(_FLAG_LABELS.get(flag, flag) for flag in flags)
    head = f"基础黄 + {len(flags)} 项升级（{flags_text}）"
    if discount and remaining < len(flags):
        head += f"，强验证抵消 {len(flags) - remaining} 项后仍达 {remaining} 项"
    return f"{head} → 红，金额 {_money(amount or 0)} 元，需强验证"


def _discounted_reason(flags: list[str], amount: int | None, age: Any) -> str:
    flags_text = "、".join(_FLAG_LABELS.get(flag, flag) for flag in flags)
    age_text = f"{age}s" if _as_cents(age) is not None else "有效窗口内"
    return (
        f"基础黄 + {len(flags)} 项升级（{flags_text}），强验证 {age_text} 且同一收款人，"
        f"抵消 1 项后保持黄，金额 {_money(amount or 0)} 元，需显式确认"
    )


# ============================================================ 结果与格式化
def _result(
    level: str,
    rule_id: str,
    reason: str,
    base_level: str,
    escalations: int = 0,
    discount: int = 0,
) -> dict:
    """按契约 §6 组装返回体（字段与顺序固定）。"""
    return {
        "level": level,
        "rule_id": rule_id,
        "reason": reason,
        "required_factors": list(_REQUIRED_FACTORS[level]),
        "base_level": base_level,
        "escalations": escalations,
        "discount": discount,
    }


def _money(cents: int) -> str:
    """分 → "1,000.00"（Policy 只负责 reason 文案，回复渲染以 trust.money 为准）。"""
    sign = "-" if cents < 0 else ""
    yuan, fen = divmod(abs(int(cents)), 100)
    return f"{sign}{yuan:,}.{fen:02d}"


def _normalize_action(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    return key or None


def _as_cents(value: Any) -> int | None:
    """把入参归一为整数分；无法解释为整数时返回 None（视为"未提供"）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是", "on"}
    return False
