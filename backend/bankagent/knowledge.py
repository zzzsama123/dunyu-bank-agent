"""知识库：带来源、生效日期、版本、审批发布与过期下架。

检索：关键词 + 可选 BGE 语义相似度（便携版）；答案附来源/生效/版本/编号。
版本：draft → approved → published，发布时同条目旧版本自动 retired。
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

from . import config, semantic

_DATA = Path(__file__).with_name("data") / "knowledge.json"
_POLICY_HINT = re.compile(
    r"(政策|规定|规则|流程|材料|生效|出处|标准|怎么|如何|需要什么|年费标准|管理费|风险等级|跨境|补卡)"
)
_PERSONAL = re.compile(r"(我的|我.*(额度|限额)|还剩|还有)")
_VEC_CACHE: dict[tuple, list] = {}


@lru_cache(maxsize=1)
def entries() -> list[dict]:
    try:
        data = json.loads(_DATA.read_text(encoding="utf-8"))
        return [item for item in data if item.get("id") and item.get("answer")]
    except (OSError, ValueError):
        return []


def _seed_rows() -> list[dict]:
    rows = []
    now = config.now_iso()
    for item in entries():
        rows.append({
            "entry_id": item["id"], "version": item.get("version") or "v1",
            "title": item["title"], "content": item["answer"],
            "keywords": json.dumps(item.get("keywords") or [], ensure_ascii=False),
            "source": item.get("source"),
            "effective_from": item.get("effective_from"), "effective_to": item.get("effective_to"),
            "status": "published", "created_at": now, "approved_at": now, "published_at": now,
        })
    return rows


def ensure_seed(conn) -> None:
    ensure_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_versions'"
    ).fetchone()
    if not ensure_table:
        return
    count = int(conn.execute("SELECT COUNT(*) FROM knowledge_versions").fetchone()[0])
    if count:
        return
    for row in _seed_rows():
        conn.execute(
            "INSERT INTO knowledge_versions(entry_id,version,title,content,keywords,source,"
            "effective_from,effective_to,status,created_at,approved_at,published_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["entry_id"], row["version"], row["title"], row["content"], row["keywords"],
             row["source"], row["effective_from"], row["effective_to"], row["status"],
             row["created_at"], row["approved_at"], row["published_at"]),
        )


def _db_rows(conn, published_only: bool = False) -> list[dict]:
    ensure_seed(conn)
    sql = ("SELECT * FROM knowledge_versions" +
           (" WHERE status='published'" if published_only else "") +
           " ORDER BY entry_id, id DESC")
    return [dict(row) for row in conn.execute(sql).fetchall()]


def _is_effective(item: dict, today: date) -> bool:
    start = str(item.get("effective_from") or "")
    end = str(item.get("effective_to") or "")
    try:
        if start and date.fromisoformat(start) > today:
            return False
        if end and date.fromisoformat(end) < today:
            return False
    except ValueError:
        return False
    return True


def _keyword_score(text: str, item: dict) -> float:
    score = 0.0
    keywords = item.get("keywords") or []
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except ValueError:
            keywords = []
    for keyword in keywords:
        if str(keyword) in text:
            score += 2.0
    title = str(item.get("title") or "")
    grams_a = {text[i:i + 2] for i in range(max(0, len(text) - 1))}
    grams_b = {title[i:i + 2] for i in range(max(0, len(title) - 1))}
    if grams_a and grams_b:
        score += len(grams_a & grams_b) / len(grams_a | grams_b)
    return score


def _vector_score(text: str, items: list[dict]) -> list[float]:
    if not items or not semantic.available():
        return [0.0] * len(items)
    try:
        vectors = []
        for item in items:
            key = (item.get("id") or item.get("entry_id"), str(item.get("content"))[:80])
            vec = _VEC_CACHE.get(key)
            if vec is None:
                vec = semantic.embed_texts([
                    str(item.get("title") or "") + " " + str(item.get("content") or "")
                ])[0]
                _VEC_CACHE[key] = vec
            vectors.append(vec)
        query = semantic.embed_texts([text])[0]
        qnorm = math.sqrt(sum(v * v for v in query)) or 1.0
        out = []
        for vec in vectors:
            vnorm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append(sum(a * b for a, b in zip(query, vec)) / (qnorm * vnorm))
        return out
    except Exception:  # noqa: BLE001
        return [0.0] * len(items)


def _rank(text: str, items: list[dict]) -> list[float]:
    vector = _vector_score(text, items)
    return [_keyword_score(text, item) + max(0.0, vector[i] - 0.35) * 6.0
            for i, item in enumerate(items)]


def answer(text: str, today: date | None = None, conn=None) -> str | None:
    clean = re.sub(r"\s+", "", str(text or ""))
    if not clean or len(clean) > 60:
        return None
    if not _POLICY_HINT.search(clean) or _PERSONAL.search(clean):
        return None
    today = today or config.now_dt().date()
    if conn is not None:
        all_rows = _db_rows(conn)
        active = [item for item in all_rows
                  if item.get("status") == "published" and _is_effective(item, today)]
        expired = [item for item in all_rows
                   if item.get("status") in ("retired", "expired")
                   or (item.get("status") == "published" and not _is_effective(item, today))]
    else:
        rows = [{**item, "content": item.get("answer")} for item in entries()]
        active = [item for item in rows if _is_effective(item, today)]
        expired = [item for item in rows if not _is_effective(item, today)]
    if not active:
        return None
    scores = _rank(clean, active)
    best_index = max(range(len(active)), key=lambda i: scores[i])
    if scores[best_index] < 2.0:
        if expired:
            stale_scores = _rank(clean, expired)
            stale_index = max(range(len(expired)), key=lambda i: stale_scores[i])
            if stale_scores[stale_index] >= 3.0:
                stale = expired[stale_index]
                return (f"「{stale['title']}」已于 {stale.get('effective_to')} 失效，"
                        f"暂无现行有效版本，建议转人工确认最新要求。")
        return None
    best = active[best_index]
    return (f"{best.get('content')}\n"
            f"来源：{best.get('source') or '—'} ｜ 生效日期：{best.get('effective_from')} ｜ "
            f"版本：{best.get('version')} ｜ 知识编号：{best.get('entry_id')}")


def expire_due(conn, today: date | None = None) -> int:
    ensure_seed(conn)
    today = today or config.now_dt().date()
    cur = conn.execute(
        "UPDATE knowledge_versions SET status='retired' "
        "WHERE status='published' AND effective_to IS NOT NULL AND effective_to < ?",
        (today.isoformat(),),
    )
    return int(cur.rowcount or 0)


def draft(conn, entry_id: str, content: str, version: str = "", source: str = "",
          effective_from: str = "", effective_to: str = "", title: str = "",
          keywords=None) -> dict:
    ensure_seed(conn)
    entry_id = (entry_id or "").strip()
    base = None
    if entry_id:
        base = conn.execute(
            "SELECT * FROM knowledge_versions WHERE entry_id=? ORDER BY id DESC LIMIT 1",
            (entry_id,),
        ).fetchone()
    if not base:
        if not entry_id:
            entry_id = "kb_" + config.now_iso().replace(":", "").replace("-", "")[:14]
        if not title or not content:
            return {"ok": False, "error": "E_PARAM", "message": "新建知识需要 title 和 content"}
        if keywords is None:
            kw = []
        elif isinstance(keywords, str):
            kw = [k.strip() for k in re.split(r"[,，]", keywords) if k.strip()]
        else:
            kw = [str(k) for k in keywords]
        version = version or "v1"
        cur = conn.execute(
            "INSERT INTO knowledge_versions(entry_id,version,title,content,keywords,source,"
            "effective_from,effective_to,status,created_at) VALUES(?,?,?,?,?,?,?,?,'draft',?)",
            (entry_id, version, title, content, json.dumps(kw, ensure_ascii=False), source,
             effective_from, effective_to, config.now_iso()),
        )
        return {"ok": True, "id": int(cur.lastrowid), "entry_id": entry_id, "version": version,
                "created": True}
    version = version or ("v" + str(int(str(base["version"]).lstrip("v") or 1) + 1))
    cur = conn.execute(
        "INSERT INTO knowledge_versions(entry_id,version,title,content,keywords,source,"
        "effective_from,effective_to,status,created_at) VALUES(?,?,?,?,?,?,?,?,'draft',?)",
        (entry_id, version, title or base["title"], content or base["content"], base["keywords"],
         source or base["source"], effective_from or base["effective_from"],
         effective_to if effective_to else base["effective_to"], config.now_iso()),
    )
    return {"ok": True, "id": int(cur.lastrowid), "entry_id": entry_id, "version": version}


def approve(conn, version_id: int) -> dict:
    row = conn.execute("SELECT * FROM knowledge_versions WHERE id=?", (version_id,)).fetchone()
    if not row or row["status"] != "draft":
        return {"ok": False, "error": "E_STATE", "message": "只有草稿可以审批"}
    conn.execute("UPDATE knowledge_versions SET status='approved', approved_at=? WHERE id=?",
                 (config.now_iso(), version_id))
    return {"ok": True, "id": version_id, "status": "approved"}


def publish(conn, version_id: int) -> dict:
    row = conn.execute("SELECT * FROM knowledge_versions WHERE id=?", (version_id,)).fetchone()
    if not row or row["status"] != "approved":
        return {"ok": False, "error": "E_STATE", "message": "只有已审批版本可以发布"}
    conn.execute(
        "UPDATE knowledge_versions SET status='retired' WHERE entry_id=? AND status='published'",
        (row["entry_id"],),
    )
    conn.execute("UPDATE knowledge_versions SET status='published', published_at=? WHERE id=?",
                 (config.now_iso(), version_id))
    return {"ok": True, "id": version_id, "entry_id": row["entry_id"], "status": "published"}


def status(today: date | None = None, conn=None) -> list[dict]:
    today = today or config.now_dt().date()
    if conn is not None and conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_versions'"
    ).fetchone():
        ensure_seed(conn)
        out = []
        for item in _db_rows(conn):
            state = item["status"]
            if state == "published" and not _is_effective(item, today):
                state = "expired"
            out.append({
                "id": item["id"], "entry_id": item["entry_id"], "title": item["title"],
                "version": item["version"], "status": state,
                "effective_from": item["effective_from"], "effective_to": item["effective_to"],
                "source": item["source"],
            })
        return out
    out = []
    for item in entries():
        out.append({
            "id": item["id"], "entry_id": item["id"], "title": item["title"],
            "effective_from": item.get("effective_from"), "effective_to": item.get("effective_to"),
            "version": item.get("version"),
            "status": "active" if _is_effective(item, today) else "expired",
        })
    return out
