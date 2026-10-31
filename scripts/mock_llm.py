"""本地模拟的 OpenAI 兼容服务（仅用于离线验证 check_llm.ps1 的链路，不联网）。

按请求内容返回固定的意图/计划 JSON：
- 含「诗」→ unknown + out_of_scope
- 含「行长」→ 转账 50000 元
- 其他 → 转账 500 元给妈
规划请求（system 含「规划器」）返回两节点 DAG。
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _intent_json(user_text: str) -> dict:
    if "诗" in user_text:
        return {"name": "unknown", "slots": {}, "missing": [], "ambiguity": ["out_of_scope"],
                "spans": {}, "confidence": 0.2}
    amount = 50000
    m = re.search(r"(\d{3,6})", user_text)
    if m:
        amount = int(m.group(1)) * 100
    return {"name": "transfer", "slots": {"amount": amount, "payee": "妈"},
            "missing": [], "ambiguity": [], "spans": {"amount": m.group(1) if m else "500"},
            "confidence": 0.9}


def _plan_json(user_text: str, amount: int = 50000) -> dict:
    return {
        "plan_id": "pl_mock", "goal": "转账", "intent": "transfer",
        "nodes": [
            {"id": "n1", "tool": "get_contacts", "args": {"alias": "妈"},
             "provenance": {"alias": "user"}, "depends_on": [], "risk_hint": "green"},
            {"id": "n2", "tool": "create_transfer",
             "args": {"contact_id": "$n1.contact_id", "amount": amount, "payee": "妈"},
             "provenance": {"contact_id": "tool", "amount": "user"}, "depends_on": ["n1"],
             "risk_hint": "yellow"},
        ],
        "confirm_points": ["n2"],
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        messages = payload.get("messages") or []
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        if "意图解析器" in system:
            content = json.dumps(_intent_json(user), ensure_ascii=False)
        elif "规划器" in system:
            try:
                ctx = json.loads(user)
                amt = int(((ctx.get("intent") or {}).get("slots") or {}).get("amount") or 50000)
            except Exception:
                amt = 50000
            content = json.dumps(_plan_json(user, amt), ensure_ascii=False)
        else:
            content = json.dumps({"ok": True})
        body = {"model": "mock-chat", "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": 64}}
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8199)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock LLM on http://127.0.0.1:{args.port}/v1")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
