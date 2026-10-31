"""本地语言资源测试：口语改写、示例库兜底、中文日期/相对日期。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, lexicon, orchestrator, seed


class TestLexicon(unittest.TestCase):
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

    def test_canonicalize_colloquial(self):
        self.assertEqual(lexicon.canonicalize("十五号提醒我"), "15号提醒我")
        self.assertIn("提醒我", lexicon.canonicalize("到时候叫我"))
        self.assertIn("查", lexicon.canonicalize("帮我瞅瞅账单"))

    def test_match_bill_phrase(self):
        intent = lexicon.match("这个月花超了吗", [])
        self.assertIsNotNone(intent)
        self.assertEqual(intent["name"], "bill_analysis")

    def test_reminder_chinese_date(self):
        reply = orchestrator.handle_chat(self.conn, "s_lx", "十五号提醒我交房租")
        self.assertEqual(reply["status"], "done")
        self.assertIn("15 日 09:00", reply["reply"])
        self.assertIn("只提醒，不动资金", reply["reply"])

    def test_reminder_relative_week(self):
        reply = orchestrator.handle_chat(self.conn, "s_lx2", "下周三提醒我开会")
        self.assertEqual(reply["status"], "done")
        self.assertIn("09:00", reply["reply"])

    def test_reminder_without_date_asks(self):
        reply = orchestrator.handle_chat(self.conn, "s_lx3", "到时候提醒我")
        self.assertIn("哪天提醒你", reply["reply"])

    def test_colloquial_bill_phrase(self):
        reply = orchestrator.handle_chat(self.conn, "s_lx4", "这个月花超了吗")
        self.assertNotIn("我没完全听清", reply["reply"])
        self.assertIn("支出", reply["reply"])


if __name__ == "__main__":
    unittest.main()
