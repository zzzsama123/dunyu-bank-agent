"""办理闭环测试：还款、风险测评、密码重置（只发链接）。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import bankcore, db, orchestrator, seed


class TestServiceFlow(unittest.TestCase):
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

    def test_risk_assessment_confirm(self):
        reply = orchestrator.handle_chat(self.conn, "s_svc", "做风险测评")
        self.assertEqual(reply["status"], "awaiting_confirm")
        card = reply["pending"]["card"]
        done = orchestrator.handle_confirm(self.conn, "s_svc", card["intent_id"], card["token"])
        self.assertIn("风险测评已完成", done["reply"])
        row = self.conn.execute("SELECT level, score FROM risk_profiles WHERE user_id=1").fetchone()
        self.assertEqual(row["level"], "稳健型")
        self.assertEqual(int(row["score"]), 55)

    def test_repay_loan_stepup(self):
        before = int(self.conn.execute("SELECT remaining FROM loans WHERE user_id=1").fetchone()[0])
        reply = orchestrator.handle_chat(self.conn, "s_svc2", "我要还房贷")
        self.assertEqual(reply["status"], "awaiting_confirm")
        card = reply["pending"]["card"]
        step = orchestrator.handle_confirm(self.conn, "s_svc2", card["intent_id"], card["token"])
        self.assertEqual(step["status"], "awaiting_stepup")
        done = orchestrator.handle_stepup(
            self.conn, "s_svc2", step["pending"]["challenge_id"], "123456")
        self.assertIn("已归还贷款", done["reply"])
        after = int(self.conn.execute("SELECT remaining FROM loans WHERE user_id=1").fetchone()[0])
        self.assertEqual(before - after, 780000)

    def test_password_reset_only_sends_link(self):
        reply = orchestrator.handle_chat(self.conn, "s_svc3", "重置密码")
        self.assertEqual(reply["status"], "awaiting_stepup")
        done = orchestrator.handle_stepup(
            self.conn, "s_svc3", reply["pending"]["challenge_id"], "123456")
        self.assertIn("一次性链接", done["reply"])
        self.assertIn("不会收集新密码", done["reply"])
        row = self.conn.execute("SELECT reset_no, status FROM password_resets").fetchone()
        self.assertTrue(row["reset_no"].startswith("R"))
        self.assertEqual(row["status"], "pending")


if __name__ == "__main__":
    unittest.main()
