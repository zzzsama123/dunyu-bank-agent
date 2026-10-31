"""拼音同音匹配 + 精确日期区间测试。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import config, db, phonetic, seed


class TestPhonetic(unittest.TestCase):
    def test_same_sound_names(self):
        self.assertTrue(phonetic.same_sound("李那", "李娜"))
        self.assertTrue(phonetic.contains_sound("给李那转100", "李娜"))
        self.assertFalse(phonetic.same_sound("张三", "李娜"))

    def test_key_is_pinyin(self):
        self.assertEqual(phonetic.pinyin_key("李娜"), "lina")
        self.assertEqual(phonetic.pinyin_key("转账"), "zhuanzhang")


class TestDateRanges(unittest.TestCase):
    def test_exact_days(self):
        start, end, label = config.range_bounds("today")
        self.assertEqual(start, end)
        self.assertEqual(label, "今天")
        start, end, label = config.range_bounds("yesterday")
        self.assertEqual(start, end)
        self.assertEqual(label, "昨天")
        start, end, label = config.range_bounds("day_before")
        self.assertEqual(label, "前天")

    def test_this_week(self):
        start, end, label = config.range_bounds("this_week")
        self.assertEqual(label, "本周")
        self.assertLessEqual(start, end)


class TestHomophoneFlow(unittest.TestCase):
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

    def test_typo_payee_resolves(self):
        from bankagent import orchestrator

        res = orchestrator.handle_chat(self.conn, "s_phon", "给李那转 100")
        self.assertEqual(res["status"], "awaiting_confirm")
        self.assertIn("李娜", res["reply"])

    def test_yesterday_bill(self):
        from bankagent import orchestrator

        res = orchestrator.handle_chat(self.conn, "s_yday", "昨天花了多少")
        self.assertEqual(res["status"], "done")
        self.assertIn("昨天", res["reply"])


if __name__ == "__main__":
    unittest.main()
