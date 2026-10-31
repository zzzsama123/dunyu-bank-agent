"""V3 收尾：短指代、赎回闭环、订阅指令、IM stepup 提示。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import bankcore, db, orchestrator, seed


class TestV3Remaining(unittest.TestCase):
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

    def test_short_last_month_followup(self):
        orchestrator.handle_chat(self.conn, "s_v3r", "我这个月账单")
        res = orchestrator.handle_chat(self.conn, "s_v3r", "那上个月呢")
        self.assertIn("2026年9月", res["reply"])

    def test_money_and_casual_phrases(self):
        self.assertIn("可用余额", orchestrator.handle_chat(self.conn, "s_v3r2", "我钱呢")["reply"])
        res = orchestrator.handle_chat(self.conn, "s_v3r2", "随便看看")
        self.assertIn("想看哪一类", res["reply"])

    def test_subscription_prompt_and_cancel_all(self):
        res = orchestrator.handle_chat(self.conn, "s_v3r3", "那个自动扣费的给我关了")
        self.assertIn("全关", res["reply"])
        batch = orchestrator.handle_chat(self.conn, "s_v3r3", "全关")
        self.assertEqual(batch["status"], "awaiting_stepup")

    def test_redeem_end_to_end(self):
        first = orchestrator.handle_chat(self.conn, "s_v3r4", "赎回第1笔")
        self.assertEqual(first["status"], "awaiting_confirm")
        card = first["pending"]["card"]
        step = orchestrator.handle_confirm(self.conn, "s_v3r4", card["intent_id"], card["token"])
        self.assertEqual(step["status"], "awaiting_stepup")
        done = orchestrator.handle_stepup(
            self.conn, "s_v3r4", step["pending"]["challenge_id"], "123456")
        self.assertIn("已赎回理财", done["reply"])
        self.assertEqual(int(bankcore.get_balance(self.conn, 1)), 1734567)

    def test_im_stepup_hint(self):
        orchestrator.handle_chat(self.conn, "s_im_v3", "我要还房贷")
        res = orchestrator.handle_chat(self.conn, "s_im_v3", "确认")
        self.assertEqual(res["status"], "awaiting_stepup")
        self.assertIn("6 位验证码", res["reply"])


if __name__ == "__main__":
    unittest.main()
