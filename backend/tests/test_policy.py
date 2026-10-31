"""策略引擎单测：文档 §8.3 的 40 条权限边界用例 + 契约 §6 的 P0 回归。

规则即策略资产，可单测：
    ``bankagent.policy`` 是纯函数，不碰数据库、不读时钟，所以每条边界都能用
    结构化 ``ctx`` 直接断言 ``level`` / ``rule_id`` / ``base_level`` / ``escalations`` /
    ``discount``。本文件就是"边界用例 100%"这条 CI 门禁的执行体。

运行（在 backend 目录下）::

    python -m unittest tests.test_policy -v

用例编号与 ``docs/CONTRACT.md`` §6、``银行AI智能体_完整执行方案.md`` §8.3 对齐；
``CASE_*`` 注释里的 `#n` 即文档表格的行号。
"""

from __future__ import annotations

import ast
import copy
import inspect
import re
import unittest

from bankagent import config, policy, specs

YUAN = 100
RESULT_KEYS = {
    "level",
    "rule_id",
    "reason",
    "required_factors",
    "base_level",
    "escalations",
    "discount",
}
RULE_ID_PATTERN = re.compile(r"^R-[A-Z]-\d{2}$")

_DROP = object()  # build_ctx 里用来"删除某个字段"，模拟未提供


def yuan(value: int) -> int:
    """元 → 分（测试金额一律用整数元，杜绝浮点）。"""
    return int(value) * YUAN


# 一条"什么都没超标"的基线：老收款人、APP、白天、正常设备、正常风险
BASE_CTX: dict = {
    "action": "create_transfer",
    "amount": 0,
    "daily_used": 0,
    "payee_is_new": False,
    "channel": "app",
    "hour": 10,
    "new_device": False,
    "risk_level": "normal",
    "plan_outflow": 0,
    "batch_count": 1,
    "stepup_age_seconds": None,
    "stepup_payee": None,
    "payee_id": "p_mom",
    "risk_score": 10,
    "session_state": "NORMAL",
}


def build_ctx(**overrides) -> dict:
    """基线 ctx + 覆盖；传 ``_DROP`` 表示该字段未提供。"""
    ctx = dict(BASE_CTX)
    for key, value in overrides.items():
        if value is _DROP:
            ctx.pop(key, None)
        else:
            ctx[key] = value
    return ctx


def decide(**overrides) -> dict:
    return policy.decide(build_ctx(**overrides))


class TestBoundaryCases833(unittest.TestCase):
    """文档 §8.3 的 40 条权限边界用例（期望等级以文档为准）。"""

    # ------------------------------------------------------------ #1–#5 金额边界
    def test_case_01_transfer_999_yuan_is_yellow(self) -> None:
        """#1 转账 999 元，当日已用 0 → 黄。"""
        result = decide(amount=yuan(999), daily_used=0)
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["rule_id"], policy.R_T_YELLOW)

    def test_case_02_transfer_1000_yuan_is_yellow_inclusive(self) -> None:
        """#2 转账 1000 元，当日已用 0 → 黄（边界含等于）。"""
        result = decide(amount=yuan(1000), daily_used=0)
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["base_level"], "yellow")

    def test_case_03_transfer_1001_yuan_is_red(self) -> None:
        """#3 转账 1001 元，当日已用 0 → 红（金额阈值类硬红）。"""
        result = decide(amount=yuan(1001), daily_used=0)
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["base_level"], "red")
        self.assertEqual(result["rule_id"], policy.R_T_DAILY)

    def test_case_04_transfer_500_with_used_500_is_yellow(self) -> None:
        """#4 转账 500 元，当日已用 500 → 黄（累计恰好 1000）。"""
        result = decide(amount=yuan(500), daily_used=yuan(500))
        self.assertEqual(result["level"], "yellow")

    def test_case_05_transfer_500_with_used_501_is_red(self) -> None:
        """#5 转账 500 元，当日已用 501 → 红（累计 1001）。"""
        result = decide(amount=yuan(500), daily_used=yuan(501))
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_T_DAILY)

    # ------------------------------------------------------------ #6–#7 新收款人
    def test_case_06_new_payee_500_is_yellow(self) -> None:
        """#6 转账 500 元，收款人为新增 → 黄（新收款人不是升级信号）。"""
        result = decide(amount=yuan(500), payee_is_new=True, daily_used=0)
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["escalations"], 0)
        self.assertEqual(result["discount"], 0)

    def test_case_07_new_payee_501_is_red(self) -> None:
        """#7 转账 501 元，收款人为新增 → 红（新收款人单笔 >500 硬红）。"""
        result = decide(amount=yuan(501), payee_is_new=True, daily_used=0)
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["base_level"], "red")
        self.assertEqual(result["rule_id"], policy.R_T_NEW_PAYEE)

    # ------------------------------------------------------------ #8–#9 升级信号
    def test_case_08_im_channel_400_is_red(self) -> None:
        """#8 转账 400 元，渠道 = IM → 红（渠道 +1 级）。"""
        result = decide(amount=yuan(400), channel="im", daily_used=0)
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["escalations"], 1)
        self.assertEqual(result["rule_id"], policy.R_H_ESCALATED)

    def test_case_09_night_800_is_red(self) -> None:
        """#9 转账 800 元，时间 23:30 → 红（夜间 +1 级）。"""
        result = decide(amount=yuan(800), hour=23, daily_used=0)
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["escalations"], 1)

    # ------------------------------------------------------------ #10–#11 计划聚合
    def test_case_10_plan_3x400_is_red(self) -> None:
        """#10 一个计划内含 3 笔 400 元 → 红（计划聚合 1200）。"""
        result = decide(amount=yuan(400), daily_used=0, plan_outflow=yuan(1200))
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_P_OUTFLOW)

    def test_case_11_window_3x500_is_red(self) -> None:
        """#11 10 分钟内 3 个计划各 500 元 → 红（窗口聚合 1500）。"""
        result = decide(amount=yuan(500), daily_used=0, plan_outflow=yuan(1500))
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_P_OUTFLOW)

    # ------------------------------------------------------------ #12–#14 订阅
    def test_case_12_cancel_single_25_is_yellow(self) -> None:
        """#12 取消订阅 1 条，金额 25 元 → 黄。"""
        result = decide(action="cancel_subscription", amount=yuan(25), batch_count=1)
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["rule_id"], policy.R_S_YELLOW)

    def test_case_13_cancel_single_299_is_red(self) -> None:
        """#13 取消订阅 1 条，金额 299 元 → 红（单条 >200）。"""
        result = decide(action="cancel_subscription", amount=yuan(299), batch_count=1)
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_S_SINGLE)

    def test_case_14_batch_cancel_3_is_red(self) -> None:
        """#14 批量取消 3 条订阅 → 红 + 强制逐条确认。"""
        result = decide(action="cancel_subscription", amount=yuan(25), batch_count=3)
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_S_BATCH)

    # ------------------------------------------------------------ #15–#18 只读 / 恒红 / deny
    def test_case_15_balance_query_is_green(self) -> None:
        """#15 查询余额 → 绿。"""
        result = decide(action="get_accounts", amount=_DROP)
        self.assertEqual(result["level"], "green")
        self.assertEqual(result["base_level"], "green")
        self.assertEqual(result["rule_id"], policy.R_G_READONLY)
        self.assertEqual(result["required_factors"], [])

    def test_case_16_purchase_wealth_1000_is_red(self) -> None:
        """#16 理财申购 1000 元 → 红（矩阵恒红）。"""
        result = decide(action="purchase_wealth", amount=yuan(1000))
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_M_RED)

    def test_case_17_report_card_lost_is_red(self) -> None:
        """#17 卡片挂失 → 红（恒红，不可逆）。"""
        result = decide(action="report_card_lost", amount=_DROP)
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_M_RED)

    def test_case_18_modify_password_is_denied(self) -> None:
        """#18 要求 Agent 代填新密码 → 拒绝（deny，不提供工具）。"""
        result = decide(action="modify_password", amount=_DROP)
        self.assertEqual(result["level"], "deny")
        self.assertEqual(result["rule_id"], policy.R_D_PASSWORD)

    # ------------------------------------------------------------ #19 前置失败
    def test_case_19_insufficient_balance_is_precheck_not_policy(self) -> None:
        """#19 可用余额 500 请求转 800 → 前置失败（E_INSUFFICIENT_FUND），不进策略。

        余额校验在 bankcore / executor 前置完成，策略层根本不读余额；
        这里验证"多传余额字段不会改变策略结论"，避免把账务判断塞进策略。
        """
        ctx = build_ctx(amount=yuan(800), daily_used=0)
        plain = policy.decide(ctx)
        with_balance = policy.decide({**ctx, "available_cents": yuan(500), "balance": yuan(500)})
        self.assertEqual(plain, with_balance)
        self.assertEqual(plain["level"], "yellow")

    # ------------------------------------------------------------ #20 会话锁定
    def test_case_20_locked_session_write_is_denied(self) -> None:
        """#20 会话处于 LOCKED 状态时发起写操作 → 拒绝（deny）。"""
        result = decide(amount=yuan(100), daily_used=0, session_state="LOCKED")
        self.assertEqual(result["level"], "deny")
        self.assertEqual(result["rule_id"], policy.R_D_LOCKED)
        self.assertEqual(result["base_level"], "yellow")

    # ------------------------------------------------------------ #21 跨自然日
    def test_case_21_two_500_transfers_across_midnight_both_yellow(self) -> None:
        """#21 跨日 23:59:59 转 500，00:00:05 再转 500 → 两次均为黄（新自然日）。"""
        before_midnight = decide(amount=yuan(500), daily_used=0, hour=23)
        after_midnight = decide(amount=yuan(500), daily_used=0, hour=0)
        self.assertEqual(before_midnight["level"], "yellow")
        self.assertEqual(after_midnight["level"], "yellow")
        self.assertEqual(before_midnight["escalations"], 0)  # 夜间升级要求 >500
        self.assertEqual(after_midnight["escalations"], 0)

    # ------------------------------------------------------------ #22 频次
    def test_case_22_fifth_transfer_in_10_minutes_is_blocked(self) -> None:
        """#22 同一收款人 10 分钟内第 5 笔 → 拦截（日累计已越过 1000 元硬红）。"""
        result = decide(amount=yuan(500), daily_used=yuan(2000))
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_T_DAILY)

    # ------------------------------------------------------------ #23–#26 升降级组合
    def test_case_23_new_device_plus_night_is_red(self) -> None:
        """#23 新设备 + 夜间 23:40，转账 800 元（老收款人）→ 红（两项升级叠加）。"""
        result = decide(amount=yuan(800), daily_used=0, new_device=True, hour=23)
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["escalations"], 2)

    def test_case_24_stepup_offsets_night_escalation(self) -> None:
        """#24 强验证后 5 分钟内、同一收款人、夜间 23:30 转 600 → 黄。"""
        result = decide(
            amount=yuan(600),
            daily_used=0,
            hour=23,
            stepup_age_seconds=60,
            stepup_payee="p_mom",
            payee_id="p_mom",
        )
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["escalations"], 1)
        self.assertEqual(result["discount"], 1)
        self.assertEqual(result["rule_id"], policy.R_H_DISCOUNTED)

    def test_case_25_stepup_cannot_lift_hard_red_p0(self) -> None:
        """#25 强验证后 5 分钟内再转 5000 元 → 红（金额阈值类红色不可抵扣，P0 回归）。"""
        result = decide(
            amount=yuan(5000),
            daily_used=0,
            stepup_age_seconds=60,
            stepup_payee="p_mom",
            payee_id="p_mom",
        )
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["base_level"], "red")
        self.assertEqual(result["rule_id"], policy.R_T_DAILY)
        self.assertEqual(result["escalations"], 0)
        self.assertEqual(result["discount"], 0)  # 折价不进入金额硬红

    def test_case_26_stepup_other_payee_does_not_discount(self) -> None:
        """#26 强验证后 5 分钟内、不同收款人、IM 渠道转 400 → 红（降级窗口不生效）。"""
        result = decide(
            amount=yuan(400),
            daily_used=0,
            channel="im",
            stepup_age_seconds=60,
            stepup_payee="p_mom",
            payee_id="p_landlord",
        )
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["escalations"], 1)
        self.assertEqual(result["discount"], 0)

    # ------------------------------------------------------------ #27–#28 累计边界
    def test_case_27_transfer_300_with_used_700_is_yellow(self) -> None:
        """#27 转账 300 元，当日已用 700 → 黄（累计 1000）。"""
        result = decide(amount=yuan(300), daily_used=yuan(700))
        self.assertEqual(result["level"], "yellow")

    def test_case_28_transfer_300_with_used_701_is_red(self) -> None:
        """#28 转账 300 元，当日已用 701 → 红（累计 1001）。"""
        result = decide(amount=yuan(300), daily_used=yuan(701))
        self.assertEqual(result["level"], "red")

    # ------------------------------------------------------------ #29 文档内部冲突
    def test_case_29_new_payee_500_at_night_stays_yellow(self) -> None:
        """#29 新收款人 500 元、夜间 23:30。

        文档 §8.3 表格写"红（基础黄 + 两项升级）"，但它与同一份文档的
        §6.2.2 边界 1（新收款人不进升降级表）、用例 #6（新收款人 500 → 黄）
        和用例 #21（23:59:59 转 500 → 黄，夜间升级要求金额 >500）**直接冲突**，
        且 #21 属于文档点名的"必须 100% 通过"核心用例。

        因此按 §6.2.2 与 config.LIMIT_NIGHT（严格 >500）实现：本场景为黄。
        若补齐一个真实的升级项（例如同一笔走 IM 渠道），则按规则升红——
        见下一条用例，证明升级链路本身没有失效。
        """
        result = decide(amount=yuan(500), payee_is_new=True, daily_used=0, hour=23)
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["escalations"], 0)

    def test_case_29_variant_new_payee_500_via_im_is_red(self) -> None:
        """#29 补充：新收款人 500 元 + IM 渠道 → 红（确有一个升级项）。"""
        result = decide(amount=yuan(500), payee_is_new=True, daily_used=0, hour=23, channel="im")
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["escalations"], 1)

    # ------------------------------------------------------------ #30 绿色不被升级
    def test_case_30_wealth_query_on_im_is_green(self) -> None:
        """#30 查询理财收益，渠道 = IM → 绿（绿色不被升级）。"""
        result = decide(action="get_wealth_products", amount=yuan(5000), channel="im", hour=23)
        self.assertEqual(result["level"], "green")
        self.assertEqual(result["escalations"], 0)

    # ------------------------------------------------------------ #31–#34 定时 / 长期计划
    def test_case_31_scheduled_transfer_1000_is_yellow(self) -> None:
        """#31 创建定时转账 1000 元 → 黄。"""
        result = decide(action="create_standing_plan", amount=yuan(1000))
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["rule_id"], policy.R_Y_GENERIC)

    def test_case_32_scheduled_transfer_1001_is_red(self) -> None:
        """#32 创建定时转账 1001 元 → 红。"""
        result = decide(action="create_standing_plan", amount=yuan(1001))
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_P_TEMPLATE)

    def test_case_33_standing_plan_template_800_is_yellow(self) -> None:
        """#33 创建长期计划，模板单笔 800 元 → 黄。"""
        result = decide(action="create_standing_plan", amount=yuan(800))
        self.assertEqual(result["level"], "yellow")

    def test_case_34_standing_plan_template_1200_is_red(self) -> None:
        """#34 创建长期计划，模板单笔 1200 元 → 红。"""
        result = decide(action="create_standing_plan", amount=yuan(1200))
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_P_TEMPLATE)

    # ------------------------------------------------------------ #35–#38 卡片 / 理财
    def test_case_35_apply_virtual_card_is_yellow(self) -> None:
        """#35 申请虚拟卡 → 黄。"""
        result = decide(action="apply_virtual_card", amount=_DROP)
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["rule_id"], policy.R_Y_GENERIC)

    def test_case_36_card_limit_down_5000_is_yellow(self) -> None:
        """#36 卡片额度下调 5000 元 → 黄。"""
        result = decide(action="adjust_card_limit_down", amount=yuan(5000))
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["rule_id"], policy.R_Y_ADJUST_DOWN)

    def test_case_37_card_limit_up_is_red(self) -> None:
        """#37 卡片额度上调 → 红。"""
        result = decide(action="adjust_card_limit_up", amount=yuan(1000))
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_M_RED)

    def test_case_38_redeem_wealth_2000_is_red(self) -> None:
        """#38 理财赎回 2000 元 → 红。"""
        result = decide(action="redeem_wealth", amount=yuan(2000))
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_M_RED)

    # ------------------------------------------------------------ #39 AA 收款（资金流入）
    def test_case_39_collection_request_is_yellow_and_not_outflow(self) -> None:
        """#39 AA 收款：4 人各 80 元 → 黄（资金流入，plan_outflow 不累加）。"""
        result = decide(
            action="create_collection_request",
            amount=yuan(80),
            batch_count=4,
            plan_outflow=0,  # 流入：计划净流出必须为 0（文档 §6.2.4 符号约定）
        )
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["rule_id"], policy.R_Y_GENERIC)

    # ------------------------------------------------------------ #40 硬上限
    def test_case_40_single_50000_yuan_is_denied(self) -> None:
        """#40 单笔 5 万元整 → 拒绝（deny 边界，单笔硬上限）。"""
        result = decide(amount=yuan(50000), daily_used=0)
        self.assertEqual(result["level"], "deny")
        self.assertEqual(result["rule_id"], policy.R_D_NODE_CAP)
        self.assertEqual(result["base_level"], "red")  # 先命中金额硬红，再被硬上限 deny


class TestContractRules(unittest.TestCase):
    """契约 §6 与文档 §6.2 的规则细节（40 条之外的必备回归）。"""

    # ---------------- 金额三点 / 累计边界（任务点名的额外覆盖）
    def test_amount_triple_999_1000_1001(self) -> None:
        """999 / 1000 / 1001 元三点：黄 / 黄 / 红。"""
        levels = [
            decide(amount=yuan(999), daily_used=0)["level"],
            decide(amount=yuan(1000), daily_used=0)["level"],
            decide(amount=yuan(1001), daily_used=0)["level"],
        ]
        self.assertEqual(levels, ["yellow", "yellow", "red"])

    def test_cumulative_exactly_1000_vs_1001(self) -> None:
        """累计恰好 1000 → 黄；累计 1001 → 红（严格大于才越限）。"""
        exactly = decide(amount=yuan(500), daily_used=yuan(500))
        overflow = decide(amount=yuan(500), daily_used=yuan(501))
        self.assertEqual(exactly["level"], "yellow")
        self.assertEqual(overflow["level"], "red")

    # ---------------- P0 回归
    def test_p0_stepup_window_cannot_unlock_5000(self) -> None:
        """P0：强验证后 5 分钟内转 5000 元必须是红，且 discount 不参与抵扣。"""
        result = decide(
            amount=yuan(5000),
            daily_used=0,
            stepup_age_seconds=1,
            stepup_payee="p_mom",
            payee_id="p_mom",
            channel="app",
        )
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["discount"], 0)

    def test_p0_stepup_window_cannot_unlock_new_payee_501(self) -> None:
        """P0：新收款人 501 元 + 有效强验证窗口 → 仍是红。"""
        result = decide(
            amount=yuan(501),
            payee_is_new=True,
            daily_used=0,
            stepup_age_seconds=1,
            stepup_payee="p_mom",
            payee_id="p_mom",
        )
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_T_NEW_PAYEE)
        self.assertEqual(result["discount"], 0)

    def test_p0_stepup_window_cannot_unlock_plan_outflow(self) -> None:
        """P0：计划净流出 1200 元 + 有效强验证窗口 → 仍是红。"""
        result = decide(
            amount=yuan(400),
            daily_used=0,
            plan_outflow=yuan(1200),
            stepup_age_seconds=1,
            stepup_payee="p_mom",
            payee_id="p_mom",
        )
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["discount"], 0)

    def test_green_query_on_im_stays_green(self) -> None:
        """绿色查询走 IM 渠道仍是绿（升级只作用于 base=yellow）。"""
        result = decide(action="get_accounts", amount=_DROP, channel="im")
        self.assertEqual(result["level"], "green")
        self.assertEqual(result["escalations"], 0)

    def test_new_payee_500_is_not_double_escalated(self) -> None:
        """新收款人 500 元是黄，不能被"新收款人"重复升级成红（§6.2.2 边界 1）。"""
        result = decide(amount=yuan(500), payee_is_new=True, daily_used=0)
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["escalations"], 0)
        self.assertEqual(result["base_level"], "yellow")

    # ---------------- 阈值边界
    def test_im_threshold_boundary_300_vs_300_01(self) -> None:
        """IM 渠道：恰好 300 元不升级，300.01 元升级。"""
        self.assertEqual(decide(amount=yuan(300), channel="im", daily_used=0)["level"], "yellow")
        self.assertEqual(decide(amount=30001, channel="im", daily_used=0)["level"], "red")

    def test_night_threshold_boundary_500_vs_501(self) -> None:
        """夜间：恰好 500 元不升级，501 元升级。"""
        self.assertEqual(decide(amount=yuan(500), hour=23, daily_used=0)["level"], "yellow")
        self.assertEqual(decide(amount=yuan(501), hour=23, daily_used=0)["level"], "red")

    def test_night_hour_boundaries(self) -> None:
        """夜间窗口：23:00–05:59 生效，06:00 与 22:00 不生效。"""
        self.assertEqual(decide(amount=yuan(600), hour=23)["level"], "red")
        self.assertEqual(decide(amount=yuan(600), hour=0)["level"], "red")
        self.assertEqual(decide(amount=yuan(600), hour=5)["level"], "red")
        self.assertEqual(decide(amount=yuan(600), hour=6)["level"], "yellow")
        self.assertEqual(decide(amount=yuan(600), hour=22)["level"], "yellow")

    def test_stepup_window_boundary_299_vs_300(self) -> None:
        """强验证窗口：299s 内有效，恰好 300s 失效。"""
        inside = decide(
            amount=yuan(600),
            hour=23,
            stepup_age_seconds=299,
            stepup_payee="p_mom",
            payee_id="p_mom",
        )
        expired = decide(
            amount=yuan(600),
            hour=23,
            stepup_age_seconds=300,
            stepup_payee="p_mom",
            payee_id="p_mom",
        )
        self.assertEqual(inside["level"], "yellow")
        self.assertEqual(inside["discount"], 1)
        self.assertEqual(expired["level"], "red")
        self.assertEqual(expired["discount"], 0)

    def test_stepup_requires_same_payee(self) -> None:
        """强验证必须绑定同一收款人；缺 payee_id 时不折价。"""
        result = decide(
            amount=yuan(600),
            hour=23,
            stepup_age_seconds=60,
            stepup_payee="p_mom",
            payee_id=_DROP,
        )
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["discount"], 0)

    def test_stepup_offset_is_single_item_only(self) -> None:
        """折价只抵消 1 项：两项升级抵消 1 项后仍为红。"""
        result = decide(
            amount=yuan(600),
            hour=23,
            new_device=True,
            stepup_age_seconds=60,
            stepup_payee="p_mom",
            payee_id="p_mom",
        )
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["base_level"], "yellow")
        self.assertEqual(result["escalations"], 2)
        self.assertEqual(result["discount"], 1)
        self.assertEqual(result["rule_id"], policy.R_H_ESCALATED)

    def test_stepup_without_escalation_keeps_yellow(self) -> None:
        """没有升级项时折价不产生副作用。"""
        result = decide(
            amount=yuan(300),
            stepup_age_seconds=60,
            stepup_payee="p_mom",
            payee_id="p_mom",
        )
        self.assertEqual(result["level"], "yellow")
        self.assertEqual(result["escalations"], 0)
        self.assertEqual(result["discount"], 1)

    def test_escalation_flags_are_all_supported(self) -> None:
        """四个升级信号逐一生效（base=yellow）。"""
        single = [
            decide(amount=yuan(400), channel="im")["level"],
            decide(amount=yuan(600), hour=23)["level"],
            decide(amount=yuan(100), new_device=True)["level"],
            decide(amount=yuan(100), risk_level="high")["level"],
        ]
        self.assertEqual(single, ["red", "red", "red", "red"])

    def test_red_base_never_reports_escalations(self) -> None:
        """base=red 的恒红动作不吃升级信号（escalations/discount 均为 0）。"""
        result = decide(
            action="purchase_wealth",
            amount=yuan(1000),
            channel="im",
            hour=23,
            new_device=True,
            risk_level="high",
            stepup_age_seconds=1,
            stepup_payee="p_mom",
            payee_id="p_mom",
        )
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["escalations"], 0)
        self.assertEqual(result["discount"], 0)

    # ---------------- deny 规则
    def test_plan_outflow_over_50k_is_denied(self) -> None:
        """计划净流出 >5 万 → deny（R-D-02）。"""
        result = decide(plan_outflow=config.HARD_CAP_PER_PLAN + 1)
        self.assertEqual(result["level"], "deny")
        self.assertEqual(result["rule_id"], policy.R_D_PLAN_OUTFLOW)

    def test_plan_outflow_exactly_50k_is_red_not_deny(self) -> None:
        """计划净流出恰好 5 万：不触发 deny，但触发计划防拆分硬红。"""
        result = decide(amount=yuan(100), daily_used=0, plan_outflow=config.HARD_CAP_PER_PLAN)
        self.assertEqual(result["level"], "red")
        self.assertEqual(result["rule_id"], policy.R_P_OUTFLOW)

    def test_risk_score_boundary_90_vs_91(self) -> None:
        """risk_score > 90 才 deny；恰好 90 走正常矩阵。"""
        self.assertEqual(decide(amount=yuan(100), risk_score=90)["level"], "yellow")
        denied = decide(amount=yuan(100), risk_score=91)
        self.assertEqual(denied["level"], "deny")
        self.assertEqual(denied["rule_id"], policy.R_D_RISK_SCORE)

    def test_session_state_locked_is_case_insensitive(self) -> None:
        """契约表里是 LOCKED、Rego 里是 locked，两种写法都必须 deny。"""
        for state in ("LOCKED", "locked", "Locked"):
            with self.subTest(state=state):
                self.assertEqual(decide(session_state=state)["level"], "deny")

    def test_node_cap_boundary_4999999_vs_5000000(self) -> None:
        """单笔硬上限：4,999,999 分仍走金额硬红，5,000,000 分起 deny。"""
        just_below = decide(amount=4_999_999, daily_used=0)
        at_cap = decide(amount=5_000_000, daily_used=0)
        self.assertEqual(just_below["level"], "red")
        self.assertEqual(at_cap["level"], "deny")
        self.assertEqual(at_cap["rule_id"], policy.R_D_NODE_CAP)

    def test_batch_count_boundary_1_vs_2(self) -> None:
        """批量边界：1 条可行，2 条起恒红。"""
        self.assertEqual(decide(action="cancel_subscription", amount=yuan(10), batch_count=1)["level"], "yellow")
        self.assertEqual(decide(action="cancel_subscription", amount=yuan(10), batch_count=2)["level"], "red")

    def test_modify_password_variants_are_denied(self) -> None:
        """密码类动作（含别名）一律 deny。"""
        for action in ("modify_password", "change_password", "reset_password"):
            with self.subTest(action=action):
                result = decide(action=action, amount=_DROP)
                self.assertEqual(result["level"], "deny")
                self.assertEqual(result["rule_id"], policy.R_D_PASSWORD)

    def test_unknown_action_is_denied(self) -> None:
        """未登记动作 → deny（默认拒绝，不是默认放行）。"""
        result = decide(action="wire_money_to_anyone", amount=yuan(1))
        self.assertEqual(result["level"], "deny")
        self.assertEqual(result["rule_id"], policy.R_D_UNKNOWN)
        self.assertEqual(result["base_level"], "deny")

    def test_system_tool_is_denied(self) -> None:
        """系统内部工具（notify）不接受用户侧调用。"""
        result = decide(action="notify")
        self.assertEqual(result["level"], "deny")
        self.assertEqual(result["rule_id"], policy.R_D_SYSTEM)

    def test_transfer_action_aliases_resolve(self) -> None:
        """文档/Rego 里的 transfer 简写等价于 create_transfer。"""
        alias = decide(action="transfer", amount=yuan(500), daily_used=0)
        canonical = decide(action="create_transfer", amount=yuan(500), daily_used=0)
        self.assertEqual(alias, canonical)

    # ---------------- 入参残缺（不得抛异常）
    def test_missing_transfer_amount_is_denied(self) -> None:
        """transfer 缺 amount → deny（R-D-05）。"""
        result = decide(amount=_DROP)
        self.assertEqual(result["level"], "deny")
        self.assertEqual(result["rule_id"], policy.R_D_INCOMPLETE)

    def test_missing_daily_used_is_denied(self) -> None:
        """transfer 缺 daily_used → deny（R-D-05），绝不按 0 处理。"""
        result = decide(amount=yuan(500), daily_used=_DROP)
        self.assertEqual(result["level"], "deny")
        self.assertEqual(result["rule_id"], policy.R_D_INCOMPLETE)

    def test_zero_or_negative_transfer_amount_is_denied(self) -> None:
        """0 / 负数金额视为未提供（从严）。"""
        self.assertEqual(decide(amount=0)["rule_id"], policy.R_D_INCOMPLETE)
        self.assertEqual(decide(amount=-100)["rule_id"], policy.R_D_INCOMPLETE)

    def test_empty_or_non_mapping_ctx_does_not_raise(self) -> None:
        """空 ctx / 非 dict / action 非字符串都不抛异常，统一 deny。"""
        for ctx in ({}, None, [], {"action": None}, {"action": 123}, {"action": "  "}):
            with self.subTest(ctx=ctx):
                result = policy.decide(ctx)  # type: ignore[arg-type]
                self.assertEqual(result["level"], "deny")
                self.assertEqual(result["rule_id"], policy.R_D_UNKNOWN)

    def test_green_query_tolerates_missing_optional_fields(self) -> None:
        """只读查询缺金额类字段不影响绿色。"""
        result = policy.decide({"action": "get_transactions"})
        self.assertEqual(result["level"], "green")

    # ---------------- 返回体契约
    def test_result_shape_is_exact(self) -> None:
        """返回字段严格等于契约 §6 定义的 7 个键。"""
        samples = [
            decide(amount=yuan(100)),                       # yellow
            decide(amount=yuan(5000)),                      # red
            decide(action="get_accounts", amount=_DROP),    # green
            decide(action="modify_password", amount=_DROP), # deny
        ]
        for result in samples:
            with self.subTest(level=result["level"]):
                self.assertEqual(set(result), RESULT_KEYS)

    def test_level_values_are_from_contract_enum(self) -> None:
        """level / base_level 只能是契约枚举值。"""
        valid = {"green", "yellow", "red", "deny"}
        for amount in (0, yuan(100), yuan(1001), yuan(50000)):
            result = decide(amount=amount)
            self.assertIn(result["level"], valid)
            self.assertIn(result["base_level"], valid)

    def test_rule_id_format(self) -> None:
        """rule_id 形如 R-T-01（便于审计检索与答辩讲解）。"""
        samples = [
            decide(amount=yuan(100)),
            decide(amount=yuan(1001)),
            decide(amount=yuan(400), channel="im"),
            decide(action="get_accounts", amount=_DROP),
            decide(action="modify_password", amount=_DROP),
            decide(amount=_DROP),
        ]
        for result in samples:
            with self.subTest(rule_id=result["rule_id"]):
                self.assertRegex(result["rule_id"], RULE_ID_PATTERN)

    def test_required_factors_per_level(self) -> None:
        """red 双因子 / yellow 显式确认 / green 免确认 / deny 不可执行。"""
        self.assertEqual(
            decide(amount=yuan(5000))["required_factors"], ["sms", "face_or_ushield"]
        )
        self.assertEqual(decide(amount=yuan(100))["required_factors"], ["explicit_confirm"])
        self.assertEqual(decide(action="get_accounts", amount=_DROP)["required_factors"], [])
        self.assertEqual(decide(action="modify_password", amount=_DROP)["required_factors"], [])

    def test_reason_is_chinese_and_contains_money(self) -> None:
        """reason 为中文且金额按 x/y 元渲染（千分位 + 两位小数）。"""
        reasons = [
            decide(amount=yuan(1001), daily_used=0)["reason"],
            decide(amount=yuan(501), payee_is_new=True, daily_used=0)["reason"],
            decide(action="cancel_subscription", amount=yuan(299))["reason"],
            decide(amount=yuan(400), channel="im")["reason"],
        ]
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertIn("元", reason)
                self.assertRegex(reason, r"\d[\d,]*\.\d{2}")
        self.assertIn("1,001.00/1,000.00 元", reasons[0])
        self.assertIn("501.00/500.00 元", reasons[1])

    def test_policy_does_not_mutate_ctx(self) -> None:
        """策略是纯函数：不修改调用方传入的 ctx。"""
        ctx = build_ctx(amount=yuan(400), channel="im", hour=23)
        snapshot = copy.deepcopy(ctx)
        policy.decide(ctx)
        self.assertEqual(ctx, snapshot)

    def test_policy_is_deterministic(self) -> None:
        """同一输入两次裁决结果完全一致（无隐藏状态）。"""
        ctx = build_ctx(amount=yuan(600), hour=23, stepup_age_seconds=60,
                        stepup_payee="p_mom", payee_id="p_mom")
        self.assertEqual(policy.decide(ctx), policy.decide(ctx))

    def test_policy_module_is_pure_logic(self) -> None:
        """只依赖 config/specs，不 import bankcore/db/sqlite3（契约 §2 依赖方向）。"""
        tree = ast.parse(inspect.getsource(policy))
        absolute: set[str] = set()
        relative: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    relative.update(alias.name for alias in node.names)
                elif node.module:
                    absolute.add(node.module)
            elif isinstance(node, ast.Import):
                absolute.update(alias.name for alias in node.names)
        self.assertEqual(absolute, {"__future__", "typing"})
        self.assertEqual(relative, {"config", "specs"})

    def test_numeric_string_amount_is_accepted(self) -> None:
        """容错：数字字符串按分解释（JSON/表单偶发），仍不改变裁决语义。"""
        as_string = decide(amount="50000", daily_used="0")
        as_int = decide(amount=50000, daily_used=0)
        self.assertEqual(as_string, as_int)
        self.assertEqual(as_string["level"], "yellow")

    def test_bool_amount_is_treated_as_missing(self) -> None:
        """bool 不是金额（True == 1 的坑要堵住）。"""
        result = decide(amount=True)
        self.assertEqual(result["rule_id"], policy.R_D_INCOMPLETE)


class TestMatrixCoverage(unittest.TestCase):
    """矩阵完整性：specs 里的每个工具都必须有明确等级，不允许"漏网"。"""

    def test_all_callable_tools_are_classified(self) -> None:
        """所有可生成工具都能得到 green/yellow/red，绝不 deny。"""
        for name in specs.CALLABLE_TOOLS:
            with self.subTest(tool=name):
                result = decide(action=name, amount=yuan(100), daily_used=0, batch_count=1)
                self.assertIn(result["level"], {"green", "yellow", "red"})

    def test_green_tools_stay_green_under_every_signal(self) -> None:
        """只读工具在 IM / 夜间 / 新设备 / 高风险 / 计划聚合下仍是绿。"""
        for name in specs.GREEN_TOOLS:
            with self.subTest(tool=name):
                result = decide(
                    action=name,
                    amount=yuan(3000),
                    channel="im",
                    hour=23,
                    new_device=True,
                    risk_level="high",
                    plan_outflow=yuan(2000),
                )
                self.assertEqual(result["level"], "green")
                self.assertEqual(result["escalations"], 0)

    def test_yellow_tools_are_yellow_at_small_amount(self) -> None:
        """低风险写入在小额、无信号时为黄。"""
        for name in specs.YELLOW_TOOLS:
            with self.subTest(tool=name):
                result = decide(action=name, amount=yuan(100), daily_used=0,
                                batch_count=1, plan_outflow=0)
                self.assertEqual(result["level"], "yellow")
                self.assertEqual(result["base_level"], "yellow")

    def test_red_tools_are_red(self) -> None:
        """红组工具（含大额转账）在矩阵层恒红。"""
        for name in specs.RED_TOOLS:
            with self.subTest(tool=name):
                result = decide(action=name, amount=yuan(100), daily_used=0)
                self.assertEqual(result["level"], "red")

    def test_pseudo_actions_are_classified(self) -> None:
        """契约 §6 追加的两个伪动作（额度上调/下调）单独登记。"""
        self.assertEqual(decide(action="adjust_card_limit_up", amount=yuan(100))["level"], "red")
        self.assertEqual(decide(action="adjust_card_limit_down", amount=yuan(100))["level"], "yellow")


class TestDocCasesCore(unittest.TestCase):
    """文档点名"必须 100% 通过"的核心用例：#1–#5、#10–#11、#21、#24–#26。"""

    def test_core_cases_are_all_green(self) -> None:
        cases = [
            ("#1", decide(amount=yuan(999), daily_used=0), "yellow"),
            ("#2", decide(amount=yuan(1000), daily_used=0), "yellow"),
            ("#3", decide(amount=yuan(1001), daily_used=0), "red"),
            ("#4", decide(amount=yuan(500), daily_used=yuan(500)), "yellow"),
            ("#5", decide(amount=yuan(500), daily_used=yuan(501)), "red"),
            ("#10", decide(amount=yuan(400), plan_outflow=yuan(1200)), "red"),
            ("#11", decide(amount=yuan(500), plan_outflow=yuan(1500)), "red"),
            ("#21a", decide(amount=yuan(500), hour=23, daily_used=0), "yellow"),
            ("#21b", decide(amount=yuan(500), hour=0, daily_used=0), "yellow"),
            ("#24", decide(amount=yuan(600), hour=23, stepup_age_seconds=60,
                           stepup_payee="p_mom", payee_id="p_mom"), "yellow"),
            ("#25", decide(amount=yuan(5000), stepup_age_seconds=60,
                           stepup_payee="p_mom", payee_id="p_mom"), "red"),
            ("#26", decide(amount=yuan(400), channel="im", stepup_age_seconds=60,
                           stepup_payee="p_mom", payee_id="p_landlord"), "red"),
        ]
        for label, result, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(result["level"], expected, msg=f"{label} 期望 {expected}")


if __name__ == "__main__":
    unittest.main()
