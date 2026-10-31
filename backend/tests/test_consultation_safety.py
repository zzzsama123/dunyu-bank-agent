"""咨询/否定/假回执回归：问句不能触发扣款，失败不能报成功。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed


class TestConsultationSafety(unittest.TestCase):
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

    def test_prepay_question_does_not_execute(self):
        res = orchestrator.handle_chat(self.conn, "s_c", "提前还款有违约金吗")
        self.assertNotEqual(res["status"], "awaiting_stepup")
        self.assertIsNone(res.get("pending"))
        self.assertIn("违约金", res["reply"])

    def test_negation_cancels(self):
        res = orchestrator.handle_chat(self.conn, "s_n", "我不想还房贷")
        self.assertIn("不执行", res["reply"])
        self.assertIsNone(res.get("pending"))

    def test_redeem_without_holding_asks(self):
        res = orchestrator.handle_chat(self.conn, "s_r", "赎回理财")
        self.assertIn("哪一笔", res["reply"])
        self.assertNotIn("操作已完成", res["reply"])

    def test_loan_rate_not_fake_receipt(self):
        res = orchestrator.handle_chat(self.conn, "s_l", "房贷利率是多少")
        self.assertIn("3.45", res["reply"])
        self.assertNotIn("操作已完成", res["reply"])


if __name__ == "__main__":
    unittest.main()
