"""大模型适配层测试：JSON 规范化、真实 HTTP 调用（本地 mock）、失败回退与审计留痕。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import os as _os
_os.environ["AGENT_BRAIN"] = "rules"   # 测试固定规则模式，避免本机 .env 的 Key 让单测去调真模型
from bankagent import config, db, llm, orchestrator, seed, specs
config.AGENT_BRAIN = "rules"  # 固定规则模式（不受本机 .env 影响）


class _MockLLM(BaseHTTPRequestHandler):
    reply = {"name": "transfer", "slots": {"amount": 50000, "payee": "妈"}, "missing": [],
             "spans": {"amount": "500", "payee": "我妈"}, "confidence": 0.93}

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = {
            "model": "mock-chat",
            "choices": [{"message": {"content": "```json\n" + json.dumps(self.reply, ensure_ascii=False) + "\n```"}}],
            "usage": {"total_tokens": 42},
        }
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class TestNormalize(unittest.TestCase):
    def test_extract_json_from_fence(self):
        obj = llm._extract_json('```json\n{"a": 1}\n```')
        self.assertEqual(obj, {"a": 1})

    def test_extract_json_garbage(self):
        self.assertIsNone(llm._extract_json("抱歉，我无法完成"))
        self.assertIsNone(llm._extract_json(""))

    def test_intent_unknown_name_falls_back(self):
        it = llm._normalize_intent({"name": "hack_the_bank", "slots": {}, "confidence": 2})
        self.assertEqual(it["name"], "unknown")
        self.assertLessEqual(it["confidence"], 1.0)

    def test_plan_rejects_tool_outside_whitelist(self):
        plan = llm._normalize_plan({"nodes": [{"id": "n1", "tool": "drop_database", "args": {}}]},
                                   {"name": "transfer"})
        self.assertIsNone(plan)

    def test_plan_rejects_system_tool(self):
        plan = llm._normalize_plan({"nodes": [{"id": "n1", "tool": "notify", "args": {}}]},
                                   {"name": "transfer"})
        self.assertIsNone(plan)

    def test_plan_ok_and_provenance_normalized(self):
        plan = llm._normalize_plan({
            "goal": "转账", "nodes": [
                {"id": "n1", "tool": "get_contacts", "args": {"alias": "妈"}, "provenance": {"alias": "user"}},
                {"id": "n2", "tool": "create_transfer", "args": {"contact_id": "$n1.contact_id", "amount": 50000},
                 "provenance": {"contact_id": "tool", "amount": "user"}, "depends_on": ["n1"]},
            ]}, {"name": "transfer"})
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan["nodes"]), 2)
        self.assertEqual(plan["source"], "llm")


class TestHttpWire(unittest.TestCase):
    """真起一个本地 HTTP 服务，验证 urllib 调用链（不联网）。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockLLM)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_parse_intent_over_http(self):
        with patch.object(config, "LLM_BASE_URL", f"http://127.0.0.1:{self.port}/v1"), \
             patch.object(config, "LLM_API_KEY", "sk-test"), \
             patch.object(config, "AGENT_BRAIN", "llm"):
            self.assertTrue(llm.enabled())
            it, meta = llm.parse_intent("给我妈转 500", {"user_id": 1})
        self.assertEqual(it["name"], "transfer")
        self.assertEqual(it["slots"]["amount"], 50000)
        self.assertEqual(meta.get("tokens"), 42)

    def test_unreachable_returns_none(self):
        with patch.object(config, "LLM_BASE_URL", "http://127.0.0.1:9/v1"), \
             patch.object(config, "LLM_API_KEY", "sk-test"), \
             patch.object(config, "AGENT_BRAIN", "llm"), \
             patch.object(config, "LLM_TIMEOUT", 1):
            it, meta = llm.parse_intent("你好", {})
        self.assertIsNone(it)


class TestFallbackWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        db.reset_db(self.tmp.name)
        self.conn = db.connect(self.tmp.name)
        seed.seed(self.conn)
        self.sid = "s_llm"
        # 本组用例验证"规则优先 + 模型兜底"的旧链路；命令 IR 链路在 test_commands.py 里测
        self._mode = patch.object(config, "AGENT_MODE", "rules")
        self._mode.start()
        self.addCleanup(self._mode.stop)

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass

    def test_llm_intent_is_used_and_audited(self):
        intent = {"name": "bill_analysis", "slots": {"range": "this_month"}, "missing": [],
                  "ambiguity": [], "spans": {}, "confidence": 0.9, "source": "llm"}
        with patch.object(llm, "enabled", return_value=True), \
             patch.object(llm, "parse_intent", return_value=(intent, {"model": "mock"})), \
             patch.object(llm, "build_plan", return_value=(None, {})):
            res = orchestrator.handle_chat(self.conn, self.sid, "随便问问")
        self.assertEqual(res["status"], "done")
        audit = orchestrator.get_audit(self.conn, res["trace_id"])
        actions = [e["action"] for e in audit["events"]]
        self.assertIn("brain.nlu", actions)

    def test_out_of_scope_intent_is_refused(self):
        intent = {"name": "unknown", "slots": {}, "missing": [], "ambiguity": ["out_of_scope"],
                  "spans": {}, "confidence": 0.2, "source": "llm"}
        with patch.object(llm, "enabled", return_value=True), \
             patch.object(llm, "parse_intent", return_value=(intent, {})), \
             patch.object(llm, "build_plan", return_value=(None, {})):
            res = orchestrator.handle_chat(self.conn, self.sid, "解释一下相对论")
        self.assertEqual(res["status"], "done")
        self.assertIn("不属于银行业务", res["reply"])
        self.assertNotIn("plan", [e["type"] for e in res["events"]])
        audit = orchestrator.get_audit(self.conn, res["trace_id"])
        self.assertIn("guard.out_of_scope", [e["action"] for e in audit["events"]])

    def test_invalid_llm_plan_falls_back_to_rules(self):
        intent = {"name": "bill_analysis", "slots": {"range": "this_month"}, "missing": [],
                  "ambiguity": [], "spans": {}, "confidence": 0.9, "source": "llm"}
        bad_plan = {"plan_id": "pl_bad", "goal": "x", "intent": "bill_analysis",
                    "nodes": [{"id": "n1", "tool": "get_accounts", "args": {},
                               "provenance": {}, "depends_on": [], "risk_hint": "green"}],
                    "source": "llm"}
        # 用 cycle 让校验失败
        bad_plan["nodes"].append({"id": "n2", "tool": "get_transactions", "args": {},
                                  "provenance": {}, "depends_on": ["n2"], "risk_hint": "green"})
        with patch.object(llm, "enabled", return_value=True), \
             patch.object(llm, "parse_intent", return_value=(intent, {})), \
             patch.object(llm, "build_plan", return_value=(bad_plan, {})):
            res = orchestrator.handle_chat(self.conn, self.sid, "看看这个月账")
        self.assertEqual(res["status"], "done")
        audit = orchestrator.get_audit(self.conn, res["trace_id"])
        plan_events = [e for e in audit["events"] if e["action"] == "brain.plan"]
        self.assertTrue(plan_events and plan_events[0]["decision"] == "fallback")


if __name__ == "__main__":
    unittest.main()
