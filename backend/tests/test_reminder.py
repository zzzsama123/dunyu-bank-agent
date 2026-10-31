"""提醒/生日方案跟进测试：改提醒日期不应新建计划，纯提醒不锁资金。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import bankcore, db, orchestrator, seed


class TestReminder(unittest.TestCase):
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

    def test_proposal_reminder_date_change(self):
        first = orchestrator.handle_chat(
            self.conn, "s_rem", "我老婆下个月生日，帮我准备一下，别超过1000")
        self.assertIn("生日", first["reply"])
        second = orchestrator.handle_chat(self.conn, "s_rem", "15号提醒我")
        self.assertEqual(second["status"], "done")
        self.assertIn("15 日 09:00", second["reply"])
        self.assertIsNone(second.get("pending"))

        third = orchestrator.handle_chat(self.conn, "s_rem", "按这个来")
        self.assertEqual(third["status"], "awaiting_confirm")
        fields = dict(third["pending"]["card"]["fields"])
        self.assertIn("-15 09:00", fields["计划触发"])

    def test_standalone_reminder_has_no_budget(self):
        reply = orchestrator.handle_chat(self.conn, "s_rem2", "15号提醒我交房租")
        self.assertEqual(reply["status"], "done")
        self.assertIn("只提醒，不动资金", reply["reply"])
        plans = bankcore.list_standing_plans(self.conn, 1)
        self.assertTrue(plans)
        self.assertEqual(int(plans[-1]["budget"]), 0)


if __name__ == "__main__":
    unittest.main()
