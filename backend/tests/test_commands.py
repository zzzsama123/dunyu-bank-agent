"""命令 IR 测试：模型输出命令码 → 校验 → 编译成 Intent。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from bankagent import commands, config, db, llm, orchestrator, seed


class TestCatalog(unittest.TestCase):
    def test_all_codes_have_intent_mapping(self):
        for code, spec in commands.CATALOG.items():
            self.assertIn("desc", spec)
            self.assertIn("intent", spec)

    def test_prompt_lists_every_code(self):
        text = commands.catalog_for_prompt()
        for code in commands.CATALOG:
            self.assertIn(code, text)


class TestToIntent(unittest.TestCase):
    def test_transactions(self):
        it = commands.to_intent({"cmd": "query.transactions",
                                 "args": {"range": "last_3m", "direction": "in", "counterparty": "妈"}})
        self.assertEqual(it["name"], "bill_analysis")
        self.assertEqual(it["slots"], {"focus": "transactions", "range": "last_3m",
                                       "direction": "in", "counterparty": "妈"})

    def test_transfer_amount_cents(self):
        it = commands.to_intent({"cmd": "transfer.create", "args": {"amount": 10000, "payee": "张三"}})
        self.assertEqual(it["slots"]["amount"], 10000)

    def test_invalid_amount_dropped(self):
        it = commands.to_intent({"cmd": "transfer.create", "args": {"amount": "很多", "payee": "张三"}})
        self.assertNotIn("amount", it["slots"])

    def test_invalid_direction_and_range_dropped(self):
        it = commands.to_intent({"cmd": "query.transactions",
                                 "args": {"range": "最近很久", "direction": "both"}})
        self.assertNotIn("range", it["slots"])
        self.assertNotIn("direction", it["slots"])

    def test_unknown_command_rejected(self):
        self.assertIsNone(commands.to_intent({"cmd": "drop.database"}))
        self.assertIsNone(commands.to_intent("not-a-dict"))

    def test_handoff(self):
        it = commands.to_intent({"cmd": "human.handoff"})
        self.assertEqual(it["name"], "handoff")


class TestWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        db.reset_db(self.tmp.name)
        self.conn = db.connect(self.tmp.name)
        seed.seed(self.conn)
        self.sid = "s_cmd_" + self.id().rsplit(".", 1)[-1]

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass

    def test_valid_command_is_executed(self):
        cmd = {"cmd": "query.transactions", "args": {"range": "last_3m", "direction": "in",
                                                     "counterparty": "妈"}, "confidence": 0.95}
        with patch.object(config, "AGENT_MODE", "model"), \
             patch.object(llm, "enabled", return_value=True), \
             patch.object(llm, "parse_command", return_value=(cmd, {"model": "mock"})):
            res = orchestrator.handle_chat(self.conn, self.sid, "我妈最近转给我多少钱")
        self.assertEqual(res["status"], "done")
        self.assertIn("转账", res["reply"])
        audit = orchestrator.get_audit(self.conn, res["trace_id"])
        self.assertIn("command.parsed", [e["action"] for e in audit["events"]])

    def test_invalid_command_falls_back(self):
        cmd = {"cmd": "hack.the.bank", "args": {}}
        with patch.object(config, "AGENT_MODE", "model"), \
             patch.object(llm, "enabled", return_value=True), \
             patch.object(llm, "parse_command", return_value=(cmd, {})), \
             patch.object(llm, "parse_intent", return_value=(None, {})), \
             patch.object(llm, "build_plan", return_value=(None, {})):
            res = orchestrator.handle_chat(self.conn, self.sid, "帮我看看这个月账单")
        self.assertEqual(res["status"], "done")
        audit = orchestrator.get_audit(self.conn, res["trace_id"])
        decisions = [e.get("decision") for e in audit["events"] if e["action"] == "command.parsed"]
        self.assertIn("invalid", decisions)


if __name__ == "__main__":
    unittest.main()
