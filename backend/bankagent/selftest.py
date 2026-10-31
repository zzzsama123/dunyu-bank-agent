"""安全证据自测：在隔离临时库里演示 token/参数/审计链被篡改后的拦截。

绝不触碰当前演示数据库；每次运行新建临时库，结束后删除。
"""

from __future__ import annotations

import json
import os
import tempfile

from . import config, db, orchestrator, seed, trust


def _error_code(res: dict) -> str:
    for event in res.get("events") or []:
        if event.get("type") == "error":
            return str((event.get("data") or {}).get("code") or "")
    return ""


def _token_case(conn) -> dict:
    res = orchestrator.handle_chat(conn, "selftest", "给张三转100")
    if res.get("status") != "awaiting_confirm":
        return {"passed": False, "steps": ["创建待确认请求失败：" + str(res.get("status"))]}
    card = res["pending"]["card"]
    token = str(card["token"])
    tampered = token[:-1] + ("a" if token[-1:] != "a" else "b")
    out = orchestrator.handle_confirm(conn, "selftest", card["intent_id"], tampered)
    code = _error_code(out)
    return {
        "passed": out.get("status") == "error" and code == "E_CONFIRM_TOKEN",
        "expected": "E_CONFIRM_TOKEN",
        "actual": code or out.get("status"),
        "steps": [
            "创建黄色确认卡（临时库）",
            "把 confirm token 最后一位改掉",
            "用篡改后的 token 提交确认",
            f"服务端返回：{code or out.get('status')}",
        ],
    }


def _params_case(conn) -> dict:
    res = orchestrator.handle_chat(conn, "selftest", "给张三转100")
    if res.get("status") != "awaiting_confirm":
        return {"passed": False, "steps": ["创建待确认请求失败：" + str(res.get("status"))]}
    card = res["pending"]["card"]
    row = conn.execute("SELECT card_json, token FROM pending_intents WHERE id=?",
                       (card["intent_id"],)).fetchone()
    params = json.loads(row["card_json"])
    params["amount"] = "9,999.00"
    conn.execute("UPDATE pending_intents SET card_json=? WHERE id=?",
                 (json.dumps(params, ensure_ascii=False), card["intent_id"]))
    out = orchestrator.handle_confirm(conn, "selftest", card["intent_id"], row["token"])
    code = _error_code(out)
    return {
        "passed": out.get("status") == "error" and code == "E_PARAM",
        "expected": "E_PARAM",
        "actual": code or out.get("status"),
        "steps": [
            "创建黄色确认卡（临时库）",
            "把待确认金额从 100.00 改成 9,999.00（保留原 token）",
            "提交确认",
            f"服务端返回：{code or out.get('status')}",
        ],
    }


def _audit_case(conn) -> dict:
    res = orchestrator.handle_chat(conn, "selftest", "查余额")
    trace_id = str(res.get("trace_id") or "")
    log = trust.AuditLog(conn)
    before = log.verify_chain(trace_id)
    conn.execute("UPDATE audit_event SET payload=? WHERE trace_id=? AND seq=2",
                 ('{"text":"tampered"}', trace_id))
    after = log.verify_chain(trace_id)
    return {
        "passed": before and not after,
        "expected": "before=true, after=false",
        "actual": f"before={before}, after={after}",
        "steps": [
            "产生一条完整审计链（临时库）",
            "篡改 seq=2 的 payload",
            "重新校验哈希链",
            f"结果：{after}",
        ],
    }


def run(case: str) -> dict:
    case = str(case or "").strip()
    if case not in ("token", "params", "audit"):
        return {"ok": False, "error": "未知检查项", "case": case}
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = None
    try:
        db.reset_db(path)
        conn = db.connect(path)
        seed.seed(conn)
        result = {"token": _token_case, "params": _params_case, "audit": _audit_case}[case](conn)
        return {"ok": True, "case": case, "temp_db": True, **result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "case": case, "error": str(exc)}
    finally:
        if conn is not None:
            conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
