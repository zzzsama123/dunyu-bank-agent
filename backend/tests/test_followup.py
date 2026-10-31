"""上下文追问：查看详情 / 查看审计。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed


class TestFollowup(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        db.reset_db(self.path)
        self.conn = db.connect(self.path)
        seed.seed(self.conn)
        self.sid = "s_fu"

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def test_anomaly_detail(self):
        first = orchestrator.handle_chat(self.conn, self.sid, "帮我看看有没有乱扣费的")
        self.assertIn("查看详情", first["reply"])
        detail = orchestrator.handle_chat(self.conn, self.sid, "查看详情")
        self.assertIn("星耀直播", detail["reply"])
        self.assertIn("疑似", detail["reply"])

    def test_transactions_detail(self):
        orchestrator.handle_chat(self.conn, self.sid, "查一下我上周的流水")
        detail = orchestrator.handle_chat(self.conn, self.sid, "哪几笔")
        self.assertIn("明细", detail["reply"])

    def test_audit_link(self):
        orchestrator.handle_chat(self.conn, self.sid, "查到账流水")
        res = orchestrator.handle_chat(self.conn, self.sid, "查看审计")
        self.assertIn("/audit/", res["reply"])


    def test_income_filter(self):
        orchestrator.handle_chat(self.conn, self.sid, "最近流水")
        res = orchestrator.handle_chat(self.conn, self.sid, "收入呢")
        self.assertIn("收入", res["reply"])
        self.assertIn("1,100.00", res["reply"])

    def test_expense_filter(self):
        orchestrator.handle_chat(self.conn, self.sid, "最近流水")
        res = orchestrator.handle_chat(self.conn, self.sid, "只看支出")
        self.assertIn("支出", res["reply"])
        self.assertIn("合计", res["reply"])

    def test_filter_without_context(self):
        res = orchestrator.handle_chat(self.conn, "s_empty2", "收入呢")
        self.assertIn("最近的流水", res["reply"])

    def test_no_context(self):
        res = orchestrator.handle_chat(self.conn, "s_empty", "查看详情")
        self.assertIn("没有可展开", res["reply"])

    def test_show_all_transactions_not_subscription(self):
        first = orchestrator.handle_chat(self.conn, self.sid, "今年的流水")
        self.assertIn("全部展示", first["reply"])
        self.assertNotIn("审计页看全量", first["reply"])
        res = orchestrator.handle_chat(self.conn, self.sid, "全部展示")
        self.assertIn("共 158 笔", res["reply"])
        self.assertNotIn("订阅", res["reply"])
        self.assertGreaterEqual(len(res["reply"].splitlines()), 100)


if __name__ == "__main__":
    unittest.main()
