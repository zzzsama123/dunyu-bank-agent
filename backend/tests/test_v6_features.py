"""V6：定时转账、备注收款、年度账单、理财推荐/对比、卡限额、续费提醒。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed


class TestV6(unittest.TestCase):
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

    def test_scheduled_transfer_not_immediate(self):
        res = orchestrator.handle_chat(self.conn, "s_sched", "下个月1号给房东转2500")
        self.assertEqual(res["status"], "awaiting_confirm")
        card = res["pending"]["card"]
        self.assertEqual(card.get("action"), "schedule_transfer")
        before = self.conn.execute(
            "SELECT balance FROM v_account_balance WHERE account_id=1").fetchone()[0]
        done = orchestrator.handle_confirm(self.conn, "s_sched", card["intent_id"], card["token"])
        self.assertIn("已预约", done["reply"])
        self.assertIn("当前未扣款", done["reply"])
        after = self.conn.execute(
            "SELECT balance FROM v_account_balance WHERE account_id=1").fetchone()[0]
        self.assertEqual(before, after)
        row = self.conn.execute(
            "SELECT payee,amount,run_at,status FROM scheduled_transfers").fetchone()
        self.assertEqual(row["payee"], "房东")
        self.assertEqual(row["status"], "scheduled")

    def test_memo_payee(self):
        res = orchestrator.handle_chat(self.conn, "s_memo", "给上次备注房租的那个人转100")
        self.assertEqual(res["status"], "awaiting_confirm")
        self.assertIn("陈先生", res["reply"])

    def test_annual_bill(self):
        res = orchestrator.handle_chat(self.conn, "s_year", "2026年的年度账单")
        self.assertIn("2026年账单", res["reply"])
        self.assertIn("收入", res["reply"])

    def test_wealth_recommend_and_compare(self):
        rec = orchestrator.handle_chat(self.conn, "s_w", "推荐几个理财产品")
        self.assertIn("在售理财产品", rec["reply"])
        cmp = orchestrator.handle_chat(self.conn, "s_w", "对比一下稳盈90天和稳健180天")
        self.assertIn("稳盈 90 天", cmp["reply"])
        self.assertIn("稳健 180 天", cmp["reply"])
        self.assertNotIn("回执", cmp["reply"])

    def test_card_limit_variants(self):
        res = orchestrator.handle_chat(self.conn, "s_card", "把卡限额设成5000")
        self.assertEqual(res["status"], "awaiting_confirm")
        ask = orchestrator.handle_chat(self.conn, "s_card", "设置交易限制")
        self.assertIn("要设置多少额度", ask["reply"])

    def test_renewal_reminder(self):
        res = orchestrator.handle_chat(self.conn, "s_rem", "下次扣费前提醒我")
        self.assertIn("已设置", res["reply"])
        self.assertIn("扣费提醒", res["reply"])


if __name__ == "__main__":
    unittest.main()
