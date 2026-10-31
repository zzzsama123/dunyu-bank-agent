"""熔断测试：连续失败 → OBSERVE → LOCKED → 到期自动解锁。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from bankagent import bankcore, db, orchestrator, seed


class TestCircuit(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        db.reset_db(self.path)
        self.conn = db.connect(self.path)
        seed.seed(self.conn)
        self.sid = "s_circuit"

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def state(self) -> str:
        return (bankcore.get_session(self.conn, self.sid) or {}).get("state")

    def test_three_failures_go_observe_then_lock(self):
        for _ in range(3):
            res = orchestrator.handle_chat(self.conn, self.sid, "转 50000 给张三")
            self.assertEqual(res["status"], "denied")
        self.assertEqual(self.state(), "OBSERVE")

        for _ in range(2):
            orchestrator.handle_chat(self.conn, self.sid, "转 50000 给张三")
        self.assertEqual(self.state(), "LOCKED")

        locked = orchestrator.handle_chat(self.conn, self.sid, "转 10 给张三")
        self.assertEqual(locked["status"], "denied")
        self.assertTrue("锁定" in locked["reply"] or "LOCKED" in locked["reply"])

    def test_reads_still_allowed_when_locked(self):
        for _ in range(5):
            orchestrator.handle_chat(self.conn, self.sid, "转 50000 给张三")
        self.assertEqual(self.state(), "LOCKED")
        res = orchestrator.handle_chat(self.conn, self.sid, "我这个月钱都去哪了")
        self.assertEqual(res["status"], "done")

    def test_lock_expires_and_unlocks(self):
        for _ in range(5):
            orchestrator.handle_chat(self.conn, self.sid, "转 50000 给张三")
        self.assertEqual(self.state(), "LOCKED")
        sess = bankcore.get_session(self.conn, self.sid)
        ents = json.loads(sess.get("recent_entities") or "{}")
        ents["locked_until"] = "2000-01-01T00:00:00+08:00"
        bankcore.update_session(self.conn, self.sid, state="LOCKED", recent_entities=ents)

        res = orchestrator.handle_chat(self.conn, self.sid, "你好")
        self.assertEqual(res["status"], "done")
        self.assertEqual(self.state(), "NORMAL")

    def test_success_resets_streak(self):
        orchestrator.handle_chat(self.conn, self.sid, "转 50000 给张三")
        self.assertEqual(self.state(), "NORMAL")  # 1 次未到阈值
        orchestrator.handle_chat(self.conn, self.sid, "我这个月钱都去哪了")
        sess = bankcore.get_session(self.conn, self.sid)
        ents = json.loads(sess.get("recent_entities") or "{}")
        self.assertEqual(int(ents.get("fail_streak") or 0), 0)

    def test_stepup_failure_counts(self):
        orchestrator.handle_chat(self.conn, self.sid, "我卡丢了")
        pending = self.conn.execute(
            "SELECT stepup_json FROM pending_intents WHERE session_id=? AND status='awaiting_stepup' "
            "ORDER BY created_at DESC LIMIT 1", (self.sid,)).fetchone()
        self.assertIsNotNone(pending)
        challenge = json.loads(pending["stepup_json"])["challenge_id"]
        orchestrator.handle_stepup(self.conn, self.sid, challenge, "000000")
        sess = bankcore.get_session(self.conn, self.sid)
        ents = json.loads(sess.get("recent_entities") or "{}")
        self.assertGreaterEqual(int(ents.get("fail_streak") or 0), 1)

    def test_stepup_failures_lock_session(self):
        """连续 5 次验证码错误 → LOCKED；第 6 次写操作直接拒绝。"""
        for index in range(5):
            res = orchestrator.handle_chat(self.conn, self.sid, "给张三转6000")
            self.assertEqual(res["status"], "awaiting_stepup", f"第 {index + 1} 次应进入强验证")
            challenge = res["pending"]["challenge_id"]
            orchestrator.handle_stepup(self.conn, self.sid, challenge, "000000")
        self.assertEqual(self.state(), "LOCKED")
        blocked = orchestrator.handle_chat(self.conn, self.sid, "给张三转6000")
        self.assertEqual(blocked["status"], "denied")


if __name__ == "__main__":
    unittest.main()
