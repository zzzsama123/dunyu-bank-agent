"""测试包。

重要：测试必须与"本机是否配了大模型 Key"无关。
这里先把 AGENT_BRAIN 固定为 rules，避免 backend/.env 里的 Key 让单元测试去调真模型。
需要测 LLM 适配层时，用 unittest.mock 显式 patch。
"""

import os

os.environ["AGENT_BRAIN"] = "rules"
