"""本机 API Key 保存与一键切换（不联网、不泄露明文）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from bankagent import config, llm


class TestLlmRuntimeConfig(unittest.TestCase):
    def setUp(self):
        self.old_db = config.DB_PATH
        self.old_mode = config.AGENT_BRAIN
        self.old_key = config.LLM_API_KEY
        self.dir = tempfile.mkdtemp()
        config.DB_PATH = str(Path(self.dir) / "bank.db")

    def tearDown(self):
        llm.configure(clear=True)
        config.DB_PATH = self.old_db
        config.AGENT_BRAIN = self.old_mode
        config.LLM_API_KEY = self.old_key
        for path in Path(self.dir).glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            os.rmdir(self.dir)
        except OSError:
            pass

    def test_save_switch_and_clear(self):
        res = llm.configure(api_key="sk-test-1234567890", enabled=True)
        self.assertTrue(res["saved"])
        self.assertTrue(res["has_key"])
        self.assertTrue(res["enabled"])
        self.assertNotIn("sk-test-1234567890", str(res))
        self.assertIn("****", res["masked"])
        cleared = llm.configure(clear=True)
        self.assertFalse(cleared["has_key"])
        self.assertFalse(cleared["enabled"])


if __name__ == "__main__":
    unittest.main()
