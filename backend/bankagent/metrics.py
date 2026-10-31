"""运营指标与 badcase 归集。"""

from __future__ import annotations

from . import knowledge


def build(conn) -> dict:
    sessions = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
    traces = int(conn.execute("SELECT COUNT(DISTINCT trace_id) FROM audit_event").fetchone()[0])
    events = int(conn.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0])
    tool_calls = int(conn.execute(
        "SELECT COUNT(*) FROM audit_event WHERE action LIKE 'tool.%'").fetchone()[0])
    feedback_up = int(conn.execute(
        "SELECT COUNT(*) FROM feedback WHERE rating='up'").fetchone()[0])
    feedback_down = int(conn.execute(
        "SELECT COUNT(*) FROM feedback WHERE rating='down'").fetchone()[0])
    badcases = [dict(row) for row in conn.execute(
        "SELECT trace_id, session_id, reason, created_at FROM feedback "
        "WHERE rating='down' ORDER BY id DESC LIMIT 20").fetchall()]
    kb = knowledge.status(conn=conn)
    return {
        "sessions": sessions,
        "traces": traces,
        "events": events,
        "tool_calls": tool_calls,
        "feedback_up": feedback_up,
        "feedback_down": feedback_down,
        "badcases": badcases,
        "knowledge": {
            "total": len(kb),
            "active": sum(1 for item in kb if item["status"] == "published"),
            "expired": sum(1 for item in kb if item["status"] in ("expired", "retired")),
        },
    }
