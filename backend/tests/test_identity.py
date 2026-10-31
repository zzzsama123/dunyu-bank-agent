"""身份与授权测试：客户号、设备绑定、防串话、代办授权。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, identity, orchestrator, seed


class TestIdentity(unittest.TestCase):
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

    def test_profile_customer_no(self):
        data = identity.profile(self.conn)
        self.assertEqual(data["customer_no"], "CUST000001")
        self.assertEqual(data["kyc_level"], "L2")
        self.assertFalse(data["device_bound"])

    def test_device_binding_and_mismatch(self):
        identity.bind_session(self.conn, "s_id", "dev_abc12345")
        identity.bind_session(self.conn, "s_id", "dev_abc12345")
        with self.assertRaises(identity.IdentityError) as ctx:
            identity.bind_session(self.conn, "s_id2", "dev_other999")
        self.assertEqual(ctx.exception.code, "E_DEVICE_MISMATCH")

    def test_authorization_scope_and_revoke(self):
        auth = identity.authorize(self.conn, 1, "李娜", "配偶", "small", 30)
        self.assertEqual(identity.verify_delegation(self.conn, 1, "李娜", "small")["id"], auth["id"])
        with self.assertRaises(identity.IdentityError):
            identity.verify_delegation(self.conn, 1, "李娜", "all")
        identity.revoke(self.conn, 1, auth["id"])
        with self.assertRaises(identity.IdentityError):
            identity.verify_delegation(self.conn, 1, "李娜", "query")

    def test_chat_with_delegation(self):
        identity.authorize(self.conn, 1, "李娜", "配偶", "query", 30)
        reply = orchestrator.handle_chat(self.conn, "s_del", "查余额", act_as="李娜")
        self.assertEqual(reply["status"], "done")
        self.assertIn("余额", reply["reply"])


if __name__ == "__main__":
    unittest.main()
