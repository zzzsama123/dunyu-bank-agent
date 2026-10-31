"""V3 回归：复合句不能走计算器、两件事都要执行、条件式要展示账单。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed


class TestCompoundV3(unittest.TestCase):
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

    def test_repay_then_balance(self):
        first = orchestrator.handle_chat(self.conn, "s_v3", "帮我还房贷，然后查一下余额")
        self.assertEqual(first["status"], "awaiting_confirm")
        card = first["pending"]["card"]
        step = orchestrator.handle_confirm(self.conn, "s_v3", card["intent_id"], card["token"])
        self.assertEqual(step["status"], "awaiting_stepup")
        done = orchestrator.handle_stepup(
            self.conn, "s_v3", step["pending"]["challenge_id"], "123456")
        self.assertIn("已归还贷款", done["reply"])
        self.assertIn("接着办了第二件", done["reply"])
        self.assertIn("可用余额", done["reply"])

    def test_balance_then_cancel_subscription(self):
        res = orchestrator.handle_chat(self.conn, "s_v3b", "先查余额，再把没用的会员退了")
        self.assertIn("当前可用余额", res["reply"])
        self.assertIn("接着办了第二件", res["reply"])

    def test_repay_phrase_not_loan_calculator(self):
        res = orchestrator.handle_chat(self.conn, "s_v3c", "还房贷")
        self.assertEqual(res["status"], "awaiting_confirm")
        self.assertNotIn("等额本息", res["reply"])


if __name__ == "__main__":
    unittest.main()
