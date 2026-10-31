"""语义帧解析测试：先把话拆成帧，再编译成命令。"""

from __future__ import annotations

import unittest

from bankagent import understanding

CONTACTS = [
    {"id": 1, "name": "王秀兰", "alias": "妈"},
    {"id": 2, "name": "李娜", "alias": "老婆"},
    {"id": 3, "name": "陈先生", "alias": "房东"},
]


class TestFrames(unittest.TestCase):
    def frame(self, text):
        return understanding.parse(text, {}, CONTACTS)

    def test_flow_between_me_and_contact(self):
        f = self.frame("查询我妈这个月与我的流水")
        self.assertIsNotNone(f)
        self.assertEqual(f["slots"]["focus"], "transactions")
        self.assertEqual(f["slots"]["range"], "this_month")
        self.assertEqual(f["slots"]["counterparty"], "妈")
        self.assertIsNone(f["slots"].get("direction"))

    def test_incoming_transfers(self):
        f = self.frame("查询最近3个月我妈给我的转账")
        self.assertEqual(f["slots"]["direction"], "in")
        self.assertEqual(f["slots"]["range"], "last_3m")
        self.assertEqual(f["slots"]["counterparty"], "妈")

    def test_outgoing_transfers(self):
        f = self.frame("查一下我转给我妈的转账")
        self.assertEqual(f["slots"]["direction"], "out")

    def test_plain_flow(self):
        f = self.frame("最近3个月我的流水")
        self.assertEqual(f["slots"]["focus"], "transactions")
        self.assertEqual(f["slots"]["range"], "last_3m")
        self.assertNotIn("counterparty", f["slots"])

    def test_category_filter(self):
        f = self.frame("查一下上个月餐饮流水")
        self.assertEqual(f["slots"].get("category"), "餐饮")
        self.assertEqual(f["slots"]["range"], "last_month")

    def test_not_a_query(self):
        for text in ("帮我转 500 给我妈", "今天天气怎么样", "我卡丢了",
                     "把没用的都退了", "看看上个月花了多少"):
            self.assertIsNone(self.frame(text), text)


if __name__ == "__main__":
    unittest.main()
