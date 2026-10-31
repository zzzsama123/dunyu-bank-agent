"""账实一致性：贷款进度、可用余额、订阅过滤、知识库新建。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import bankcore, config, db, knowledge, orchestrator, seed


class TestLedgerConsistency(unittest.TestCase):
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

    def test_loan_progress_reads_db_after_repay(self):
        before = orchestrator.handle_chat(self.conn, "s_loan", "我的贷款进度")
        self.assertIn("420,000.00", before["reply"])
        pending = orchestrator.handle_chat(self.conn, "s_loan", "还房贷")
        card = pending["pending"]["card"]
        step = orchestrator.handle_confirm(self.conn, "s_loan", card["intent_id"], card["token"])
        orchestrator.handle_stepup(self.conn, "s_loan", step["pending"]["challenge_id"], "123456")
        after = orchestrator.handle_chat(self.conn, "s_loan", "我的贷款进度")
        self.assertIn("412,200.00", after["reply"])
        self.assertNotIn("420,000.00", after["reply"])

    def test_available_balance_shows_frozen(self):
        self.conn.execute(
            "INSERT INTO freezes(user_id,account_id,amount,status,until,created_at) "
            "VALUES(1,1,100000,'active','2026-12-01',?)", (config.now_iso(),))
        res = orchestrator.handle_chat(self.conn, "s_bal", "查余额")
        self.assertIn("当前可用余额 11,345.67", res["reply"])
        self.assertIn("总余额 12,345.67", res["reply"])
        self.assertIn("1,000.00 元已被计划锁定", res["reply"])

    def test_cancelled_subscription_not_counted_active(self):
        pending = orchestrator.handle_chat(self.conn, "s_sub", "退掉视频会员A")
        card = pending["pending"]["card"]
        orchestrator.handle_confirm(self.conn, "s_sub", card["intent_id"], card["token"])
        res = orchestrator.handle_chat(self.conn, "s_sub", "我都被扣了什么会员")
        self.assertIn("在用 4 项", res["reply"])
        self.assertIn("已取消 1 项", res["reply"])

    def test_knowledge_create_new_entry(self):
        res = knowledge.draft(
            self.conn, "", "新业务说明", "", "运营", "", "", "新业务", ["新业务"])
        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("created"))
        row = self.conn.execute(
            "SELECT title,status FROM knowledge_versions WHERE entry_id=?",
            (res["entry_id"],)).fetchone()
        self.assertEqual(row["title"], "新业务")
        self.assertEqual(row["status"], "draft")


if __name__ == "__main__":
    unittest.main()
