"""P0 补齐测试：知识库引用/过期、只读查询、主动提醒、转人工工单、运营指标。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, knowledge, metrics, orchestrator, proactive, seed, trust


class TestP0(unittest.TestCase):
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

    def test_knowledge_has_source_and_expiry(self):
        active = knowledge.answer("转账限额政策是什么")
        self.assertIn("来源：", active)
        self.assertIn("生效日期", active)
        expired = knowledge.answer("跨境汇款需要什么材料")
        self.assertIn("失效", expired)

    def test_readonly_queries(self):
        for text, expected in [
            ("我的房贷还款计划", "剩余本金"),
            ("信用卡账单多少", "本期账单"),
            ("我有多少积分", "积分"),
            ("附近网点排队", "排队"),
            ("今天汇率多少", "USD/CNY"),
        ]:
            self.assertIn(expected, orchestrator.handle_chat(self.conn, "s_p0", text)["reply"])

    def test_proactive_summary(self):
        data = proactive.build(self.conn, 1)
        self.assertGreaterEqual(data["total"], 1)
        self.assertTrue(any(item["type"] == "subscription" for item in data["items"]))

    def test_handoff_creates_ticket(self):
        reply = orchestrator.handle_chat(self.conn, "s_p0", "转人工")["reply"]
        self.assertIn("工单号", reply)
        rows = self.conn.execute("SELECT ticket_no FROM support_tickets").fetchall()
        self.assertEqual(len(rows), 1)

    def test_metrics_and_badcase(self):
        orchestrator.handle_chat(self.conn, 's_metrics', '你好')
        self.conn.execute(
            "INSERT INTO feedback(trace_id,session_id,user_id,rating,reason,created_at) "
            "VALUES('tr_x','s_p0',1,'down','测试',?)", ("2026-10-31T12:00:00+08:00",))
        data = metrics.build(self.conn)
        self.assertEqual(data["feedback_down"], 1)
        self.assertEqual(len(data["badcases"]), 1)
        self.assertGreaterEqual(data["sessions"], 1)


if __name__ == "__main__":
    unittest.main()
