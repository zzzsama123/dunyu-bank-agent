"""口语 / 错字 / 金额归一化测试。"""

from __future__ import annotations

import unittest

from bankagent.normalize import cn_to_int, normalize


class TestAmounts(unittest.TestCase):
    def test_colloquial_chinese(self):
        self.assertEqual(cn_to_int("五百二"), 520)
        self.assertEqual(cn_to_int("一千二"), 1200)
        self.assertEqual(cn_to_int("一万二"), 12000)
        self.assertEqual(cn_to_int("两百五"), 250)
        self.assertEqual(cn_to_int("五千"), 5000)
        self.assertEqual(cn_to_int("一万二千三"), 12300)

    def test_wan_suffix(self):
        self.assertEqual(normalize("转 1.2万 给妈"), "转 12000 给妈")

    def test_amount_after_verb(self):
        self.assertIn("520", normalize("转 五百二 给妈"))
        self.assertIn("12000", normalize("这个月花了一万二"))
        self.assertIn("250", normalize("花了两百五"))


class TestColloquial(unittest.TestCase):
    def test_typos(self):
        self.assertIn("转账", normalize("转帐 100"))
        self.assertIn("账单", normalize("上个月帐单"))
        self.assertIn("云存储", normalize("云储存多少钱"))

    def test_verbs_and_contacts(self):
        self.assertIn("看看", normalize("帮我瞅瞅"))
        self.assertIn("查一下", normalize("查查"))
        self.assertIn("妈", normalize("老妈生日"))
        self.assertIn("取消", normalize("把这个会员退掉"))

    def test_time_words(self):
        self.assertIn("上周", normalize("上周三的流水"))
        self.assertIn("这个月", normalize("月初的账单"))

    def test_transfer_verb(self):
        self.assertIn("转100", normalize("给张三打 100 块"))

    def test_idempotent(self):
        once = normalize("转 五百二 给老妈，查查上个月帐单")
        self.assertEqual(normalize(once), once)


if __name__ == "__main__":
    unittest.main()
