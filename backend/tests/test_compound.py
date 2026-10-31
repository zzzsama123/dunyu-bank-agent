"""复合请求：拆句、代词消解、第一件完成后自动办第二件。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed


class TestCompound(unittest.TestCase):
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

    def test_split_transfer_then_query(self):
        pair = orchestrator._split_compound("给张三转300并查询我与他最近的流水")
        self.assertIsNotNone(pair)
        self.assertEqual(pair[0], "给张三转300")
        self.assertIn("流水", pair[1])

    def test_split_requires_write_first(self):
        self.assertIsNone(orchestrator._split_compound("查询流水并看看账单"))

    def test_compound_runs_second_after_confirm(self):
        res = orchestrator.handle_chat(self.conn, "s_cmp", "给张三转300并查询我与他最近的流水")
        self.assertEqual(res["status"], "awaiting_confirm")
        card = res["pending"]["card"]
        done = orchestrator.handle_confirm(self.conn, "s_cmp", res["pending"]["intent_id"],
                                           card.get("token", ""))
        self.assertEqual(done["status"], "done")
        self.assertIn("转账 300.00", done["reply"])
        self.assertIn("接着办了第二件", done["reply"])
        self.assertIn("张三", done["reply"])

    def test_pronoun_resolved_to_contact(self):
        res = orchestrator.handle_chat(self.conn, "s_cmp2", "给张三转300并查询我与他最近的流水")
        sess = self.conn.execute("SELECT recent_entities FROM sessions WHERE id='s_cmp2'").fetchone()
        self.assertIn("张三", sess["recent_entities"])


if __name__ == "__main__":
    unittest.main()
