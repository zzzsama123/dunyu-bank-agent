"""澄清候选测试：听不懂时给选项、回数字能接住。"""

from __future__ import annotations

import unittest

from bankagent import clarify


class TestBuild(unittest.TestCase):
    def test_bill_first_for_money_words(self):
        cands = clarify.build("帮我把钱弄一下")
        self.assertEqual(cands[0]["key"], "bill")
        self.assertEqual(len(cands), 4)  # 3 个业务 + 转人工

    def test_transfer_hint(self):
        cands = clarify.build("我要给朋友打钱")
        self.assertEqual(cands[0]["key"], "transfer")

    def test_defaults_when_no_signal(self):
        cands = clarify.build("我想办个业务")
        self.assertEqual([c["key"] for c in cands[:3]], ["bill", "transfer", "subscription"])

    def test_handoff_always_last(self):
        cands = clarify.build("随便")
        self.assertEqual(cands[-1]["key"], "handoff")

    def test_render_is_actionable(self):
        text = clarify.render(clarify.build("帮我把钱弄一下"))
        self.assertIn("1.", text)
        self.assertIn("回个数字", text)
        self.assertIn("转人工", text)


class TestPick(unittest.TestCase):
    def setUp(self):
        self.state = {"candidates": clarify.build("帮我把钱弄一下")}

    def test_arabic_number(self):
        self.assertEqual(clarify.pick(self.state, "1")["key"], "bill")
        self.assertEqual(clarify.pick(self.state, "2")["key"], "transfer")

    def test_ordinal_and_cn(self):
        self.assertEqual(clarify.pick(self.state, "第二个")["key"], "transfer")
        self.assertEqual(clarify.pick(self.state, "三")["key"], "subscription")

    def test_handoff(self):
        self.assertIsNone(clarify.pick(self.state, "4")["intent"])

    def test_out_of_range(self):
        self.assertIsNone(clarify.pick(self.state, "9"))
        self.assertIsNone(clarify.pick(self.state, "好的"))
        self.assertIsNone(clarify.pick(None, "1"))


if __name__ == "__main__":
    unittest.main()
