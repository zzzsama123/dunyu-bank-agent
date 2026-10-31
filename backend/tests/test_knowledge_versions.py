"""知识版本测试：草稿→审批→发布、旧版下架、过期。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, knowledge, seed


class TestKnowledgeVersions(unittest.TestCase):
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

    def test_draft_approve_publish(self):
        draft = knowledge.draft(self.conn, "kb-transfer-limit", "新版：单笔 3000 元以内免确认。")
        self.assertEqual(draft["version"], "v4")
        self.assertEqual(knowledge.approve(self.conn, draft["id"])["status"], "approved")
        knowledge.publish(self.conn, draft["id"])
        reply = knowledge.answer("转账限额政策是什么", conn=self.conn)
        self.assertIn("新版", reply)
        self.assertIn("版本：v4", reply)

    def test_publish_retires_old(self):
        draft = knowledge.draft(self.conn, "kb-arrival", "新版到账规则。")
        knowledge.approve(self.conn, draft["id"])
        knowledge.publish(self.conn, draft["id"])
        rows = [r for r in knowledge.status(conn=self.conn)
                if r["entry_id"] == "kb-arrival" and r["status"] == "published"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["version"], draft["version"])

    def test_expire_due(self):
        count = knowledge.expire_due(self.conn)
        self.assertGreaterEqual(count, 1)
        reply = knowledge.answer("跨境汇款需要什么材料", conn=self.conn)
        self.assertIn("失效", reply)


if __name__ == "__main__":
    unittest.main()
