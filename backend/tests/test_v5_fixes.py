"""V5 回归：冲正、手机号、AA 追问、额度提示、知识新建、风险句号。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import config, db, knowledge, orchestrator, seed


class TestV5Fixes(unittest.TestCase):
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

    def test_reverse_transfer(self):
        first = orchestrator.handle_chat(self.conn, "s_rev", "转50给张三")
        card = first["pending"]["card"]
        orchestrator.handle_confirm(self.conn, "s_rev", card["intent_id"], card["token"])
        rev = orchestrator.handle_chat(self.conn, "s_rev", "取消刚才那笔转账")
        self.assertEqual(rev["status"], "awaiting_confirm")
        card = rev["pending"]["card"]
        step = orchestrator.handle_confirm(self.conn, "s_rev", card["intent_id"], card["token"])
        done = orchestrator.handle_stepup(
            self.conn, "s_rev", step["pending"]["challenge_id"], "123456")
        self.assertIn("已撤销原转账", done["reply"])
        self.assertIn("50.00", done["reply"])
        row = self.conn.execute(
            "SELECT status, reversed_by FROM transactions WHERE type='transfer' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["status"], "reversed")
        self.assertIsNotNone(row["reversed_by"])

    def test_phone_payee(self):
        res = orchestrator.handle_chat(self.conn, "s_phone", "转50给13800138000")
        self.assertEqual(res["status"], "awaiting_confirm")
        self.assertIn("张三", res["reply"])

    def test_aa_without_payers_asks(self):
        res = orchestrator.handle_chat(self.conn, "s_aa", "AA收款200，四个人")
        self.assertIn("向谁收", res["reply"])
        self.assertIsNone(res.get("pending"))

    def test_exhausted_quota_no_negative_tip(self):
        self.conn.execute(
            "INSERT INTO daily_quota(user_id,biz_date,used) VALUES(1,?,170000) "
            "ON CONFLICT(user_id,biz_date) DO UPDATE SET used=170000", (config.today_str(),))
        res = orchestrator.handle_chat(self.conn, "s_quota", "转50给张三")
        self.assertNotIn("-700", res["reply"])
        self.assertIn("已用完", res["reply"])

    def test_risk_note_no_double_period(self):
        self.conn.execute(
            "INSERT INTO daily_quota(user_id,biz_date,used) VALUES(1,?,170000) "
            "ON CONFLICT(user_id,biz_date) DO UPDATE SET used=170000", (config.today_str(),))
        res = orchestrator.handle_chat(self.conn, "s_punct", "转1800给张三")
        self.assertNotIn("。。", res["reply"])

    def test_knowledge_create(self):
        res = knowledge.draft(self.conn, "", "新内容", "", "运营", "", "", "新条目", ["新"])
        self.assertTrue(res.get("created"))
        row = self.conn.execute(
            "SELECT status FROM knowledge_versions WHERE entry_id=?", (res["entry_id"],)).fetchone()
        self.assertEqual(row["status"], "draft")

    def test_proposal_budget_followup(self):
        orchestrator.handle_chat(self.conn, "s_budget", "我老婆下个月生日，帮我准备一下，别超过1000")
        revised = orchestrator.handle_chat(self.conn, "s_budget", "2000元呢")
        self.assertIn("预算改为 2,000.00", revised["reply"])
        self.assertNotIn("没太听清", revised["reply"])
        confirm = orchestrator.handle_chat(self.conn, "s_budget", "按这个来")
        self.assertEqual(confirm["status"], "awaiting_confirm")
        self.assertEqual(confirm["pending"]["card"]["amount"], "2,000.00")


if __name__ == "__main__":
    unittest.main()
