"""补偿演示：注入失败节点后，已锁预算应被逆序释放。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed


class TestCompensationDemo(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        db.reset_db(self.path)
        self.conn = db.connect(self.path)
        seed.seed(self.conn)
        os.environ.pop("DEMO_FAIL_TOOL", None)

    def tearDown(self):
        os.environ.pop("DEMO_FAIL_TOOL", None)
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def test_injected_failure_triggers_compensation(self):
        orchestrator.handle_chat(self.conn, "s_comp", "我老婆下个月生日，帮我准备一下，别超过1000")
        pending = orchestrator.handle_chat(self.conn, "s_comp", "按这个来")
        self.assertEqual(pending["status"], "awaiting_confirm")
        card = pending["pending"]["card"]
        os.environ["DEMO_FAIL_TOOL"] = "create_standing_plan"
        result = orchestrator.handle_confirm(
            self.conn, "s_comp", card["intent_id"], card["token"])
        self.assertEqual(result["status"], "error")
        self.assertIn("已撤销", result["reply"])
        rows = self.conn.execute("SELECT status FROM freezes").fetchall()
        self.assertTrue(rows)
        self.assertEqual(rows[0]["status"], "released")


if __name__ == "__main__":
    unittest.main()
