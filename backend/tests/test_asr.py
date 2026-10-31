"""离线语音识别测试：状态、参数校验、引擎可用性。"""

from __future__ import annotations

import unittest

from bankagent import asr


class TestASR(unittest.TestCase):
    def test_status_shape(self):
        data = asr.status()
        self.assertIn("available", data)
        self.assertIsInstance(data["available"], bool)

    def test_bad_base64(self):
        with self.assertRaises(ValueError):
            asr.recognize("!!!not-base64!!!", [])

    def test_empty_audio(self):
        with self.assertRaises(ValueError):
            asr.recognize("", [])

    def test_phrase_list_contains_contacts(self):
        phrases = asr._phrases(["张三"])
        self.assertTrue(any("给张三转" in p for p in phrases))


if __name__ == "__main__":
    unittest.main()
