"""IM 渠道测试：同一后端、channel=im 会触发更严的升级规则。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, orchestrator, seed


class TestIMChannel(unittest.TestCase):
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

    def test_im_channel_escalates_amount_over_300(self):
        app = orchestrator.handle_chat(self.conn, "s_app", "转 400 给张三", channel="app")
        im = orchestrator.handle_chat(self.conn, "s_im", "转 400 给张三", channel="im")
        self.assertEqual(app["status"], "awaiting_confirm")
        self.assertEqual(im["status"], "awaiting_stepup")
        self.assertIn("IM", im["reply"])

    def test_im_small_amount_stays_confirm(self):
        im = orchestrator.handle_chat(self.conn, "s_im2", "转 100 给张三", channel="im")
        self.assertEqual(im["status"], "awaiting_confirm")

    def test_im_session_is_separate(self):
        orchestrator.handle_chat(self.conn, "im:u1", "你好", channel="im")
        orchestrator.handle_chat(self.conn, "im:u2", "你好", channel="im")
        rows = self.conn.execute("SELECT id FROM sessions WHERE id LIKE 'im:%'").fetchall()
        self.assertEqual({r["id"] for r in rows}, {"im:u1", "im:u2"})


if __name__ == "__main__":
    unittest.main()
