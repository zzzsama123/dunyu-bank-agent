"""工具清单（唯一真相）。

任何模块需要"有哪些工具、属于哪组、要什么参数、资金方向"时，只读本文件。
tools.py 必须实现这里的每一个名字，planner/validator 只能引用这里的名字。
"""

from __future__ import annotations

# group: green(只读) / yellow(用户确认) / red(强验证) / system(系统内部)
# flow : out(资金流出) / in(资金流入) / none
TOOL_SPECS: dict[str, dict] = {
    # ---------------- 绿 · 只读 ----------------
    "get_accounts": {
        "group": "green", "flow": "none",
        "desc": "账户与卡列表、余额、可用额",
        "params": {"user_id": {"type": "int", "required": False}},
    },
    "get_transactions": {
        "group": "green", "flow": "none",
        "desc": "流水查询（时间范围、分类、条数）",
        "params": {
            "range": {"type": "str", "required": False},
            "category": {"type": "str", "required": False},
            "counterparty": {"type": "str", "required": False},
            "direction": {"type": "str", "required": False},
            "limit": {"type": "int", "required": False},
        },
    },
    "analyze_spending": {
        "group": "green", "flow": "none",
        "desc": "支出分类统计、占比、环比",
        "params": {"range": {"type": "str", "required": False}},
    },
    "detect_anomaly": {
        "group": "green", "flow": "none",
        "desc": "异常交易识别（凌晨/异地/分拆）",
        "params": {"range": {"type": "str", "required": False}},
    },
    "get_bill_report": {
        "group": "green", "flow": "none",
        "desc": "月度/年度账单报告（含图表数据）",
        "params": {
            "range": {"type": "str", "required": False},
            "format": {"type": "str", "required": False},
        },
    },
    "get_contacts": {
        "group": "green", "flow": "none",
        "desc": "联系人查询（别名/姓名模糊匹配）",
        "params": {
            "alias": {"type": "str", "required": False},
            "name": {"type": "str", "required": False},
        },
    },
    "get_subscriptions": {
        "group": "green", "flow": "none",
        "desc": "订阅/代扣列表与下次扣费",
        "params": {},
    },
    "get_wealth_products": {
        "group": "green", "flow": "none",
        "desc": "理财产品列表（风险等级取字段值）",
        "params": {
            "risk_level": {"type": "str", "required": False},
            "term_days": {"type": "int", "required": False},
        },
    },
    "get_profile": {
        "group": "green", "flow": "none",
        "desc": "用户档案（含生日、风险等级）",
        "params": {},
    },
    "quote_transfer": {
        "group": "green", "flow": "none",
        "desc": "转账试算（手续费、到账时间，不动账）",
        "params": {
            "contact_id": {"type": "int", "required": True},
            "amount": {"type": "int", "required": True},
        },
    },
    # ---------------- 黄 · 低风险写入 ----------------
    "create_transfer": {
        "group": "yellow", "flow": "out",
        "desc": "小额转账（≤1000 且累计合规）",
        "params": {
            "contact_id": {"type": "int", "required": True},
            "amount": {"type": "int", "required": True},
            "from_account_id": {"type": "int", "required": False},
            "memo": {"type": "str", "required": False},
        },
        "compensation": "reverse",
    },
    "cancel_subscription": {
        "group": "yellow", "flow": "none",
        "desc": "取消订阅（不可逆，需逐条确认）",
        "params": {"sub_id": {"type": "int", "required": True}},
        "compensation": None,
    },
    "apply_virtual_card": {
        "group": "yellow", "flow": "none",
        "desc": "申请虚拟卡",
        "params": {"card_type": {"type": "str", "required": False}},
    },
    "create_collection_request": {
        "group": "yellow", "flow": "in",
        "desc": "AA 收款请求（资金流入）",
        "params": {
            "payer_contact_ids": {"type": "list", "required": True},
            "amount_each": {"type": "int", "required": True},
        },
    },
    "set_reminder": {
        "group": "yellow", "flow": "none",
        "desc": "设置提醒",
        "params": {
            "at": {"type": "str", "required": True},
            "content": {"type": "str", "required": True},
        },
    },
    "create_standing_plan": {
        "group": "yellow", "flow": "none",
        "desc": "创建长期计划（生日等长周期事务）",
        "params": {
            "goal": {"type": "str", "required": True},
            "trigger": {"type": "dict", "required": True},
            "actions": {"type": "list", "required": True},
            "budget": {"type": "int", "required": False},
        },
    },
    "freeze_amount": {
        "group": "yellow", "flow": "none",
        "desc": "锁定活期资金（hold）",
        "params": {
            "account_id": {"type": "int", "required": False},
            "amount": {"type": "int", "required": True},
            "until": {"type": "str", "required": False},
        },
        "compensation": "release_freeze",
    },
    "order_gift": {
        "group": "yellow", "flow": "out",
        "desc": "外部商户下单（鲜花/蛋糕；已下单不可冲正）",
        "params": {
            "item": {"type": "str", "required": True},
            "amount": {"type": "int", "required": True},
            "deliver_at": {"type": "str", "required": False},
        },
        "compensation": None,
    },
    "release_freeze": {
        "group": "yellow", "flow": "none",
        "desc": "解除冻结（仅补偿上下文可调）",
        "params": {"freeze_id": {"type": "int", "required": True}},
        "compensation_only": True,
    },
    # ---------------- 红 · 高风险写入 ----------------
    "submit_transfer": {
        "group": "red", "flow": "out",
        "desc": "大额转账（>1000 或累计超限）",
        "params": {
            "contact_id": {"type": "int", "required": True},
            "amount": {"type": "int", "required": True},
            "from_account_id": {"type": "int", "required": False},
            "memo": {"type": "str", "required": False},
        },
        "compensation": "reverse",
    },
    "report_card_lost": {
        "group": "red", "flow": "none",
        "desc": "卡片挂失（恒红，不可逆）",
        "params": {"card_id": {"type": "int", "required": True}},
    },
    "unlock_card": {
        "group": "red", "flow": "none",
        "desc": "解挂",
        "params": {"card_id": {"type": "int", "required": True}},
    },
    "request_password_reset": {
        "group": "red", "flow": "none",
        "desc": "申请密码重置链接（只发链接，不修改密码）",
        "params": {"channel": {"type": "str", "required": False}},
    },
    "schedule_transfer": {
        "group": "yellow", "flow": "none",
        "desc": "预约未来转账",
        "params": {"amount": {"type": "int", "required": True},
                   "run_at": {"type": "str", "required": True},
                   "contact_id": {"type": "int", "required": False}},
    },
    "reverse_transfer": {
        "group": "red", "flow": "out",
        "desc": "撤销已执行转账",
        "params": {"txn_id": {"type": "int", "required": True}},
    },
    "repay_credit_card": {
        "group": "red", "flow": "out",
        "desc": "信用卡还款",
        "params": {"amount": {"type": "int", "required": True}},
    },
    "repay_loan": {
        "group": "red", "flow": "out",
        "desc": "归还贷款（默认月供）",
        "params": {"amount": {"type": "int", "required": False},
                   "loan_id": {"type": "int", "required": False}},
    },
    "risk_assessment": {
        "group": "yellow", "flow": "none",
        "desc": "风险承受能力测评",
        "params": {"level": {"type": "str", "required": True},
                   "score": {"type": "int", "required": False}},
    },
    "purchase_wealth": {
        "group": "red", "flow": "out",
        "desc": "理财申购",
        "params": {
            "product_id": {"type": "int", "required": True},
            "amount": {"type": "int", "required": True},
        },
    },
    "redeem_wealth": {
        "group": "red", "flow": "in",
        "desc": "理财赎回",
        "params": {
            "holding_id": {"type": "int", "required": True},
            "amount": {"type": "int", "required": False},
        },
    },
    "adjust_card_limit": {
        "group": "red", "flow": "none",
        "desc": "卡片额度调整（上调恒红，下调黄）",
        "params": {
            "card_id": {"type": "int", "required": True},
            "new_limit": {"type": "int", "required": True},
        },
    },
    # ---------------- 系统 · 非用户授权 ----------------
    "notify": {
        "group": "system", "flow": "none",
        "desc": "站内/IM 通知（scheduler 内部调用，模型不可直接生成）",
        "params": {
            "user_id": {"type": "int", "required": False},
            "content": {"type": "str", "required": True},
        },
    },
}

TOOL_NAMES: list[str] = list(TOOL_SPECS)
GREEN_TOOLS = [n for n, s in TOOL_SPECS.items() if s["group"] == "green"]
YELLOW_TOOLS = [n for n, s in TOOL_SPECS.items() if s["group"] == "yellow"]
RED_TOOLS = [n for n, s in TOOL_SPECS.items() if s["group"] == "red"]
SYSTEM_TOOLS = [n for n, s in TOOL_SPECS.items() if s["group"] == "system"]
# 模型可生成的工具（系统工具不在内）
CALLABLE_TOOLS = [n for n in TOOL_NAMES if TOOL_SPECS[n]["group"] != "system"]

# 资金参数（provenance 强校验对象）
FUND_PARAMS = {"amount", "amount_each", "budget", "new_limit", "from_account_id"}
# 单节点 / 单计划硬上限（分）
HARD_CAP_PER_NODE = 5_000_000
HARD_CAP_PER_PLAN = 5_000_000
MAX_PLAN_NODES = 8


def flow_of(name: str) -> str:
    return TOOL_SPECS.get(name, {}).get("flow", "none")


def group_of(name: str) -> str:
    return TOOL_SPECS.get(name, {}).get("group", "unknown")
