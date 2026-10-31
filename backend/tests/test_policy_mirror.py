"""策略资产一致性：policy.py 的 rule_id 必须都出现在 rego 镜像里。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from bankagent import policy


class TestPolicyMirror(unittest.TestCase):
    def test_rego_contains_policy_rule_ids(self):
        rego_path = Path(policy.__file__).with_name("rules") / "policy.rego"
        text = rego_path.read_text(encoding="utf-8")
        rego_ids = set(re.findall(r"R-[A-Z]-\d{2}", text))
        python_ids = {
            value for name, value in vars(policy).items()
            if name.startswith("R_") and isinstance(value, str) and value.startswith("R-")
        }
        missing = sorted(python_ids - rego_ids)
        self.assertFalse(missing, f"policy.rego 缺少规则编号：{missing}")
        self.assertGreater(len(python_ids), 10)


if __name__ == "__main__":
    unittest.main()
