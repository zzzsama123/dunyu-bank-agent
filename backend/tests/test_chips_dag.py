"""T2/T4：候选 chips 结构化、条件计划含查询节点与 condition。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed


class TestChipsAndDag(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        db.reset_db(self.path)
        self.conn = db.connect(self.path)
        seed.seed(self.conn)

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def test_missing_payee_has_chips(self):
        res = orchestrator.handle_chat(self.conn, "s_chip", "转500给李四")
        self.assertIn("没找到", res["reply"])
        self.assertIn("张三", res["quick_replies"])

    def test_conditional_plan_contains_query_and_condition(self):
        res = orchestrator.handle_chat(
            self.conn, "s_dag", "查上个月账单，如果超1000就帮我转500给张三")
        self.assertEqual(res["status"], "awaiting_confirm")
        tools = [node["tool"] for node in res["plan"]["nodes"]]
        self.assertEqual(tools[0], "analyze_spending")
        self.assertIn("create_transfer", tools)
        condition = res["plan"]["nodes"][-1]["condition"]
        self.assertEqual(condition[0]["op"], ">")

    def test_conditional_else_branch(self):
        res = orchestrator.handle_chat(
            self.conn, "s_dag2", "如果账单超过100000就转500给张三，否则查余额")
        self.assertIn("条件不成立", res["reply"])
        self.assertIn("余额", res["reply"])
        self.assertIsNone(res.get("pending"))

    def test_bill_attribution(self):
        res = orchestrator.handle_chat(self.conn, "s_dag3", "我这个月钱都去哪了")
        self.assertIn("最大一笔", res["reply"])
        self.assertIn("变化最大", res["reply"])


if __name__ == "__main__":
    unittest.main()
