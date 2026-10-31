"""盲测集 Runner：只跑 NLU/语义帧，不用盲测结果调规则。

用法：cd backend && python -m eval.blind_runner
产物：eval/blind_report.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from bankagent import nlu, understanding  # noqa: E402

CONTACTS = [{"name": "王秀兰", "alias": "妈"}, {"name": "李娜", "alias": "老婆"},
            {"name": "陈先生", "alias": "房东"}, {"name": "张三", "alias": None}]


def main() -> int:
    cases = json.loads((HERE / "cases" / "blind.json").read_text(encoding="utf-8"))
    detail = []
    passed = 0
    for case in cases:
        ctx = {"user_id": 1, "channel": "app", "recent_entities": {}, "last_intent": None}
        frame = understanding.parse(case["text"], ctx, CONTACTS)
        got = (frame or nlu.parse(case["text"], ctx)).get("name")
        ok = got == case["expect"]
        passed += ok
        if not ok:
            detail.append({"id": case["id"], "text": case["text"],
                           "expect": case["expect"], "got": got})
    report = {"total": len(cases), "passed": passed, "detail": detail}
    (HERE / "blind_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    print(f"盲测集 {passed}/{len(cases)}")
    for item in detail:
        print(f"  {item['id']} {item['text']}  期望={item['expect']} 实际={item['got']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
