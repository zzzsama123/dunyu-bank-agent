"""业务问答测试：限额、年费、最近一笔、联系人、强验证解释、到账规则。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, insight, seed


class TestInsight(unittest.TestCase):
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

    def test_quota(self):
        text = insight.answer(self.conn, 1, "我今天还能转多少")
        self.assertIn("免密额度", text)
        self.assertIn("已用", text)

    def test_annual_subs(self):
        text = insight.answer(self.conn, 1, "我一年订阅要花多少")
        self.assertIn("一年大约", text)

    def test_latest(self):
        text = insight.answer(self.conn, 1, "刚才那笔到哪了")
        self.assertIn("最近一笔", text)

    def test_contacts(self):
        text = insight.answer(self.conn, 1, "我有哪些联系人")
        self.assertIn("联系人", text)
        self.assertIn("妈", text)

    def test_why_stepup(self):
        text = insight.answer(self.conn, 1, "为什么需要强验证")
        self.assertIn("强验证", text)

    def test_fee(self):
        text = insight.answer(self.conn, 1, "转账多久到账，有手续费吗")
        self.assertIn("到账", text)

    def test_unknown_returns_none(self):
        self.assertIsNone(insight.answer(self.conn, 1, "给我妈转100"))


    def test_budget_set_and_query(self):
        set_reply = insight.answer(self.conn, 1, "把餐饮预算设为1500")
        self.assertIn("餐饮", set_reply)
        query = insight.answer(self.conn, 1, "本月预算还剩多少")
        self.assertIn("预算", query)
        self.assertIn("餐饮", query)

    def test_deposit_calculator(self):
        text = insight.answer(self.conn, 1, "10万存一年利息多少")
        self.assertIn("利息", text)
        self.assertIn("1,450.00", text)

    def test_loan_calculator(self):
        text = insight.answer(self.conn, 1, "借10万3年每月还多少")
        self.assertIn("每月约", text)

    def test_installment_calculator(self):
        text = insight.answer(self.conn, 1, "1万分12期手续费多少")
        self.assertIn("12 期", text)
        self.assertIn("手续费", text)

    def test_search_by_merchant(self):
        text = insight.answer(self.conn, 1, "搜一下星耀直播的交易")
        self.assertIn("星耀直播", text)
        self.assertIn("笔交易", text)


    def test_calculator_followup_changes_term(self):
        from bankagent import orchestrator

        first = orchestrator.handle_chat(self.conn, "s_calc", "10万存一年利息多少")
        self.assertIn("1,450.00", first["reply"])
        second = orchestrator.handle_chat(self.conn, "s_calc", "存半年期")
        self.assertIn("0.5 年", second["reply"])
        self.assertIn("650.00", second["reply"])

    def test_calc_followup_without_context(self):
        from bankagent import orchestrator

        res = orchestrator.handle_chat(self.conn, "s_calc2", "存半年期")
        self.assertIn("先告诉我金额", res["reply"])


if __name__ == "__main__":
    unittest.main()
