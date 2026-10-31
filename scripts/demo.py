"""五幕剧命令行演示（不需要浏览器）。

用法：cd bankagent && python scripts/demo.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
DB = os.environ.get("BANK_DB") or str(ROOT / "backend" / "data" / "demo.db")
os.environ["BANK_DB"] = DB

from bankagent import bankcore, config, db, orchestrator, seed  # noqa: E402

ICON = {"thinking": "…", "plan": "[plan]", "policy": "[policy]", "confirm": "[confirm]", "stepup": "[stepup]",
        "step": "OK", "result": "AI", "error": "ERR"}


def banner(text: str) -> None:
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


def render(res: dict) -> None:
    for ev in res.get("events") or []:
        t, d = ev["type"], ev["data"]
        if t == "plan":
            print(f"  [plan]  计划 {len(d['plan']['nodes'])} 步：{d['summary']}")
        elif t == "policy":
            print(f"  [policy]  裁决：{d['level']} / {d['rule_id']} — {d.get('reason','')}")
        elif t == "confirm":
            card = d["card"]
            who = card.get("payee_name") or next(
                (v for k, v in (card.get("fields") or []) if k in ("关联人", "收款人")), "")
            print(f"  [confirm] 待确认：{card.get('amount','')} {who}  (intent {d['intent_id']})")
        elif t == "stepup":
            print(f"  [stepup] 强验证：{d.get('hint','')}  (challenge {d['challenge_id']})")
        elif t == "step":
            print(f"  OK  {d.get('label') or d.get('tool')}  {d.get('ms', 0)}ms")
        elif t == "result":
            print(f"  AI {d.get('reply')}")
        elif t == "error":
            print(f"  ERR  [{d.get('code')}] {d.get('message')}")
    print(f"  → 状态：{res.get('status')}   trace={res.get('trace_id')}")


def turn(conn, session, text, auto=True, code=config.DEMO_STEPUP_CODE) -> dict:
    print(f"\nUser {text}")
    res = orchestrator.handle_chat(conn, session, text)
    render(res)
    if auto and res.get("status") == "awaiting_confirm":
        card = (res.get("pending") or {}).get("card") or {}
        print("  [click]  （点击确认）")
        time.sleep(0.2)
        res = orchestrator.handle_confirm(conn, session, res["pending"]["intent_id"], card.get("token", ""))
        render(res)
    elif auto and res.get("status") == "awaiting_stepup":
        print(f"  [code]  （输入验证码 {code}）")
        time.sleep(0.2)
        res = orchestrator.handle_stepup(conn, session, res["pending"]["challenge_id"], code)
        render(res)
    return res


def main() -> None:
    db.reset_db(DB)
    conn = db.connect(DB)
    info = seed.seed(conn)
    print(f"已重置演示库：{DB}")
    print(f"初始余额 {bankcore.get_balance(conn, 1)/100:,.2f} 元，今日已用 {bankcore.get_daily_used(conn, 1)/100:,.2f} 元")

    session = "s_demo_" + str(int(time.time()))

    banner("第一幕｜“我这个月钱都去哪了” —— 模糊表达 → 绿色直通 → 分类 + 异常")
    turn(conn, session, "我这个月钱都去哪了")
    turn(conn, session, "有没有不对劲的")

    banner("第二幕｜“我都被扣了什么会员” —— 账单 → 订阅的跨场景识别")
    turn(conn, session, "我都被扣了什么会员")

    banner("第三幕｜“把没用的都退了” —— 批量不可逆动作必须克制")
    turn(conn, session, "把没用的都退了")

    banner("第四幕｜“再给我妈转 800” —— 日累计 500+800=1,300 越线，红色强验证")
    act4 = turn(conn, session, "再给我妈转 800")

    banner("第五幕｜“我老婆下个月生日” —— 先给方案，确认后再锁预算 + 建长期计划")
    act5 = turn(conn, session, "我老婆下个月生日，帮我准备一下，别超过 1000")
    if act5.get("status") == "done" and "按这个来" in (act5.get("reply") or ""):
        turn(conn, session, "就按这个来")

    banner("收尾｜“算了” —— Saga 逆序补偿（取消计划 + 解冻 + 冲正）")
    turn(conn, session, "算了，先别弄了")

    banner("审计回放（第四幕那笔红色转账的决策链）")
    trace = act4.get("trace_id") or conn.execute(
        "SELECT trace_id FROM audit_event ORDER BY id DESC LIMIT 1").fetchone()["trace_id"]
    audit = orchestrator.get_audit(conn, trace)
    for ev in audit["events"][:12]:
        print(f"  {ev['seq']:>2} {ev['actor']:<8} {ev['action']:<22} {ev.get('decision','')}")
    print(f"  哈希链校验：{'OK' if audit['chain_ok'] else 'FAILED'}")

    print("\n最终余额：", f"{bankcore.get_balance(conn, 1)/100:,.2f} 元")
    conn.close()


if __name__ == "__main__":
    main()
