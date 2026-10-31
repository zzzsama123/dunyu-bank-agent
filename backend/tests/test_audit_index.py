"""全量审计索引测试：按 trace 聚合、摘要、搜索。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed, trust


class TestAuditIndex(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        db.reset_db(self.path)
        self.conn = db.connect(self.path)
        seed.seed(self.conn)
        for text in ("查这个月账单", "给张三转100"):
            orchestrator.handle_chat(self.conn, "s_audit", text)

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def test_list_traces_summary(self):
        data = trust.AuditLog(self.conn).list_traces(10)
        self.assertGreaterEqual(data["total"], 2)
        summaries = [item["summary"] for item in data["traces"]]
        self.assertIn("给张三转100", summaries)
        self.assertTrue(all(item["events"] > 0 for item in data["traces"]))

    def test_search_traces(self):
        data = trust.AuditLog(self.conn).list_traces(10, query="张三")
        self.assertEqual(data["total"], 1)
        self.assertIn("张三", data["traces"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
