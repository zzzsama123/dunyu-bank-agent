"""账务核心测试：不变量、幂等、冲正、额度并发。"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest

from bankagent import bankcore, config, db, seed


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        db.reset_db(self.tmp.name)
        self.conn = db.connect(self.tmp.name)
        seed.seed(self.conn)

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass


class TestInvariants(Base):
    def test_seed_balance(self):
        self.assertEqual(bankcore.get_balance(self.conn, 1), seed.TARGET_BALANCE)

    def test_i1_double_entry_balanced(self):
        rows = self.conn.execute(
            "SELECT txn_id FROM ledger_entry GROUP BY txn_id "
            "HAVING SUM(CASE direction WHEN 'D' THEN amount ELSE -amount END) <> 0"
        ).fetchall()
        self.assertEqual(rows, [], "存在借贷不平衡的交易")

    def test_i4_reversal_pairs(self):
        rows = self.conn.execute(
            "SELECT r.id FROM transactions r LEFT JOIN transactions o ON o.id = r.reversed_by "
            "WHERE r.type='reversal' AND o.id IS NULL"
        ).fetchall()
        self.assertEqual(rows, [])

    def test_balance_only_from_entries(self):
        before = bankcore.get_balance(self.conn, 1)
        r = bankcore.create_transfer(self.conn, 1, 1, 4, 12_300, idem_key="t-bal")
        self.assertTrue(r["ok"])
        self.assertEqual(bankcore.get_balance(self.conn, 1), before - 12_300)


class TestTransfer(Base):
    def test_transfer_and_entries(self):
        before = bankcore.get_balance(self.conn, 1)
        r = bankcore.create_transfer(self.conn, 1, 1, 1, 20_000, memo="测试")
        self.assertTrue(r["ok"])
        self.assertEqual(r["balance_after"], before - 20_000)
        entries = self.conn.execute("SELECT * FROM ledger_entry WHERE txn_id=?", (r["txn_id"],)).fetchall()
        self.assertEqual(len(entries), 2)
        self.assertEqual({e["direction"] for e in entries}, {"D", "C"})

    def test_idempotent_same_key(self):
        a = bankcore.create_transfer(self.conn, 1, 1, 1, 10_000, idem_key="same-1")
        b = bankcore.create_transfer(self.conn, 1, 1, 1, 10_000, idem_key="same-1")
        self.assertTrue(a["ok"] and b["ok"])
        self.assertEqual(a["txn_no"], b["txn_no"])
        cnt = self.conn.execute("SELECT COUNT(*) c FROM transactions WHERE idempotency_key='same-1'").fetchone()["c"]
        self.assertEqual(cnt, 1)

    def test_insufficient_fund(self):
        r = bankcore.create_transfer(self.conn, 1, 1, 1, 99_999_999, idem_key="big")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "E_INSUFFICIENT_FUND")

    def test_quota_recorded_without_hard_block(self):
        # 限额是策略层职责；强验证通过后账务层必须能落账，只记录已用额度
        r = bankcore.create_transfer(self.conn, 1, 1, 1, 60_000, idem_key="over-limit")
        self.assertTrue(r["ok"])
        self.assertEqual(bankcore.get_daily_used(self.conn, 1), seed.TODAY_TRANSFER + 60_000)

    def test_unknown_contact(self):
        r = bankcore.create_transfer(self.conn, 1, 1, 999, 100, idem_key="no-contact")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "E_CONTACT_NOT_FOUND")


class TestReversal(Base):
    def test_reverse_restores_balance_and_quota(self):
        before = bankcore.get_balance(self.conn, 1)
        t = bankcore.create_transfer(self.conn, 1, 1, 2, 30_000, idem_key="rev-1")
        self.assertTrue(t["ok"])
        self.assertEqual(bankcore.get_daily_used(self.conn, 1), seed.TODAY_TRANSFER + 30_000)
        rev = bankcore.reverse_transaction(self.conn, t["txn_id"])
        self.assertTrue(rev["ok"])
        self.assertEqual(bankcore.get_balance(self.conn, 1), before)
        orig = self.conn.execute("SELECT status,reversed_by FROM transactions WHERE id=?", (t["txn_id"],)).fetchone()
        self.assertEqual(orig["status"], "reversed")
        self.assertEqual(orig["reversed_by"], rev["reversal_txn_id"])
        self.assertEqual(bankcore.get_daily_used(self.conn, 1), seed.TODAY_TRANSFER)

    def test_double_reverse_rejected(self):
        t = bankcore.create_transfer(self.conn, 1, 1, 2, 10_000, idem_key="rev-2")
        self.assertTrue(t.get("ok"), t)
        self.assertTrue(bankcore.reverse_transaction(self.conn, t["txn_id"])["ok"])
        again = bankcore.reverse_transaction(self.conn, t["txn_id"])
        self.assertFalse(again["ok"])

    def test_no_delete_only_append(self):
        n0 = self.conn.execute("SELECT COUNT(*) c FROM ledger_entry").fetchone()["c"]
        t = bankcore.create_transfer(self.conn, 1, 1, 2, 10_000, idem_key="rev-3")
        bankcore.reverse_transaction(self.conn, t["txn_id"])
        n1 = self.conn.execute("SELECT COUNT(*) c FROM ledger_entry").fetchone()["c"]
        self.assertEqual(n1 - n0, 4, "一笔转账 + 一笔冲正应产生 4 条分录，且不删除任何记录")


class TestQuotaConcurrency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        db.reset_db(self.tmp.name)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass

    def test_concurrent_occupy_only_one_wins(self):
        results = []

        def worker():
            conn = db.connect(self.tmp.name)
            try:
                results.append(bankcore.occupy_quota(conn, 1, 60_000, 100_000))
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [False, True])



class TestNextRun(Base):
    def test_birthday_trigger_uses_profile(self):
        from datetime import date, timedelta

        today = config.now_dt().date()
        run = date(today.year, 11, 18)
        if run < today:
            run = date(today.year + 1, 11, 18)
        expect = (run - timedelta(days=2)).isoformat()
        got = bankcore._next_run_at(self.conn, 1, {"type": "birthday", "days_before": 2, "time": "09:00"})
        self.assertTrue(got.startswith(expect), got)

    def test_monthly_weekly_daily(self):
        today = config.now_dt().date()
        monthly = bankcore._next_run_at(self.conn, 1, {"type": "monthly", "day": 1, "time": "09:00"})
        self.assertIn("-01T09:00:00+08:00", monthly)
        weekly = bankcore._next_run_at(self.conn, 1, {"type": "weekly", "weekday": 0, "time": "09:00"})
        self.assertIn("T09:00:00+08:00", weekly)
        daily = bankcore._next_run_at(self.conn, 1, {"type": "daily", "time": "09:00"})
        self.assertGreaterEqual(daily[:10], today.isoformat())

if __name__ == "__main__":
    unittest.main()
