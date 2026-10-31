"""工具层测试：注册表完整性、26 个工具真实落库、facts/tainted 契约。

运行：cd backend && python -m unittest tests.test_tools -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from bankagent import bankcore, db, seed, specs, tools

MONEY_KEYS_WITHOUT_SUFFIX = {"balance_after", "daily_limit", "used", "min_amount", "budget"}
TEXT_CHECK_KEYS = {
    "name", "payee_name", "merchant", "product_name", "counterparty",
    "alias", "item", "goal", "content",
}
ALLOWED_FACT_KINDS = {"money", "id", "ratio", "term", "text"}


def _iter_money(data):
    """递归产出 (key, value)，覆盖所有 *_cents 与无后缀的金额键。"""
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                yield from _iter_money(value)
                continue
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and (
                key.endswith("_cents") or key in MONEY_KEYS_WITHOUT_SUFFIX
            ):
                yield key, value
    elif isinstance(data, (list, tuple)):
        for item in data:
            yield from _iter_money(item)


def _iter_values_by_key(data, keys):
    if isinstance(data, dict):
        for key, value in data.items():
            if key in keys and isinstance(value, str) and value:
                yield key, value
            yield from _iter_values_by_key(value, keys)
    elif isinstance(data, (list, tuple)):
        for item in data:
            yield from _iter_values_by_key(item, keys)


class ToolTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        db.reset_db(self.tmp.name)
        self.conn = db.connect(self.tmp.name)
        seed.seed(self.conn)

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass

    # ---------------- 通用断言 ----------------
    def invoke(self, tool_name: str, **args) -> dict:
        return tools.call(tool_name, self.conn, 1, args)

    def assertEnvelope(self, result: dict) -> None:
        self.assertIsInstance(result, dict)
        self.assertEqual(
            set(result), {"ok", "data", "facts", "tainted", "error"},
            f"信封字段不正确：{result}",
        )
        self.assertIsInstance(result["data"], dict)
        self.assertIsInstance(result["facts"], list)
        self.assertIsInstance(result["tainted"], list)
        if result["ok"]:
            self.assertIsNone(result["error"])
        else:
            self.assertIsInstance(result["error"], dict)
            self.assertIn("code", result["error"])
            self.assertIn("message", result["error"])
            self.assertEqual(result["data"], {})
            self.assertEqual(result["facts"], [])

    def assertOk(self, result: dict, label: str = "") -> dict:
        self.assertEnvelope(result)
        self.assertTrue(result["ok"], f"{label} 应成功，实际：{result}")
        return result

    def assertFactCoverage(self, result: dict, label: str = "") -> None:
        """data 中每个金额/单号/人名都必须有对应 fact。"""
        self.assertEnvelope(result)
        self.assertTrue(result["ok"], label)
        facts = result["facts"]
        ids = [f["id"] for f in facts]
        self.assertEqual(len(ids), len(set(ids)), f"{label} facts.id 重复")
        for fact in facts:
            self.assertIn("id", fact)
            self.assertIn("key", fact)
            self.assertIn("kind", fact)
            self.assertIn("value", fact)
            self.assertIn(fact["kind"], ALLOWED_FACT_KINDS)

        # 1) 所有金额（含 *_cents 与 balance_after 等）
        for key, value in _iter_money(result["data"]):
            expected_key = key[: -len("_cents")] if key.endswith("_cents") else key
            self.assertTrue(
                any(
                    f["kind"] == "money"
                    and f["value"] == value
                    and f["key"] == expected_key
                    for f in facts
                ),
                f"{label} 金额 {key}={value} 缺少 money fact（期望 key={expected_key}）",
            )

        # 2) 每个 txn_no 一条 id fact
        for _, txn_no in _iter_values_by_key(result["data"], {"txn_no"}):
            self.assertTrue(
                any(f["kind"] == "id" and f["value"] == txn_no for f in facts),
                f"{label} 单号 {txn_no} 缺少 id fact",
            )

        # 3) 人名/商户名/品名 text fact
        for key, text in _iter_values_by_key(result["data"], TEXT_CHECK_KEYS):
            self.assertTrue(
                any(f["kind"] == "text" and f["value"] == text for f in facts),
                f"{label} 文本 {key}={text!r} 缺少 text fact",
            )


# ---------------------------------------------------------------------------
# 注册表 & 统一入口
# ---------------------------------------------------------------------------
class TestRegistry(ToolTestBase):
    def test_registry_covers_all_specs_exactly(self):
        self.assertEqual(set(tools.REGISTRY), set(specs.TOOL_NAMES))
        self.assertEqual(len(tools.REGISTRY), len(specs.TOOL_NAMES))
        for name in specs.TOOL_NAMES:
            self.assertTrue(callable(tools.REGISTRY[name]), name)

    def test_unknown_tool_returns_e_tool(self):
        result = tools.call("no_such_tool", self.conn, 1, {})
        self.assertEnvelope(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "E_TOOL")

    def test_missing_required_param_is_e_param(self):
        result = self.invoke("create_transfer", contact_id=1)  # 缺 amount
        self.assertEnvelope(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "E_PARAM")

    def test_bad_contact_and_card_errors(self):
        quote = self.invoke("quote_transfer", contact_id=999, amount=100)
        self.assertFalse(quote["ok"])
        self.assertEqual(quote["error"]["code"], "E_CONTACT_NOT_FOUND")

        adjust = self.invoke("adjust_card_limit", card_id=999, new_limit=100_000)
        self.assertFalse(adjust["ok"])
        self.assertEqual(adjust["error"]["code"], "E_NOT_FOUND")

    def test_all_26_tools_call_through_call(self):
        seen: list[str] = []

        def run(name: str, args: dict) -> dict:
            result = tools.call(name, self.conn, 1, dict(args))
            self.assertOk(result, name)
            self.assertFactCoverage(result, name)
            seen.append(name)
            return result

        # ---- 绿 ----
        accounts = run("get_accounts", {})
        self.assertIn("accounts", accounts["data"])
        self.assertIn("cards", accounts["data"])

        run("get_transactions", {"range": "all", "limit": 5})
        run("analyze_spending", {"range": "last_30d"})
        run("detect_anomaly", {"range": "last_30d"})
        run("get_bill_report", {})
        run("get_contacts", {"alias": "妈"})
        run("get_subscriptions", {})
        run("get_wealth_products", {})
        run("get_profile", {})
        run("quote_transfer", {"contact_id": 1, "amount": 10_000})

        # ---- 黄 ----
        run("create_transfer", {
            "contact_id": 1, "amount": 10_000, "memo": "测试转账",
            "_idem_key": "smoke-create-transfer",
        })
        run("cancel_subscription", {"sub_id": 1})
        run("apply_virtual_card", {"card_type": "virtual"})
        run("create_collection_request", {
            "payer_contact_ids": [2, 3], "amount_each": 5_000,
        })
        run("set_reminder", {
            "at": "2026-11-01T09:00:00+08:00", "content": "给妈妈打电话",
        })
        run("create_standing_plan", {
            "goal": "妈妈生日",
            "trigger": {"at": "2026-11-18T09:00:00+08:00"},
            "actions": [{"tool": "order_gift", "args": {"item": "鲜花", "amount": 20_000}}],
            "budget": 50_000,
        })
        frozen = run("freeze_amount", {"amount": 20_000})
        run("order_gift", {"item": "蛋糕", "amount": 5_000})
        run("release_freeze", {"freeze_id": frozen["data"]["freeze_id"]})

        # ---- 红 ----
        run("submit_transfer", {
            "contact_id": 2, "amount": 20_000, "memo": "测试大额",
            "_idem_key": "smoke-submit-transfer",
        })
        run("report_card_lost", {"card_id": 1})
        run("unlock_card", {"card_id": 1})
        run("purchase_wealth", {"product_id": 5, "amount": 10_000})
        run("redeem_wealth", {"holding_id": 1, "amount": 100_000})
        run("adjust_card_limit", {"card_id": 1, "new_limit": 3_000_000})

        # ---- 办理闭环 ----
        run("repay_loan", {"amount": 10_000})
        run("repay_credit_card", {"amount": 10_000})
        run("reverse_transfer", {"txn_id": 1})
        run("risk_assessment", {"level": "稳健型"})
        run("request_password_reset", {"channel": "sms"})

        # ---- 系统 ----
        run("notify", {"content": "系统测试通知"})

        self.assertEqual(len(seen), len(specs.TOOL_NAMES), f"实际调用 {len(seen)} 个工具")
        self.assertEqual(set(seen), set(specs.TOOL_NAMES))


# ---------------------------------------------------------------------------
# 绿 · 只读工具
# ---------------------------------------------------------------------------
class TestReadTools(ToolTestBase):
    def test_get_accounts_shape(self):
        result = self.assertOk(self.invoke("get_accounts"))
        self.assertFactCoverage(result)
        data = result["data"]
        self.assertEqual(data["main_account_id"], 1)
        self.assertEqual(len(data["accounts"]), 3)
        self.assertEqual(data["accounts"][0]["balance_cents"], seed.TARGET_BALANCE)
        self.assertGreater(data["accounts"][0]["available_cents"], 0)
        self.assertEqual(len(data["cards"]), 1)
        self.assertEqual(data["cards"][0]["card_id"], 1)
        self.assertEqual(data["cards"][0]["daily_limit_cents"], 2_000_000)

    def test_get_transactions_and_category_filter(self):
        result = self.assertOk(self.invoke("get_transactions", range="all", limit=200))
        self.assertFactCoverage(result)
        self.assertGreater(result["data"]["count"], 100)
        self.assertTrue(all("txn_no" in item for item in result["data"]["items"]))

        food = self.assertOk(self.invoke(
            "get_transactions", range="all", category="餐饮", limit=200
        ))
        self.assertFactCoverage(food)
        self.assertGreater(food["data"]["count"], 0)
        self.assertTrue(all(i["category"] == "餐饮" for i in food["data"]["items"]))

    def test_analyze_spending_shape_and_ratio(self):
        result = self.assertOk(self.invoke("analyze_spending", range="last_30d"))
        self.assertFactCoverage(result)
        data = result["data"]
        self.assertGreater(data["total_cents"], 0)
        self.assertTrue(data["range_label"])
        self.assertEqual(
            sum(i["amount_cents"] for i in data["by_category"]),
            data["total_cents"],
        )
        for item in data["by_category"]:
            self.assertTrue(0 <= item["ratio"] <= 1)
            self.assertEqual(
                set(item), {"category", "amount_cents", "ratio"}
            )
        self.assertAlmostEqual(
            sum(i["ratio"] for i in data["by_category"]), 1.0, places=3
        )
        self.assertGreaterEqual(data["prev_total_cents"], 0)
        self.assertTrue(data["mom_ratio"] is None or isinstance(data["mom_ratio"], float))
        self.assertEqual(
            data["mom_change_cents"],
            abs(data["total_cents"] - data["prev_total_cents"]),
        )

    def test_detect_anomaly_three_rules(self):
        result = self.assertOk(self.invoke("detect_anomaly", range="last_30d"))
        self.assertFactCoverage(result)
        data = result["data"]
        self.assertGreaterEqual(data["count"], 2)
        rules = {item["rule"] for item in data["items"]}
        self.assertIn("night_large", rules)
        self.assertIn("same_merchant_1h", rules)
        self.assertEqual(
            set(data["rules"]), {"night_large", "same_merchant_1h", "merchant_3x_avg"}
        )

        night = [i for i in data["items"] if i["rule"] == "night_large"]
        self.assertTrue(any(i["counterparty"] == "某电商" for i in night))
        for item in data["items"]:
            self.assertEqual(
                set(item),
                {"txn_no", "counterparty", "amount_cents", "created_at", "rule", "reason"},
            )
            self.assertGreater(item["amount_cents"], 0)
            self.assertIn(item["rule"], data["rules"])
            self.assertTrue(item["reason"])

    def test_get_bill_report_summary_and_chart(self):
        result = self.assertOk(self.invoke("get_bill_report"))
        self.assertFactCoverage(result)
        data = result["data"]
        self.assertEqual(data["income_cents"], tools.MONTHLY_INCOME_CENTS)
        self.assertEqual(
            sum(i["amount_cents"] for i in data["by_category"]), data["total_cents"]
        )
        self.assertEqual(data["savings_cents"], data["income_cents"] - data["total_cents"])
        self.assertIsInstance(data["savings_rate"], float)
        self.assertEqual(data["saving_ratio"], data["savings_rate"])
        self.assertGreater(len(data["chart_points"]), 0)
        for point in data["chart_points"]:
            self.assertEqual(set(point), {"date", "amount_cents"})
            self.assertGreater(point["amount_cents"], 0)

    def test_get_contacts_alias_name_and_empty(self):
        alias = self.assertOk(self.invoke("get_contacts", alias="妈"))
        self.assertFactCoverage(alias)
        self.assertEqual(alias["data"]["count"], 1)
        self.assertEqual(alias["data"]["items"][0]["name"], "王秀兰")

        by_name = self.assertOk(self.invoke("get_contacts", name="李娜"))
        self.assertEqual(by_name["data"]["items"][0]["alias"], "老婆")

        everything = self.assertOk(self.invoke("get_contacts"))
        self.assertEqual(everything["data"]["count"], 4)

        missing = self.assertOk(self.invoke("get_contacts", alias="查无此人"))
        self.assertEqual(missing["data"]["items"], [])
        self.assertEqual(missing["data"]["count"], 0)

    def test_get_subscriptions(self):
        result = self.assertOk(self.invoke("get_subscriptions"))
        self.assertFactCoverage(result)
        data = result["data"]
        self.assertEqual(data["count"], 5)
        self.assertEqual(data["active_count"], 5)
        self.assertEqual(
            data["active_monthly_cents"],
            sum(i["amount_cents"] for i in data["items"]),
        )
        self.assertGreaterEqual(data["unused_count"], 3)
        for item in data["items"]:
            self.assertIn("merchant", item)
            self.assertIn("amount_cents", item)

    def test_get_wealth_products(self):
        result = self.assertOk(self.invoke("get_wealth_products"))
        self.assertFactCoverage(result)
        self.assertEqual(result["data"]["count"], 6)
        for item in result["data"]["items"]:
            self.assertIn("min_amount_cents", item)
            self.assertGreater(item["min_amount_cents"], 0)
            self.assertIn(item["risk_level"], {"R1", "R2", "R3"})

        r1 = self.assertOk(self.invoke("get_wealth_products", risk_level="R1"))
        self.assertEqual(r1["data"]["count"], 2)
        self.assertTrue(all(i["risk_level"] == "R1" for i in r1["data"]["items"]))

    def test_get_profile(self):
        result = self.assertOk(self.invoke("get_profile"))
        self.assertFactCoverage(result)
        data = result["data"]
        self.assertEqual(data["name"], "陈晓")
        self.assertEqual(data["risk_level"], "normal")
        self.assertEqual(data["monthly_income_cents"], 1_800_000)
        self.assertEqual(data["birthday"], "1990-11-18")

    def test_quote_transfer_fields(self):
        result = self.assertOk(self.invoke(
            "quote_transfer", contact_id=1, amount=50_000
        ))
        self.assertFactCoverage(result)
        data = result["data"]
        self.assertEqual(data["payee_name"], "王秀兰")
        self.assertEqual(data["fee_cents"], 0)
        self.assertEqual(data["arrival"], "实时")
        self.assertFalse(data["payee_is_new"])
        self.assertEqual(
            data["balance_before_cents"] - data["amount_cents"],
            data["balance_after_cents"],
        )
        self.assertTrue(data["sufficient"])

        new_payee = self.assertOk(self.invoke(
            "quote_transfer", contact_id=4, amount=10_000
        ))
        self.assertTrue(new_payee["data"]["payee_is_new"])
        self.assertEqual(new_payee["data"]["payee_name"], "张三")


# ---------------------------------------------------------------------------
# 黄/红 · 写操作真实落库
# ---------------------------------------------------------------------------
class TestWriteTools(ToolTestBase):
    def test_create_transfer_persists_idempotent_and_taints_memo(self):
        before = bankcore.get_balance(self.conn, 1)
        args = {
            "contact_id": 1, "amount": 12_300, "memo": "给妈妈的生活费",
            "_idem_key": "test-transfer-1",
        }
        first = self.assertOk(self.invoke("create_transfer", **args))
        self.assertFactCoverage(first)
        self.assertIn("memo", first["tainted"])
        self.assertIn("payee_name", first["tainted"])

        second = self.assertOk(self.invoke("create_transfer", **args))
        self.assertEqual(first["data"]["txn_no"], second["data"]["txn_no"])
        self.assertEqual(bankcore.get_balance(self.conn, 1), before - 12_300)

        row = self.conn.execute(
            "SELECT * FROM transactions WHERE txn_no=?", (first["data"]["txn_no"],)
        ).fetchone()
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["amount"], 12_300)
        self.assertEqual(row["memo"], "给妈妈的生活费")
        self.assertEqual(row["type"], "transfer")
        count = self.conn.execute(
            "SELECT COUNT(*) c FROM transactions WHERE idempotency_key='test-transfer-1'"
        ).fetchone()["c"]
        self.assertEqual(count, 1)
        self.assertEqual(
            bankcore.get_daily_used(self.conn, 1), seed.TODAY_TRANSFER + 12_300
        )

    def test_submit_transfer_persists(self):
        before = bankcore.get_balance(self.conn, 1)
        result = self.assertOk(self.invoke("submit_transfer", **{
            "contact_id": 2, "amount": 30_000, "memo": "大额转账",
            "_idem_key": "test-submit-1",
        }))
        self.assertFactCoverage(result)
        self.assertEqual(result["data"]["balance_after"], before - 30_000)
        row = self.conn.execute(
            "SELECT * FROM transactions WHERE txn_no=?", (result["data"]["txn_no"],)
        ).fetchone()
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["amount"], 30_000)

    def test_cancel_subscription_persists_status(self):
        result = self.assertOk(self.invoke("cancel_subscription", sub_id=1))
        self.assertFactCoverage(result)
        self.assertIn("merchant", result["tainted"])
        row = self.conn.execute("SELECT status FROM subscriptions WHERE id=1").fetchone()
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(result["data"]["status"], "cancelled")
        self.assertEqual(result["data"]["merchant"], "视频会员A")

    def test_apply_virtual_card_inserts_row(self):
        before = self.conn.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]
        result = self.assertOk(self.invoke("apply_virtual_card", card_type="virtual"))
        self.assertFactCoverage(result)
        after = self.conn.execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]
        self.assertEqual(after, before + 1)
        card_id = result["data"]["card_id"]
        row = self.conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["daily_limit"], 500_000)
        self.assertEqual(row["user_id"], 1)

    def test_collection_request_inserts_rows(self):
        result = self.assertOk(self.invoke(
            "create_collection_request",
            payer_contact_ids=[2, 3], amount_each=7_000,
        ))
        self.assertFactCoverage(result)
        self.assertEqual(result["data"]["count"], 2)
        self.assertEqual(result["data"]["total_cents"], 14_000)
        rows = self.conn.execute(
            "SELECT * FROM collection_requests ORDER BY id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["payer_contact_id"] for r in rows}, {2, 3})
        self.assertTrue(all(r["amount"] == 7_000 for r in rows))
        self.assertTrue(all(r["status"] == "pending" for r in rows))

    def test_set_reminder_inserts_row(self):
        result = self.assertOk(self.invoke(
            "set_reminder", at="2026-11-01T09:00:00+08:00", content="给妈妈打电话",
        ))
        self.assertFactCoverage(result)
        self.assertIn("content", result["tainted"])
        row = self.conn.execute(
            "SELECT * FROM reminders WHERE id=?", (result["data"]["reminder_id"],)
        ).fetchone()
        self.assertEqual(row["content"], "给妈妈打电话")
        self.assertEqual(row["status"], "active")

    def test_create_standing_plan_inserts_row(self):
        result = self.assertOk(self.invoke("create_standing_plan", **{
            "goal": "妈妈生日",
            "trigger": {"at": "2026-11-18T09:00:00+08:00"},
            "actions": [{"tool": "order_gift", "args": {"item": "鲜花"}}],
            "budget": 50_000,
        }))
        self.assertFactCoverage(result)
        self.assertIn("goal", result["tainted"])
        self.assertIn("actions", result["tainted"])
        row = self.conn.execute(
            "SELECT * FROM standing_plans WHERE id=?", (result["data"]["plan_id"],)
        ).fetchone()
        self.assertEqual(row["goal"], "妈妈生日")
        self.assertEqual(row["budget"], 50_000)
        self.assertEqual(row["status"], "active")
        self.assertEqual(json.loads(row["trigger_rule"])["at"], "2026-11-18T09:00:00+08:00")
        self.assertEqual(json.loads(row["actions"])[0]["tool"], "order_gift")

    def test_freeze_defaults_main_account_and_release_restores(self):
        account_id = bankcore.main_account(self.conn, 1)
        before = bankcore.available(self.conn, account_id)
        result = self.assertOk(self.invoke("freeze_amount", amount=30_000))
        self.assertFactCoverage(result)
        self.assertEqual(result["data"]["account_id"], account_id)
        self.assertEqual(result["data"]["available_cents"], before - 30_000)
        self.assertEqual(bankcore.available(self.conn, account_id), before - 30_000)
        row = self.conn.execute(
            "SELECT * FROM freezes WHERE id=?", (result["data"]["freeze_id"],)
        ).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["account_id"], account_id)
        self.assertEqual(row["amount"], 30_000)

        released = self.assertOk(self.invoke(
            "release_freeze", freeze_id=result["data"]["freeze_id"]
        ))
        self.assertFactCoverage(released)
        self.assertEqual(released["data"]["released_cents"], 30_000)
        self.assertEqual(bankcore.available(self.conn, account_id), before)
        status = self.conn.execute(
            "SELECT status FROM freezes WHERE id=?", (result["data"]["freeze_id"],)
        ).fetchone()["status"]
        self.assertEqual(status, "released")

    def test_order_gift_inserts_order_and_txn(self):
        orders_before = self.conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
        result = self.assertOk(self.invoke(
            "order_gift", item="鲜花", amount=5_000, deliver_at="2026-11-18T10:00:00+08:00",
        ))
        self.assertFactCoverage(result)
        self.assertIn("item", result["tainted"])
        self.assertFalse(result["data"]["reversible"])
        orders_after = self.conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
        self.assertEqual(orders_after, orders_before + 1)

        order = self.conn.execute(
            "SELECT * FROM orders WHERE id=?", (result["data"]["order_id"],)
        ).fetchone()
        self.assertEqual(order["item"], "鲜花")
        self.assertEqual(order["amount"], 5_000)
        self.assertEqual(order["status"], "placed")

        txn = self.conn.execute(
            "SELECT * FROM transactions WHERE id=?", (result["data"]["txn_id"],)
        ).fetchone()
        self.assertEqual(txn["type"], "order")
        self.assertEqual(txn["amount"], 5_000)
        self.assertEqual(txn["status"], "succeeded")
        self.assertEqual(txn["txn_no"], result["data"]["txn_no"])

    def test_card_lost_then_unlock(self):
        lost = self.assertOk(self.invoke("report_card_lost", card_id=1))
        self.assertFactCoverage(lost)
        row = self.conn.execute("SELECT * FROM cards WHERE id=1").fetchone()
        self.assertEqual(row["status"], "lost")
        self.assertEqual(row["frozen"], 1)
        self.assertTrue(lost["data"]["ticket_no"].startswith("WO"))

        unlocked = self.assertOk(self.invoke("unlock_card", card_id=1))
        self.assertFactCoverage(unlocked)
        row = self.conn.execute("SELECT * FROM cards WHERE id=1").fetchone()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["frozen"], 0)

    def test_purchase_and_redeem_wealth(self):
        before_balance = bankcore.get_balance(self.conn, 1)
        before_holdings = self.conn.execute("SELECT COUNT(*) c FROM holdings").fetchone()["c"]
        bought = self.assertOk(self.invoke(
            "purchase_wealth", product_id=5, amount=10_000
        ))
        self.assertFactCoverage(bought)
        self.assertEqual(bought["data"]["balance_after"], before_balance - 10_000)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM holdings").fetchone()["c"],
            before_holdings + 1,
        )
        holding = self.conn.execute(
            "SELECT * FROM holdings WHERE id=?", (bought["data"]["holding_id"],)
        ).fetchone()
        self.assertEqual(holding["amount"], 10_000)
        self.assertEqual(holding["product_id"], 5)

        txn = self.conn.execute(
            "SELECT * FROM transactions WHERE id=?", (bought["data"]["txn_id"],)
        ).fetchone()
        self.assertEqual(txn["type"], "purchase")
        self.assertEqual(txn["txn_no"], bought["data"]["txn_no"])

        before_redeem = bankcore.get_balance(self.conn, 1)
        redeemed = self.assertOk(self.invoke(
            "redeem_wealth", holding_id=1, amount=100_000
        ))
        self.assertFactCoverage(redeemed)
        self.assertEqual(redeemed["data"]["balance_after"], before_redeem + 100_000)
        remain = self.conn.execute(
            "SELECT amount FROM holdings WHERE id=1"
        ).fetchone()["amount"]
        self.assertEqual(remain, 400_000)
        redeem_txn = self.conn.execute(
            "SELECT * FROM transactions WHERE id=?", (redeemed["data"]["txn_id"],)
        ).fetchone()
        self.assertEqual(redeem_txn["type"], "redeem")

    def test_adjust_card_limit_direction_and_persist(self):
        up = self.assertOk(self.invoke(
            "adjust_card_limit", card_id=1, new_limit=3_000_000
        ))
        self.assertFactCoverage(up)
        self.assertEqual(up["data"]["direction"], "up")
        self.assertEqual(up["data"]["old_limit_cents"], 2_000_000)
        self.assertEqual(up["data"]["new_limit_cents"], 3_000_000)
        self.assertEqual(
            self.conn.execute("SELECT daily_limit FROM cards WHERE id=1").fetchone()["daily_limit"],
            3_000_000,
        )

        down = self.assertOk(self.invoke(
            "adjust_card_limit", card_id=1, new_limit=1_500_000
        ))
        self.assertFactCoverage(down)
        self.assertEqual(down["data"]["direction"], "down")
        self.assertEqual(
            self.conn.execute("SELECT daily_limit FROM cards WHERE id=1").fetchone()["daily_limit"],
            1_500_000,
        )

    def test_notify(self):
        result = self.assertOk(self.invoke("notify", content="您的账单已生成"))
        self.assertFactCoverage(result)
        self.assertTrue(result["data"]["delivered"])
        self.assertEqual(result["data"]["to"], 1)
        self.assertIn("content", result["tainted"])


# ---------------------------------------------------------------------------
# facts / tainted 专项
# ---------------------------------------------------------------------------
class TestFactsAndTainted(ToolTestBase):
    def test_tainted_contains_free_text_fields(self):
        transfer = self.assertOk(self.invoke("create_transfer", **{
            "contact_id": 1, "amount": 10_000, "memo": "生活费",
            "_idem_key": "taint-transfer",
        }))
        self.assertIn("memo", transfer["tainted"])
        self.assertIn("payee_name", transfer["tainted"])

        transactions = self.assertOk(self.invoke(
            "get_transactions", range="this_month", limit=20
        ))
        self.assertIn("memo", transactions["tainted"])
        self.assertIn("counterparty", transactions["tainted"])

        contacts = self.assertOk(self.invoke("get_contacts"))
        self.assertIn("name", contacts["tainted"])
        self.assertIn("alias", contacts["tainted"])

        subs = self.assertOk(self.invoke("get_subscriptions"))
        self.assertIn("merchant", subs["tainted"])

    def test_money_facts_cover_nested_structures(self):
        accounts = self.assertOk(self.invoke("get_accounts"))
        self.assertFactCoverage(accounts)
        account = accounts["data"]["accounts"][0]
        for key in ("balance_cents", "frozen_cents", "available_cents"):
            self.assertTrue(
                any(
                    f["kind"] == "money" and f["value"] == account[key]
                    for f in accounts["facts"]
                ),
                key,
            )

        bill = self.assertOk(self.invoke("get_bill_report"))
        self.assertFactCoverage(bill)
        for key in ("income_cents", "total_cents", "savings_cents"):
            self.assertTrue(
                any(f["kind"] == "money" and f["value"] == bill["data"][key]
                    for f in bill["facts"]),
                key,
            )


if __name__ == "__main__":
    unittest.main()
