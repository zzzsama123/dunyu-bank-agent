"""WP-NLU 单元测试：NLU / Planner / Validator。

运行：cd backend && python -m unittest tests.test_nlu -v
"""

from __future__ import annotations

import unittest

from bankagent import config, specs
from bankagent.nlu import parse
from bankagent.planner import build_plan
from bankagent.validator import validate


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
def make_node(node_id, tool, args=None, provenance=None, depends_on=None, risk=None):
    return {
        "id": node_id,
        "tool": tool,
        "args": args or {},
        "provenance": provenance or {},
        "depends_on": depends_on or [],
        "risk_hint": risk or "green",
    }


def make_plan(nodes, intent="transfer"):
    return {
        "plan_id": "pl_test0001",
        "intent": intent,
        "goal": "测试计划",
        "nodes": nodes,
        "confirm_points": [],
    }


def codes(errors):
    return [e["code"] for e in errors]


class NLUTestCase(unittest.TestCase):
    # ------------------------- 金额 -------------------------
    def test_amount_arabic_plain(self):
        r = parse("转500给张三", {})
        self.assertEqual(r["name"], "transfer")
        self.assertEqual(r["slots"]["amount"], 50_000)
        self.assertEqual(r["spans"]["amount"], "500")

    def test_amount_arabic_comma(self):
        r = parse("转1,200给张三", {})
        self.assertEqual(r["slots"]["amount"], 120_000)

    def test_amount_chinese(self):
        r = parse("转五百给张三", {})
        self.assertEqual(r["slots"]["amount"], 50_000)

    def test_amount_arabic_with_chinese_unit(self):
        r = parse("转5百给张三", {})
        self.assertEqual(r["slots"]["amount"], 50_000)

    def test_amount_half_thousand(self):
        r = parse("转半千给张三", {})
        self.assertEqual(r["slots"]["amount"], 50_000)

    def test_amount_block_suffix(self):
        r = parse("转1200块给张三", {})
        self.assertEqual(r["slots"]["amount"], 120_000)

    def test_amount_wan(self):
        r = parse("转5万给张三", {})
        self.assertEqual(r["slots"]["amount"], 5_000_000)

    def test_amount_chinese_colloquial(self):
        r = parse("转一千二给张三", {})
        self.assertEqual(r["slots"]["amount"], 120_000)

    def test_transfer_da_verb(self):
        r = parse("打5000给老婆", {})
        self.assertEqual(r["name"], "transfer")
        self.assertEqual(r["slots"]["amount"], 500_000)
        self.assertEqual(r["slots"]["payee"], "老婆")
        self.assertEqual(r["missing"], [])

    # ------------------------- 收款人 -------------------------
    def test_payee_before_verb(self):
        r = parse("给我妈转500", {})
        self.assertEqual(r["slots"]["payee"], "妈")
        self.assertEqual(r["spans"]["payee"], "我妈")
        self.assertEqual(r["missing"], [])

    def test_payee_after_amount(self):
        r = parse("转 500 给张三", {})
        self.assertEqual(r["slots"]["payee"], "张三")
        self.assertEqual(r["slots"]["amount"], 50_000)

    def test_payee_landlord(self):
        r = parse("给房东转2500", {})
        self.assertEqual(r["slots"]["payee"], "房东")
        self.assertEqual(r["slots"]["amount"], 250_000)

    def test_payee_birthday(self):
        r = parse("给老婆准备生日", {})
        self.assertEqual(r["name"], "standing_plan")
        self.assertEqual(r["slots"]["payee"], "老婆")
        self.assertEqual(r["slots"]["goal"], "生日准备")

    # ------------------------- 意图关键词 -------------------------
    def test_intent_bill_analysis(self):
        r = parse("我这个月钱都去哪了", {})
        self.assertEqual(r["name"], "bill_analysis")
        self.assertEqual(r["slots"]["range"], "this_month")
        self.assertEqual(r["spans"]["range"], "这个月")

    def test_bill_anomaly_without_context(self):
        r = parse("有没有异常交易", {})
        self.assertEqual(r["name"], "bill_analysis")
        self.assertEqual(r["slots"]["focus"], "anomaly")
        self.assertEqual(r["missing"], [])

    def test_bill_spending_phrase(self):
        r = parse("最近花钱有点多", {})
        self.assertEqual(r["name"], "bill_analysis")
        self.assertNotEqual(r["slots"].get("focus"), "anomaly")

    def test_intent_subscription(self):
        r = parse("我都被扣了什么会员", {})
        self.assertEqual(r["name"], "subscription")
        self.assertEqual(r["slots"]["action"], "query")

    def test_subscription_deduction_keyword(self):
        r = parse("查一下代扣", {})
        self.assertEqual(r["name"], "subscription")
        self.assertEqual(r["slots"]["action"], "query")

    def test_intent_wealth(self):
        r = parse("有什么稳一点的理财", {})
        self.assertEqual(r["name"], "wealth")

    def test_wealth_buy_without_product(self):
        r = parse("买5000那个", {})
        self.assertEqual(r["name"], "wealth")
        self.assertEqual(r["slots"]["action"], "purchase")
        self.assertEqual(r["slots"]["amount"], 500_000)
        self.assertIn("product", r["missing"])

    def test_intent_card(self):
        r = parse("我卡丢了，帮我挂失", {})
        self.assertEqual(r["name"], "card")
        self.assertEqual(r["slots"]["action"], "lost")

    def test_intent_standing_birthday(self):
        r = parse("我老婆下个月生日，帮我准备一下，别超过1000", {})
        self.assertEqual(r["name"], "standing_plan")
        self.assertEqual(r["slots"]["goal"], "生日准备")
        self.assertEqual(r["slots"]["payee"], "老婆")
        self.assertEqual(r["slots"]["budget"], 100_000)

    def test_intent_standing_arrange(self):
        r = parse("安排一下下个月的事", {})
        self.assertEqual(r["name"], "standing_plan")
        self.assertEqual(r["slots"]["when"], "下个月")

    def test_intent_standing_reminder(self):
        r = parse("定个提醒", {})
        self.assertEqual(r["name"], "standing_plan")
        self.assertEqual(r["slots"]["goal"], "定时提醒")

    # ------------------------- 模糊 / 省略 -------------------------
    def test_missing_amount(self):
        r = parse("转点钱给我妈", {})
        self.assertIn("amount", r["missing"])
        self.assertEqual(r["slots"]["payee"], "妈")

    def test_carry_over(self):
        ctx = {"last_intent": {"name": "transfer", "slots": {"amount": 50_000, "payee": "妈"}}}
        r = parse("再转一次", ctx)
        self.assertEqual(r["name"], "transfer")
        self.assertTrue(r.get("carry_over"))
        self.assertEqual(r["slots"]["amount"], 50_000)
        self.assertEqual(r["slots"]["payee"], "妈")
        self.assertEqual(r["missing"], [])

    def test_recent_entity(self):
        ctx = {"recent_entities": {"intent": "bill_analysis", "range": "last_month"}}
        r = parse("上次那个", ctx)
        self.assertEqual(r["name"], "bill_analysis")
        self.assertEqual(r["slots"]["range"], "last_month")

    def test_bill_anomaly_followup(self):
        ctx = {"last_intent": {"name": "bill_analysis", "slots": {"range": "last_month"}}}
        r = parse("有没有不对劲的", ctx)
        self.assertEqual(r["name"], "bill_analysis")
        self.assertEqual(r["slots"]["focus"], "anomaly")
        self.assertEqual(r["slots"]["range"], "last_month")
        self.assertEqual(r["missing"], [])

    def test_batch_cancel(self):
        ctx = {"recent_entities": {"subscriptions": [{"id": 3}, {"id": 5}]}}
        r = parse("都退了吧", ctx)
        self.assertEqual(r["name"], "subscription")
        self.assertTrue(r.get("batch"))
        self.assertTrue(r["slots"]["batch"])
        self.assertEqual(r["slots"]["action"], "cancel")
        self.assertEqual(r["slots"]["sub_ids"], [3, 5])

    def test_batch_cancel_phrase_needs_context(self):
        ctx = {"recent_entities": {"subscriptions": [{"id": 1, "unused_days": 90}]}}
        r = parse("不需要了", ctx)
        self.assertEqual(r["name"], "subscription")
        self.assertTrue(r.get("batch"))
        self.assertEqual(r["slots"]["action"], "cancel")

    # ------------------------- AA -------------------------
    def test_aa_total_split(self):
        r = parse("中午吃饭320，四个人AA", {})
        self.assertEqual(r["name"], "transfer")
        self.assertEqual(r["slots"]["mode"], "collection")
        self.assertEqual(r["slots"]["amount"], 8_000)       # 人均 80 元
        self.assertEqual(r["slots"]["total_amount"], 32_000)  # 总额 320 元
        self.assertEqual(r["slots"]["person_count"], 4)
        self.assertEqual(r["slots"]["payer_count"], 3)

    def test_aa_each(self):
        r = parse("各转80", {})
        self.assertEqual(r["name"], "transfer")
        self.assertEqual(r["slots"]["mode"], "collection")
        self.assertEqual(r["slots"]["amount"], 8_000)

    def test_aa_split_phrase(self):
        r = parse("中午吃饭320，四个人平摊", {})
        self.assertEqual(r["slots"]["mode"], "collection")
        self.assertEqual(r["slots"]["amount"], 8_000)

    # ------------------------- 健壮性 -------------------------
    def test_unknown(self):
        r = parse("今天天气怎么样", {})
        self.assertEqual(r["name"], "unknown")
        self.assertLessEqual(r["confidence"], 0.3)

    def test_never_raises(self):
        for text in (None, 123, ["转500"], {"a": 1}, "", "   ", "\x00\x01"):
            r = parse(text, None)
            self.assertIn("name", r)
            self.assertIn("slots", r)
            self.assertIn("confidence", r)
            self.assertGreaterEqual(r["confidence"], 0.0)
            self.assertLessEqual(r["confidence"], 1.0)

    def test_confidence_range_for_known(self):
        for text in ("给我妈转500", "都退了吧", "我卡丢了", "有什么稳一点的理财"):
            r = parse(text, {})
            self.assertGreaterEqual(r["confidence"], 0.0)
            self.assertLessEqual(r["confidence"], 1.0)


class PlannerTestCase(unittest.TestCase):
    def test_transfer_small(self):
        plan = build_plan(parse("给我妈转500", {}), {})
        self.assertEqual([n["tool"] for n in plan["nodes"]],
                         ["get_contacts", "quote_transfer", "create_transfer"])
        self.assertEqual(plan["confirm_points"], ["n3"])
        self.assertEqual(plan["nodes"][1]["args"]["amount"], 50_000)
        self.assertEqual(plan["nodes"][1]["args"]["contact_id"], "$n1.contact_id")
        self.assertEqual(plan["nodes"][1]["args"]["payee"], "妈")
        self.assertEqual(plan["intent"], "transfer")

    def test_transfer_large_uses_submit(self):
        plan = build_plan(parse("转1200给张三", {}), {})
        self.assertEqual(plan["nodes"][-1]["tool"], "submit_transfer")

    def test_transfer_exact_threshold_uses_create(self):
        plan = build_plan(parse("给张三转1000", {}), {})
        self.assertEqual(plan["nodes"][-1]["tool"], "create_transfer")
        self.assertEqual(plan["nodes"][-1]["args"]["amount"], config.LIMIT_DAILY)

    def test_aa_collection(self):
        ctx = {"contacts": ["张三", "李四", "王五"]}
        plan = build_plan(parse("中午吃饭320，四个人AA", ctx), ctx)
        tools = [n["tool"] for n in plan["nodes"]]
        self.assertEqual(tools, ["get_contacts", "get_contacts", "get_contacts",
                                 "create_collection_request"])
        last = plan["nodes"][-1]
        self.assertEqual(last["args"]["amount_each"], 8_000)
        self.assertEqual(len(last["args"]["payer_contact_ids"]), 3)
        self.assertEqual(plan["confirm_points"], ["n4"])

    def test_bill_plan(self):
        plan = build_plan(parse("我这个月钱都去哪了", {}), {})
        self.assertEqual([n["tool"] for n in plan["nodes"]],
                         ["analyze_spending", "detect_anomaly"])
        self.assertEqual(plan["nodes"][0]["args"]["range"], "this_month")
        self.assertEqual(plan["confirm_points"], [])

    def test_bill_anomaly_followup_plan(self):
        ctx = {"last_intent": {"name": "bill_analysis", "slots": {"range": "last_month"}}}
        plan = build_plan(parse("有没有不对劲的", ctx), ctx)
        self.assertEqual([n["tool"] for n in plan["nodes"]], ["detect_anomaly"])
        self.assertEqual(plan["nodes"][0]["args"]["range"], "last_month")
        self.assertEqual(plan["confirm_points"], [])

    def test_bill_anomaly_query_plan(self):
        plan = build_plan(parse("有没有异常交易", {}), {})
        self.assertEqual([n["tool"] for n in plan["nodes"]], ["detect_anomaly"])

    def test_subscription_cancel_plan(self):
        ctx = {"recent_entities": {"subscriptions": [
            {"id": 3, "merchant": "视频会员A", "unused_days": 190},
            {"id": 5, "merchant": "云存储B", "unused_days": 90},
        ]}}
        plan = build_plan(parse("都退了吧", ctx), ctx)
        self.assertEqual([n["tool"] for n in plan["nodes"]],
                         ["get_subscriptions", "cancel_subscription", "cancel_subscription"])
        self.assertEqual(plan["nodes"][1]["args"]["sub_id"], 3)
        self.assertEqual(plan["nodes"][2]["args"]["sub_id"], 5)
        self.assertEqual(plan["confirm_points"], ["n2", "n3"])

    def test_batch_cancel_only_unused_and_max_three(self):
        ctx = {"recent_entities": {"subscriptions": [
            {"id": 1, "merchant": "视频会员A", "unused_days": 190},
            {"id": 2, "merchant": "云存储B", "unused_days": 90},
            {"id": 3, "merchant": "健身C", "unused_days": 60},
            {"id": 4, "merchant": "仍在用D", "unused_days": 3},
            {"id": 5, "merchant": "视频会员E", "unused_days": 120},
        ]}}
        plan = build_plan(parse("把没用的都退了", ctx), ctx)
        cancels = [n for n in plan["nodes"] if n["tool"] == "cancel_subscription"]
        self.assertEqual(len(cancels), 3)
        self.assertEqual([n["args"]["sub_id"] for n in cancels], [1, 2, 3])
        self.assertEqual([n["depends_on"] for n in cancels], [[], [], []])
        self.assertEqual(cancels[0]["args"]["merchant"], "视频会员A")

    def test_batch_cancel_without_context_only_queries(self):
        ctx = {"recent_entities": {"subscriptions": [
            {"id": 1, "merchant": "仍在用", "unused_days": 3},
        ]}}
        plan = build_plan(parse("都退了吧", ctx), ctx)
        self.assertEqual([n["tool"] for n in plan["nodes"]], ["get_subscriptions"])

    def test_birthday_four_nodes(self):
        plan = build_plan(parse("我老婆下个月生日，帮我准备一下，别超过1000", {}), {})
        self.assertEqual([n["tool"] for n in plan["nodes"]],
                         ["get_contacts", "get_profile", "freeze_amount",
                          "create_standing_plan"])
        self.assertEqual(len(plan["nodes"]), 4)
        self.assertEqual(plan["nodes"][2]["args"]["amount"], 100_000)
        self.assertEqual(plan["nodes"][3]["args"]["budget"], 100_000)
        self.assertEqual(plan["nodes"][3]["depends_on"], ["n2", "n3"])
        self.assertEqual(plan["confirm_points"], ["n3", "n4"])

    def test_wealth_purchase_plan(self):
        plan = build_plan(parse("买5000理财", {}), {})
        self.assertEqual([n["tool"] for n in plan["nodes"]],
                         ["get_wealth_products", "purchase_wealth"])
        self.assertEqual(plan["nodes"][1]["args"]["amount"], 500_000)
        self.assertEqual(plan["nodes"][1]["args"]["product_id"], "$n1.product_id")
        self.assertEqual(plan["confirm_points"], ["n2"])

    def test_card_lost_plan(self):
        plan = build_plan(parse("我卡丢了，帮我挂失", {}), {})
        self.assertEqual([n["tool"] for n in plan["nodes"]],
                         ["get_accounts", "report_card_lost"])
        self.assertEqual(plan["nodes"][1]["args"]["card_id"], "$n1.cards[0].card_id")
        self.assertEqual(plan["confirm_points"], ["n2"])

    def test_plan_meta_and_whitelist(self):
        intents = [
            parse("给我妈转500", {}),
            parse("转1200给张三", {}),
            parse("我这个月钱都去哪了", {}),
            parse("都退了吧", {"recent_entities": {"subscriptions": [{"id": 1}]}}),
            parse("我老婆下个月生日，帮我准备一下，别超过1000", {}),
            parse("买5000理财", {}),
            parse("我卡丢了", {}),
        ]
        for intent in intents:
            plan = build_plan(intent, {})
            self.assertTrue(plan["plan_id"].startswith("pl_"))
            self.assertLessEqual(len(plan["nodes"]), specs.MAX_PLAN_NODES)
            self.assertEqual(len({n["id"] for n in plan["nodes"]}), len(plan["nodes"]))
            self.assertTrue(set(plan["confirm_points"]) <= {n["id"] for n in plan["nodes"]})
            for node in plan["nodes"]:
                self.assertIn(node["tool"], specs.CALLABLE_TOOLS)
            self.assertIsInstance(plan.get("intent"), str)

    def test_planner_fund_provenance_never_context(self):
        plans = [
            build_plan(parse("给我妈转500", {}), {}),
            build_plan(parse("转1200给张三", {}), {}),
            build_plan(parse("中午吃饭320，四个人AA",
                             {"contacts": ["张三", "李四", "王五"]}),
                       {"contacts": ["张三", "李四", "王五"]}),
            build_plan(parse("我老婆下个月生日，帮我准备一下，别超过1000", {}), {}),
        ]
        for plan in plans:
            for node in plan["nodes"]:
                for param in specs.FUND_PARAMS:
                    if param in node["args"]:
                        source = node["provenance"].get(param)
                        self.assertIn(source, {"user", "tool", "const"})
                        self.assertNotEqual(source, "context")


class ValidatorTestCase(unittest.TestCase):
    def test_valid_plan_returns_empty(self):
        plan = build_plan(parse("给我妈转500", {}), {})
        self.assertEqual(validate(plan, specs.TOOL_SPECS), [])

    def test_node_count(self):
        nodes = [make_node(f"n{i}", "get_accounts") for i in range(1, 10)]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_PLAN_TOO_LARGE", codes(errs))

    def test_cycle(self):
        nodes = [
            make_node("n1", "get_accounts", depends_on=["n2"]),
            make_node("n2", "get_accounts", depends_on=["n1"]),
        ]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_CYCLE", codes(errs))

    def test_tool_whitelist(self):
        nodes = [make_node("n1", "hack_transfer", {"amount": 1})]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_TOOL:n1", codes(errs))

    def test_system_tool_rejected(self):
        nodes = [make_node("n1", "notify", {"content": "hi"})]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_TOOL:n1", codes(errs))

    def test_required_args(self):
        nodes = [make_node(
            "n1", "create_transfer",
            {"amount": 50_000},
            {"amount": "user"},
        )]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_ARGS:n1", codes(errs))

    def test_money_ref_rejected(self):
        nodes = [make_node(
            "n1", "create_transfer",
            {"contact_id": 1, "amount": "$n9.amount"},
            {"contact_id": "tool", "amount": "user"},
        )]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_ARGS:n1", codes(errs))

    def test_tainted_arg(self):
        nodes = [make_node(
            "n1", "create_transfer",
            {"contact_id": 1, "amount": 50_000},
            {"contact_id": "tool", "amount": "context"},
        )]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_TAINTED_ARG:n1.amount", codes(errs))

    def test_missing_provenance(self):
        nodes = [make_node(
            "n1", "create_transfer",
            {"contact_id": 1, "amount": 50_000},
            {"contact_id": "tool"},
        )]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_TAINTED_ARG:n1.amount", codes(errs))

    def test_single_node_cap(self):
        nodes = [
            make_node("n1", "submit_transfer",
                      {"contact_id": 1, "amount": 5_000_001},
                      {"contact_id": "tool", "amount": "user"}),
            make_node("n2", "create_collection_request",
                      {"payer_contact_ids": [1], "amount_each": 1},
                      {"payer_contact_ids": "tool", "amount_each": "user"}),
        ]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_CAP:n1", codes(errs))
        self.assertNotIn("E_PLAN_OUTFLOW_CAP", codes(errs))

    def test_plan_outflow_cap(self):
        nodes = [
            make_node(f"n{i}", "create_transfer",
                      {"contact_id": 1, "amount": 1_000_000},
                      {"contact_id": "tool", "amount": "user"})
            for i in range(1, 7)
        ]
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertIn("E_PLAN_OUTFLOW_CAP", codes(errs))

    def test_inflow_subtracted(self):
        nodes = [
            make_node(f"n{i}", "create_transfer",
                      {"contact_id": 1, "amount": 1_000_000},
                      {"contact_id": "tool", "amount": "user"})
            for i in range(1, 7)
        ]
        nodes.append(make_node(
            "n7", "create_collection_request",
            {"payer_contact_ids": [1, 2], "amount_each": 2_000_000},
            {"payer_contact_ids": "tool", "amount_each": "user"},
        ))
        errs = validate(make_plan(nodes), specs.TOOL_SPECS)
        self.assertNotIn("E_PLAN_OUTFLOW_CAP", codes(errs))

    def test_exact_cap_allowed(self):
        nodes = [
            make_node(f"n{i}", "create_transfer",
                      {"contact_id": 1, "amount": 1_000_000},
                      {"contact_id": "tool", "amount": "user"})
            for i in range(1, 6)
        ]
        self.assertEqual(validate(make_plan(nodes), specs.TOOL_SPECS), [])

    def test_malformed_plan_does_not_raise(self):
        self.assertTrue(validate(None, specs.TOOL_SPECS))
        self.assertTrue(validate({"nodes": "bad"}, specs.TOOL_SPECS))
        self.assertEqual(validate({"nodes": []}, specs.TOOL_SPECS), [])

    def test_validator_does_not_mutate_plan(self):
        plan = build_plan(parse("给我妈转500", {}), {})
        before = repr(plan)
        validate(plan, specs.TOOL_SPECS)
        self.assertEqual(before, repr(plan))


if __name__ == "__main__":
    unittest.main()
