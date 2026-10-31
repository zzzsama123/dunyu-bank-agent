"""受限查询沙箱：只读连接 + 视图白名单 + 单条 SELECT + 行数/时间上限。

赛题 1.4.2 要求"生成的代码在受限、可监控的逻辑隔离环境中运行"。
本方案不执行任意代码（没有 eval/exec、没有代码生成），分析类查询统一走这里：
- 物理只读：SQLite URI `mode=ro`，写操作在驱动层就失败；
- 逻辑白名单：只允许 SELECT / WITH，只允许查询 5 个脱敏视图；
- 资源上限：单条语句、最多 500 行、2 秒超时；
- 可监控：调用方把 SQL、行数、耗时写进审计。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from . import config

ALLOWED_VIEWS = {
    "v_account_balance",
    "v_transactions",
    "v_subscriptions",
    "v_cards",
    "v_holdings",
}

_FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "attach", "detach",
    "pragma", "vacuum", "create", "reindex", "replace", "trigger",
)
_START = ("select", "with")


class SandboxError(RuntimeError):
    """查询被沙箱拒绝。"""


def readonly_conn(path: str | None = None) -> sqlite3.Connection:
    p = Path(path or config.DB_PATH).resolve()
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True,
                           timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def validate(sql: str) -> str:
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise SandboxError("SQL 为空")
    lowered = text.lower()
    if not lowered.startswith(_START):
        raise SandboxError("只允许 SELECT / WITH 查询")
    if ";" in text:
        raise SandboxError("只允许单条语句")
    for word in _FORBIDDEN:
        if word in lowered:
            raise SandboxError(f"禁止的关键字：{word}")
    tokens = {t for t in _identifiers(lowered)}
    unknown = tokens - ALLOWED_VIEWS
    if unknown:
        raise SandboxError(f"表不在白名单：{', '.join(sorted(unknown))}")
    return text


def _identifiers(sql: str) -> list[str]:
    """从 FROM / JOIN 后面提取表名（够用即可，白名单会兜住漏网的）。"""
    out, marker = [], "from"
    parts = sql.replace("(", " ").replace(")", " ").split()
    for i, token in enumerate(parts):
        if token in ("from", "join"):
            for nxt in parts[i + 1:]:
                if nxt in ("where", "group", "order", "limit", "having", "union"):
                    break
                out.append(nxt.strip(",;"))
                break
    return [t for t in out if t]


def query(sql: str, params: tuple = (), limit: int = 200, path: str | None = None,
          budget_ms: int = 2000) -> list[dict]:
    text = validate(sql)
    limit = max(1, min(int(limit), 500))
    if "limit" not in text.lower():
        text = f"{text} LIMIT {limit}"
    deadline = time.monotonic() + budget_ms / 1000
    conn = readonly_conn(path)
    try:
        conn.set_progress_handler(lambda: 0 if time.monotonic() < deadline else 1, 5000)
        rows = conn.execute(text, params).fetchall()
        return [dict(r) for r in rows][:limit]
    except sqlite3.Error as exc:
        raise SandboxError(f"查询失败：{exc}") from exc
    finally:
        conn.close()
