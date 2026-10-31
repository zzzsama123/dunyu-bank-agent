"""沙箱测试：只读、白名单、单语句、行数上限。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bankagent import db, sandbox, seed


class TestSandbox(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".db")
        db.reset_db(self.path)
        conn = db.connect(self.path)
        seed.seed(conn)
        conn.close()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    def test_read_views(self):
        rows = sandbox.query("SELECT counterparty, amount FROM v_transactions LIMIT 5", path=self.path)
        self.assertTrue(rows)
        self.assertIn("counterparty", rows[0])

    def test_account_balance_view(self):
        rows = sandbox.query("SELECT account_id, balance FROM v_account_balance", path=self.path)
        self.assertEqual(rows[0]["balance"], 1234567)

    def test_write_rejected(self):
        for sql in ("INSERT INTO transactions(txn_no) VALUES('x')",
                    "UPDATE accounts SET opening_balance = 0",
                    "DELETE FROM transactions"):
            with self.assertRaises(sandbox.SandboxError):
                sandbox.query(sql, path=self.path)

    def test_unknown_table_rejected(self):
        with self.assertRaises(sandbox.SandboxError):
            sandbox.query("SELECT * FROM users", path=self.path)

    def test_multiple_statements_rejected(self):
        with self.assertRaises(sandbox.SandboxError):
            sandbox.query("SELECT 1 FROM v_cards; SELECT 2 FROM v_cards", path=self.path)

    def test_row_limit_applied(self):
        rows = sandbox.query("SELECT id FROM v_transactions", path=self.path)
        self.assertLessEqual(len(rows), 200)

    def test_no_code_execution_surface(self):
        # 沙箱模块不暴露 eval/exec/文件读取入口
        for name in ("exec", "eval", "open", "system", "popen"):
            self.assertFalse(hasattr(sandbox, name), name)


if __name__ == "__main__":
    unittest.main()
