"""会话层测试：问候/感谢/确认语气/能力/转人工/闲聊，以及"不要误吞业务句"。"""

from __future__ import annotations

import os
import tempfile
import unittest

import os as _os
_os.environ["AGENT_BRAIN"] = "rules"   # 测试固定规则模式，避免本机 .env 的 Key 让单测去调真模型
from bankagent import bankcore, config, db, dialogue, orchestrator, seed
config.AGENT_BRAIN = "rules"  # 固定规则模式（不受本机 .env 影响）


class TestDialogueMatch(unittest.TestCase):
    def test_social_utterances(self):
        cases = {
            "你好": "greeting", "您好": "greeting", "在吗": "greeting",
            "谢谢": "thanks", "谢了": "thanks",
            "好的": "affirm", "嗯嗯": "affirm", "行": "affirm",
            "你能做什么": "capability", "你是谁": "identity",
            "转人工": "handoff", "我想投诉": "handoff",
            "今天天气怎么样": "offtopic", "我忘了": "lost",
            "再见": "goodbye", "太复杂了": "complain",
            "帮我写一首诗": "offdomain", "写个代码": "offdomain", "翻译一下": "offdomain",
        }
        for text, expect in cases.items():
            self.assertEqual(dialogue.match(text), expect, f"{text} 应识别为 {expect}")

    def test_business_utterances_not_swallowed(self):
        for text in ("这三个会员我早就不用了", "把没用的都退了", "转 500 给我妈",
                     "我这个月钱都去哪了", "我卡丢了", "取消订阅", "帮我转点钱"):
            self.assertIsNone(dialogue.match(text), f"{text} 不应被会话层拦截")

    def test_affirm_with_pending_points_to_card(self):
        reply = dialogue.reply_for("affirm", {"has_pending": True, "pending_level": "yellow"})
        self.assertIn("确认卡", reply)
        self.assertIn("文字回复不算授权", reply)

    def test_identity_mentions_no_execution_right(self):
        reply = dialogue.reply_for("identity")
        self.assertIn("不持有执行权", reply)


class TestDialogueIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        db.reset_db(self.tmp.name)
        self.conn = db.connect(self.tmp.name)
        seed.seed(self.conn)
        self.sid = "s_dlg_" + self.id().rsplit(".", 1)[-1]

    def tearDown(self):
        self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp.name + suffix)
            except OSError:
                pass

    def test_greeting_has_no_plan_event(self):
        res = orchestrator.handle_chat(self.conn, self.sid, "你好")
        self.assertEqual(res["status"], "done")
        types = [e["type"] for e in res["events"]]
        self.assertNotIn("plan", types)
        self.assertNotIn("policy", types)
        self.assertIn("智能助手", res["reply"])

    def test_handoff_not_parsed_as_transfer(self):
        res = orchestrator.handle_chat(self.conn, self.sid, "转人工")
        self.assertEqual(res["status"], "done")
        self.assertIn("人工", res["reply"])
        self.assertNotIn("转多少", res["reply"])

    def test_text_yes_does_not_authorize_pending(self):
        first = orchestrator.handle_chat(self.conn, self.sid, "转 100 给张三")
        if first["status"] != "awaiting_confirm":
            self.skipTest("NLU 未产出待确认计划")
        before = bankcore.get_balance(self.conn, 1)
        res = orchestrator.handle_chat(self.conn, self.sid, "好的")
        self.assertEqual(bankcore.get_balance(self.conn, 1), before)
        self.assertIn("确认卡", res["reply"])

    def test_missing_amount_asks_both_slots(self):
        res = orchestrator.handle_chat(self.conn, self.sid, "帮我转点钱")
        self.assertIn("转给谁", res["reply"])
        self.assertIn("转多少", res["reply"])


if __name__ == "__main__":
    unittest.main()
