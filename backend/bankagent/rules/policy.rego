package bank.policy

# =============================================================================
# 银行 AI 智能体 · 策略引擎（Rego 镜像）
# -----------------------------------------------------------------------------
# 本文件是 backend/bankagent/policy.py 的**一一对应镜像**，用于答辩展示：
# "权限矩阵是可 diff、可单测、可评审的策略资产，而不是散落在 if-else 里的魔数"。
#
# 一致性声明：
#   * level / base_level / rule_id / escalations / discount 与 policy.py 完全一致；
#   * reason 文案由 Python 侧渲染（含千分位金额），这里用 rule_reasons 给出同一
#     规则编号的中文说明，避免两套格式化逻辑漂移；
#   * 阈值与 backend/bankagent/config.py 一一对应（单位：分）。
#
# 本地不要求可执行 OPA（赛题环境禁止联网/安装），语法按 OPA v0.60+ / Rego v0 书写。
# 规则编号与 policy.py 顶部常量保持一致：
#   R-D-01..08 deny / R-T-01,02 转账硬红 / R-S-01,02 订阅硬红 / R-P-01,02 计划硬红
#   R-M-01 矩阵红 / R-T-03,R-S-03,R-Y-01,02,R-G-01 基础黄绿 / R-H-01,02 升降级
# =============================================================================

import future.keywords.if
import future.keywords.in
import future.keywords.contains

# ------------------------------------------------------------------ 阈值
# == config.py（单位：分）
LIMIT_DAILY := 100000            # 日累计 1,000 元
LIMIT_NEW_PAYEE := 50000         # 新收款人单笔 500 元
LIMIT_SUB_CANCEL := 20000        # 单条订阅 200 元
LIMIT_IM_CHANNEL := 30000        # IM 渠道 300 元
LIMIT_NIGHT := 50000             # 夜间 500 元
LIMIT_PLAN_OUTFLOW := 100000     # 计划净流出 1,000 元
HARD_CAP_PER_NODE := 5000000     # 单笔硬上限 5 万
HARD_CAP_PER_PLAN := 5000000     # 计划硬上限 5 万
STEPUP_TTL_SECONDS := 300        # 强验证窗口
NIGHT_START_HOUR := 23
NIGHT_END_HOUR := 6
RISK_SCORE_DENY := 90            # 契约 §6：risk_score > 90 恒 deny

# ------------------------------------------------------------------ 动作集合（= specs.py）
green_actions := {
    "get_accounts", "get_transactions", "analyze_spending", "detect_anomaly",
    "get_bill_report", "get_contacts", "get_subscriptions", "get_wealth_products",
    "get_profile", "quote_transfer",
}

yellow_actions := {
    "create_transfer", "cancel_subscription", "apply_virtual_card",
    "create_collection_request", "set_reminder", "create_standing_plan",
    "freeze_amount", "order_gift", "release_freeze",
}

red_actions := {
    "submit_transfer", "report_card_lost", "unlock_card", "purchase_wealth",
    "redeem_wealth", "adjust_card_limit", "adjust_card_limit_up",
}

transfer_actions := {"create_transfer", "submit_transfer"}

password_actions := {"modify_password", "change_password", "reset_password"}

system_actions := {"notify"}

# 额度调整类的 amount 是"额度值"，不受单笔资金上限约束
limit_exempt_actions := {"adjust_card_limit", "adjust_card_limit_up", "adjust_card_limit_down"}

action_aliases := {
    "transfer": "create_transfer",
    "subscription_cancel": "cancel_subscription",
    "cancel_subscriptions": "cancel_subscription",
}

known_actions := green_actions | yellow_actions | red_actions | transfer_actions | password_actions | system_actions | {"adjust_card_limit_up", "adjust_card_limit_down"}

# 注：known_actions 额外并入 password/system（policy.py 在入口处单独拦截这两个集合），
# 只是为了在 deny 链里用同一个集合判断"已登记"，最终裁决与 policy.py 逐条一致。

# ------------------------------------------------------------------ 输入归一
raw_action := lower(trim_space(object.get(input, "action", "")))

action := object.get(action_aliases, raw_action, raw_action)

known_action if { action in known_actions }

has_amount if { object.get(input, "amount", null) != null }

amount := object.get(input, "amount", 0)

has_daily_used if { object.get(input, "daily_used", null) != null }

daily_used := object.get(input, "daily_used", 0)

plan_outflow := object.get(input, "plan_outflow", 0)

batch_count := object.get(input, "batch_count", 1)

risk_score := object.get(input, "risk_score", 0)

session_state := lower(trim_space(object.get(input, "session_state", "normal")))

risk_level := lower(trim_space(object.get(input, "risk_level", "normal")))

channel := lower(trim_space(object.get(input, "channel", "app")))

hour := object.get(input, "hour", 12)

new_device if { object.get(input, "new_device", false) == true }

payee_is_new if { object.get(input, "payee_is_new", false) == true }

amount_ok if { has_amount; amount > 0 }

# ------------------------------------------------------------------ 0) 硬拒绝（优先级最高）
deny := {"rule_id": "R-D-01", "reason": "密码修改不提供工具，Agent 只做跳转引导"} if {
    raw_action in password_actions
} else := {"rule_id": "R-D-06", "reason": "未登记动作，默认拒绝（不做任何降级）"} if {
    not known_action
} else := {"rule_id": "R-D-08", "reason": "系统内部动作不接受用户侧调用"} if {
    action in system_actions
} else := {"rule_id": "R-D-02", "reason": "计划净流出超过 5 万计划硬上限"} if {
    plan_outflow > HARD_CAP_PER_PLAN
} else := {"rule_id": "R-D-03", "reason": "风险评分超过 90，禁止自动办理"} if {
    risk_score > RISK_SCORE_DENY
} else := {"rule_id": "R-D-04", "reason": "会话处于 LOCKED 状态，禁止写操作"} if {
    session_state == "locked"
} else := {"rule_id": "R-D-05", "reason": "转账入参不全，按从严原则拒绝"} if {
    action in transfer_actions
    not amount_ok
} else := {"rule_id": "R-D-05", "reason": "转账缺少当日已用额度，按从严原则拒绝"} if {
    action in transfer_actions
    not has_daily_used
} else := {"rule_id": "R-D-07", "reason": "单笔达到 5 万单笔硬上限"} if {
    amount_ok
    amount >= HARD_CAP_PER_NODE
    not limit_exempt_actions[action]
}

# ------------------------------------------------------------------ 1) base_level：只看矩阵 + 金额类硬红
# 1.1 金额类硬红（顺序与 policy.py 一致，取第一条命中）
hard_red if {
    action in transfer_actions
    has_amount
    has_daily_used
    daily_used + amount > LIMIT_DAILY
}

hard_red if {
    action in transfer_actions
    payee_is_new
    amount > LIMIT_NEW_PAYEE
}

hard_red if { action == "cancel_subscription"; amount > LIMIT_SUB_CANCEL }

hard_red if { action == "cancel_subscription"; batch_count > 1 }

hard_red if { action == "create_standing_plan"; amount > LIMIT_DAILY }

hard_red if { plan_outflow > LIMIT_PLAN_OUTFLOW }

hard_rule_id := "R-T-01" if {
    action in transfer_actions
    has_amount
    has_daily_used
    daily_used + amount > LIMIT_DAILY
}
else := "R-T-02" if { action in transfer_actions; payee_is_new; amount > LIMIT_NEW_PAYEE }
else := "R-S-01" if { action == "cancel_subscription"; amount > LIMIT_SUB_CANCEL }
else := "R-S-02" if { action == "cancel_subscription"; batch_count > 1 }
else := "R-P-02" if { action == "create_standing_plan"; amount > LIMIT_DAILY }
else := "R-P-01" if { plan_outflow > LIMIT_PLAN_OUTFLOW }

# 1.2 矩阵：绿恒绿（不受渠道/时段/金额影响），再矩阵红，再矩阵黄
base_level := "green" if { action in green_actions }
else := "red" if { hard_red }
else := "red" if { action in red_actions }
else := "yellow" if { action in transfer_actions }
else := "yellow" if { action == "cancel_subscription" }
else := "yellow" if { action == "adjust_card_limit_down" }
else := "yellow" if { action in yellow_actions }
else := "deny"

base_rule_id := "R-G-01" if { action in green_actions }
else := hard_rule_id if { hard_red }
else := "R-M-01" if { action in red_actions }
else := "R-T-03" if { action in transfer_actions }
else := "R-S-03" if { action == "cancel_subscription" }
else := "R-Y-02" if { action == "adjust_card_limit_down" }
else := "R-Y-01" if { action in yellow_actions }
else := "R-D-06"

# ------------------------------------------------------------------ 2) 升级信号（只作用于 base=yellow）
# 注意：payee_is_new **不是**升级信号（它是矩阵基础条件，见 6.2.2 边界 1），
# 否则"新收款人转 500 元"会被重复升级成红，与用例 #6 冲突。
escalation_flags contains "im_channel" if {
    channel == "im"
    amount_ok
    amount > LIMIT_IM_CHANNEL
}

escalation_flags contains "night" if {
    amount_ok
    amount > LIMIT_NIGHT
    hour >= NIGHT_START_HOUR
}

escalation_flags contains "night" if {
    amount_ok
    amount > LIMIT_NIGHT
    hour < NIGHT_END_HOUR
}

escalation_flags contains "new_device" if { new_device }

escalation_flags contains "high_risk" if { risk_level == "high" }

# ------------------------------------------------------------------ 3) 强验证折价
stepup_age := object.get(input, "stepup_age_seconds", null)

stepup_payee := object.get(input, "stepup_payee", null)

payee_id := object.get(input, "payee_id", null)

stepup_credit := 1 if {
    stepup_age != null
    stepup_age >= 0
    stepup_age < STEPUP_TTL_SECONDS
    stepup_payee != null
    payee_id != null
    stepup_payee == payee_id
}

default stepup_credit := 0

# 只在 base=yellow 时计入 / 生效；base=red 时 discount 恒 0（不得抵扣金额硬红）
counted_escalations := count(escalation_flags) if { base_level == "yellow" }

default counted_escalations := 0

discount := stepup_credit if { base_level == "yellow" }

default discount := 0

# ------------------------------------------------------------------ 4) 最终等级
bumped_level := "red" if { base_level == "yellow"; counted_escalations - discount >= 1 }
else := "yellow" if { base_level == "yellow" }
else := base_level

level := "deny" if { deny }
else := bumped_level

rule_id := deny.rule_id if { deny }
else := "R-H-01" if { base_level == "yellow"; counted_escalations - discount >= 1 }
else := "R-H-02" if {
    base_level == "yellow"
    counted_escalations > 0
    discount > 0
    counted_escalations - discount < 1
}
else := base_rule_id if { not deny }

required_factors := ["sms", "face_or_ushield"] if { level == "red" }
else := ["explicit_confirm"] if { level == "yellow" }
else := []

# ------------------------------------------------------------------ 5) 输出（字段与 CONTRACT §6 一致）
rule_reasons := {
    "R-D-01": "密码修改不提供工具，Agent 只做跳转引导",
    "R-D-02": "计划净流出超过 5 万计划硬上限",
    "R-D-03": "风险评分超过 90，禁止自动办理",
    "R-D-04": "会话处于 LOCKED 状态，禁止写操作",
    "R-D-05": "转账入参不全，按从严原则拒绝",
    "R-D-06": "未登记动作，默认拒绝",
    "R-D-07": "单笔达到 5 万单笔硬上限",
    "R-D-08": "系统内部动作不接受用户侧调用",
    "R-T-01": "当日累计超过 1,000 元免密额度",
    "R-T-02": "新收款人单笔超过 500 元首笔免验额度",
    "R-T-03": "转账未超免密额度，需显式确认",
    "R-S-01": "单条订阅超过 200 元，取消需强验证",
    "R-S-02": "批量取消订阅，禁止批量直执行",
    "R-S-03": "取消单条订阅，需逐条确认",
    "R-P-01": "计划净流出超过 1,000 元免密额度（防拆分）",
    "R-P-02": "计划模板单笔超过 1,000 元免密额度",
    "R-M-01": "矩阵恒红动作，需短信 + 人脸/U盾双因子验证",
    "R-Y-01": "低风险写入，需显式确认",
    "R-Y-02": "卡片额度下调，需显式确认",
    "R-G-01": "只读查询，免确认",
    "R-H-01": "基础黄叠加升级信号后为红，需强验证",
    "R-H-02": "强验证窗口内抵消升级项，保持黄",
}

decide := {
    "level": level,
    "rule_id": rule_id,
    "reason": object.get(rule_reasons, rule_id, rule_id),
    "required_factors": required_factors,
    "base_level": base_level,
    "escalations": counted_escalations,
    "discount": discount,
}
