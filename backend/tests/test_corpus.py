"""常用话术回归：48 句高频说法，规则模式下逐条校验状态与关键内容。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed

CASES = [
    ("你好", "done", None),
    ("谢谢", "done", None),
    ("你能做什么", "done", None),
    ("转人工", "done", "人工"),
    ("查余额", "done", "余额"),
    ("我卡里还有多少钱", "done", "余额"),
    ("这个月账单", "done", "支出"),
    ("本月花了多少", "done", "支出"),
    ("上个月花了多少", "done", "支出"),
    ("上周消费", "done", "支出"),
    ("昨天花了多少", "done", "昨天"),
    ("最近三个月流水", "done", "近 3 个月"),
    ("查我妈给我的转账", "done", "转账"),
    ("这个月餐饮花了多少", "done", "餐饮"),
    ("最近有没有盗刷", "done", "留意"),
    ("有没有异常交易", "done", "留意"),
    ("我的订阅", "done", "扣费"),
    ("都开了什么会员", "done", "扣费"),
    ("视频会员多少钱", "done", "25.00"),
    ("我的理财", "done", "理财"),
    ("理财收益", "done", "持仓"),
    ("有哪些理财产品", "done", "在售"),
    ("我有几张卡", "done", "卡"),
    ("卡额度多少", "done", "额度"),
    ("我的额度还剩多少", "done", "免密额度"),
    ("今天还能转多少", "done", "免密额度"),
    ("一年会员花多少", "done", "一年大约"),
    ("刚才那笔成功了吗", "done", "最近一笔"),
    ("转账手续费多少", "done", "手续费"),
    ("为什么需要验证", "done", "强验证"),
    ("给张三转100", "awaiting_confirm", "张三"),
    ("转500给我妈", "awaiting_confirm", None),
    ("给我妈转200", "awaiting_confirm", None),
    ("给房东转3000房租", "awaiting_stepup", None),
    ("转1000给张三", "awaiting_stepup", None),
    ("再给我妈转100", "awaiting_confirm", None),
    ("AA收款每人50", "done", None),
    ("退掉视频会员A", "awaiting_confirm", None),
    ("把没用的会员都退了", "done", "扣费"),
    ("取消自动续费", "done", "扣费"),
    ("我卡丢了", "awaiting_stepup", None),
    ("解挂我的卡", "awaiting_stepup", None),
    ("把额度调到10000", "awaiting_confirm", None),
    ("申请虚拟卡", "awaiting_confirm", None),
    ("买5000稳盈90天", "awaiting_stepup", None),
    ("赎回理财", "done", None),
    ("下个月房租提醒我", "done", "哪天"),
    ("每个月1号提醒我交房租", "awaiting_confirm", "计划"),
]


class TestCommonPhrases(unittest.TestCase):
    def test_corpus(self):
        path = tempfile.mktemp(suffix=".db")
        failures = []
        try:
            for text, status, keyword in CASES:
                db.reset_db(path)
                conn = db.connect(path)
                try:
                    seed.seed(conn)
                    res = orchestrator.handle_chat(conn, "s_corpus", text)
                finally:
                    conn.close()
                reply = res.get("reply") or ""
                if res.get("status") != status or (keyword and keyword not in reply):
                    failures.append((text, status, res.get("status"), reply.replace("\n", " ")[:60]))
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass
        self.assertEqual(failures, [], f"{len(failures)} 句未通过：{failures}")


if __name__ == "__main__":
    unittest.main()
