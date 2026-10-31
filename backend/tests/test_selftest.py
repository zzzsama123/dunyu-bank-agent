"""安全证据自测：token/参数/审计链篡改必须被拦截。"""

from __future__ import annotations

import unittest

from bankagent import selftest


class TestSecuritySelftest(unittest.TestCase):
    def test_token_tamper(self):
        result = selftest.run("token")
        self.assertTrue(result["ok"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["actual"], "E_CONFIRM_TOKEN")

    def test_params_tamper(self):
        result = selftest.run("params")
        self.assertTrue(result["ok"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["actual"], "E_PARAM")

    def test_audit_tamper(self):
        result = selftest.run("audit")
        self.assertTrue(result["ok"])
        self.assertTrue(result["passed"])
        self.assertIn("before=True", result["actual"])
        self.assertIn("after=False", result["actual"])


if __name__ == "__main__":
    unittest.main()
