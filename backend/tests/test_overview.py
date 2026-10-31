"""概览接口：余额、本月支出、下次扣费、联系人与计划。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, overview, seed


class TestOverview(unittest.TestCase):
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

    def test_fields(self):
        data = overview.build(self.conn, 1)
        self.assertGreater(data["balance_cents"], 0)
        self.assertIn("spend_cents", data["month"])
        self.assertEqual(len(data["contacts"]), 4)
        self.assertIn("count", data["subscription"])

    def test_next_subscription_sorted(self):
        data = overview.build(self.conn, 1)
        nxt = data["subscription"]["next"]
        self.assertIsNotNone(nxt)
        self.assertTrue(nxt["charge_at"])
        self.assertGreater(nxt["amount_cents"], 0)

    def test_plan_block_present(self):
        data = overview.build(self.conn, 1)
        self.assertIn("plan", data)
        self.assertIn("count", data["plan"])


if __name__ == "__main__":
    unittest.main()
