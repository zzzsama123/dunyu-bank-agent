"""可选语义层测试：开发机没有 onnxruntime 时自动跳过匹配用例。"""

from __future__ import annotations

import unittest

from bankagent import semantic


class TestSemantic(unittest.TestCase):
    def test_status_shape(self):
        data = semantic.status()
        self.assertIn("available", data)
        self.assertIsInstance(data["available"], bool)

    @unittest.skipUnless(semantic.available(), "未安装语义模型/onnxruntime")
    def test_semantic_match_paraphrase(self):
        intent = semantic.match("这月还没发工资就快花完了", [])
        self.assertIsNotNone(intent)
        self.assertEqual(intent["name"], "bill_analysis")


if __name__ == "__main__":
    unittest.main()
