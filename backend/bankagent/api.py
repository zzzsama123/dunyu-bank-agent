"""HTTP 服务：对话 API + SSE 事件流 + 审计回放 + 静态前端。

启动：cd backend && python -m bankagent.api
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import threading
import time
import traceback
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import asr, bankcore, config, db, demo_reel, identity, knowledge, llm, metrics, orchestrator, overview, proactive, sandbox, seed, selftest, semantic, trust, xlsx

EVENTS: "OrderedDict[str, list]" = OrderedDict()
EVENTS_LOCK = threading.Lock()
MAX_TRACES = 300


def _remember(trace_id: str, events: list) -> None:
    with EVENTS_LOCK:
        EVENTS[trace_id] = events
        while len(EVENTS) > MAX_TRACES:
            EVENTS.popitem(last=False)


def _json(handler: BaseHTTPRequestHandler, obj, status: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


_EXPORT_HEADERS = ["日期", "类型", "方向", "金额(元)", "对手方", "分类", "备注", "单号"]
_TYPE_CN = {"payment": "消费", "transfer": "转账", "refund": "退款", "subscription": "订阅"}


def _export_rows(rng: str, party: str):
    start, end, _ = config.range_bounds(rng)
    conn = db.connect()
    try:
        rows = bankcore.list_transactions(conn, config.DEFAULT_USER_ID,
                                          start=start, end=end, limit=1000)
        if party:
            names = {party}
            for item in bankcore.list_contacts(conn, config.DEFAULT_USER_ID):
                if party in (item.get("alias"), item.get("name")):
                    names.add(str(item.get("name")))
                    names.add(str(item.get("alias") or ""))
            rows = [r for r in rows if str(r.get("counterparty") or "") in names]
    finally:
        conn.close()
    return start, rows


def _export_matrix(rows):
    matrix = []
    for row in rows:
        matrix.append([
            str(row.get("created_at") or "")[:10],
            _TYPE_CN.get(str(row.get("type") or ""), str(row.get("type") or "")),
            "收入" if row.get("direction") == "in" else "支出",
            round(int(row.get("amount_cents") or 0) / 100, 2),
            row.get("counterparty") or "",
            row.get("category") or "",
            row.get("memo") or "",
            row.get("txn_no") or "",
        ])
    return matrix


class Handler(BaseHTTPRequestHandler):
    server_version = "BankAgentDemo/1.0"

    def log_message(self, fmt, *args):  # 降噪
        if os.environ.get("BANK_HTTP_LOG") == "1":
            super().log_message(fmt, *args)

    # ---------------- 路由 ----------------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/health":
                return _json(self, {"ok": True, "db": config.DB_PATH, "version": config.VERSION,
                                    "brain": llm.status(), "asr": asr.status(),
                                    "semantic": semantic.status()})
            if path == "/v1/identity":
                conn = db.connect()
                try:
                    return _json(self, identity.profile(conn))
                finally:
                    conn.close()
            if path == "/v1/tickets/detail":
                params = parse_qs(urlparse(self.path).query)
                ticket_no = (params.get("ticket_no") or [""])[0]
                conn = db.connect()
                try:
                    row = conn.execute("SELECT * FROM support_tickets WHERE ticket_no=?",
                                       (ticket_no,)).fetchone()
                finally:
                    conn.close()
                if not row:
                    return _json(self, {"code": "E_NOT_FOUND", "message": "工单不存在"}, 404)
                return _json(self, dict(row))
            if path == "/v1/flags":
                conn = db.connect()
                try:
                    rows = conn.execute("SELECT * FROM feature_flags ORDER BY name").fetchall()
                finally:
                    conn.close()
                return _json(self, {"flags": [dict(row) for row in rows]})
            if path == "/v1/ab/assign":
                params = parse_qs(urlparse(self.path).query)
                sid = (params.get("session_id") or ["s_demo"])[0]
                bucket = "A" if int(hashlib.sha256(sid.encode()).hexdigest()[:8], 16) % 2 == 0 else "B"
                return _json(self, {"session_id": sid, "experiment": "reply_style", "bucket": bucket})
            if path == "/v1/llm/config":
                return _json(self, llm.status())
            if path == "/v1/demo/reel":
                return _json(self, demo_reel.build())
            if path == "/v1/proactive":
                conn = db.connect()
                try:
                    return _json(self, proactive.build(conn, config.DEFAULT_USER_ID))
                finally:
                    conn.close()
            if path == "/v1/metrics":
                conn = db.connect()
                try:
                    return _json(self, metrics.build(conn))
                finally:
                    conn.close()
            if path == "/v1/knowledge":
                conn = db.connect()
                try:
                    knowledge.expire_due(conn)
                    return _json(self, {"entries": knowledge.status(conn=conn)})
                finally:
                    conn.close()
            if path == "/v1/tickets":
                conn = db.connect()
                try:
                    rows = conn.execute(
                        "SELECT ticket_no, summary, status, created_at, trace_id FROM support_tickets "
                        "ORDER BY id DESC LIMIT 50").fetchall()
                    return _json(self, {"tickets": [dict(row) for row in rows]})
                finally:
                    conn.close()
            if path == "/v1/export/options":
                conn = db.connect()
                try:
                    rows = conn.execute(
                        "SELECT DISTINCT counterparty FROM transactions WHERE user_id=? "
                        "AND counterparty IS NOT NULL AND counterparty != '' ORDER BY counterparty",
                        (config.DEFAULT_USER_ID,),
                    ).fetchall()
                    names = {str(r["counterparty"]) for r in rows}
                    names.update(
                        str(c.get("name"))
                        for c in bankcore.list_contacts(conn, config.DEFAULT_USER_ID)
                    )
                finally:
                    conn.close()
                return _json(self, {
                    "ranges": [
                        {"key": "this_month", "label": "本月"},
                        {"key": "last_month", "label": "上月"},
                        {"key": "last_3m", "label": "近 3 个月"},
                        {"key": "this_year", "label": "今年"},
                        {"key": "all", "label": "全部"},
                    ],
                    "counterparties": sorted(n for n in names if n),
                })
            if path == "/v1/export/transactions.xlsx":
                params = parse_qs(urlparse(self.path).query)
                rng = (params.get("range") or ["this_month"])[0]
                party = (params.get("counterparty") or [""])[0].strip()
                start, rows = _export_rows(rng, party)
                if not rows:
                    return _json(self, {"code": "E_EMPTY_EXPORT",
                                        "message": "该筛选条件下没有流水，未生成空文件。"}, 404)
                data = xlsx.build(
                    _EXPORT_HEADERS,
                    _export_matrix(rows),
                    widths=[13, 9, 7, 12, 16, 10, 20, 26],
                )
                self.send_response(200)
                self.send_header("Content-Type",
                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="transactions_{start[:7]}.xlsx"')
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/v1/export/transactions.csv":
                params = parse_qs(urlparse(self.path).query)
                rng = (params.get("range") or ["this_month"])[0]
                party = (params.get("counterparty") or [""])[0].strip()
                start, rows = _export_rows(rng, party)
                if not rows:
                    return _json(self, {"code": "E_EMPTY_EXPORT",
                                        "message": "该筛选条件下没有流水，未生成空文件。"}, 404)
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(["日期", "类型", "方向", "金额(元)", "对手方", "分类", "备注", "单号"])
                for row in rows:
                    writer.writerow([
                        str(row.get("created_at") or "")[:10],
                        row.get("type") or "",
                        "收入" if row.get("direction") == "in" else "支出",
                        f"{(int(row.get('amount_cents') or 0)) / 100:.2f}",
                        row.get("counterparty") or "",
                        row.get("category") or "",
                        row.get("memo") or "",
                        row.get("txn_no") or "",
                    ])
                data = ("\ufeff" + output.getvalue()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="transactions_{start[:7]}.csv"')
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/v1/overview":
                conn = db.connect()
                try:
                    return _json(self, overview.build(conn, config.DEFAULT_USER_ID))
                finally:
                    conn.close()
            if path == "/v1/audit/traces":
                params = parse_qs(urlparse(self.path).query)
                limit = (params.get("limit") or ["200"])[0]
                offset = (params.get("offset") or ["0"])[0]
                query = (params.get("q") or [""])[0]
                conn = db.connect()
                try:
                    data = trust.AuditLog(conn).list_traces(limit, offset, query)
                finally:
                    conn.close()
                return _json(self, data)
            if path.startswith("/v1/audit/"):
                trace_id = path.rsplit("/", 1)[-1]
                conn = db.connect()
                try:
                    return _json(self, orchestrator.get_audit(conn, trace_id))
                finally:
                    conn.close()
            if path.startswith("/v1/state/"):
                session_id = path.rsplit("/", 1)[-1]
                conn = db.connect()
                try:
                    return _json(self, orchestrator.get_state(conn, session_id))
                finally:
                    conn.close()
            if path.startswith("/v1/stream/"):
                return self._sse(path.rsplit("/", 1)[-1])
            if path in ("/", "/index.html"):
                return self._static("index.html")
            # SPA 回落：/audit/tr_xxx 交给前端 hash 路由处理，便于直接分享审计链接
            if path.startswith("/audit/"):
                return self._static("index.html")
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            return self._static(path.lstrip("/"))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return _json(self, {"code": "E_INTERNAL", "message": "internal error"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        body = _read_json(self)
        conn = db.connect()
        try:
            if path == "/v1/llm/config":
                res = llm.configure(
                    api_key=body.get("api_key"),
                    base_url=body.get("base_url"),
                    model=body.get("model"),
                    enabled=body.get("enabled"),
                    clear=bool(body.get("clear")))
                return _json(self, res, 200 if res.get("ok", True) else 400)
            if path == "/v1/identity/bind-device":
                try:
                    return _json(self, identity.bind_session(
                        conn, str(body.get("session_id") or "s_demo"),
                        str(body.get("device_id") or "")))
                except identity.IdentityError as exc:
                    return _json(self, {"code": exc.code, "message": exc.message}, 409)
            if path == "/v1/identity/authorize":
                try:
                    data = identity.authorize(
                        conn, config.DEFAULT_USER_ID, str(body.get("delegate_name") or ""),
                        str(body.get("relationship") or ""), str(body.get("scope") or "query"),
                        int(body.get("days") or 30))
                except identity.IdentityError as exc:
                    return _json(self, {"code": exc.code, "message": exc.message}, 400)
                return _json(self, data)
            if path == "/v1/identity/revoke":
                try:
                    return _json(self, identity.revoke(conn, config.DEFAULT_USER_ID,
                                                       int(body.get("id") or 0)))
                except identity.IdentityError as exc:
                    return _json(self, {"code": exc.code, "message": exc.message}, 404)
            if path == "/v1/knowledge/draft":
                conn2 = db.connect()
                try:
                    res = knowledge.draft(
                        conn2, str(body.get("entry_id") or ""), str(body.get("content") or ""),
                        str(body.get("version") or ""), str(body.get("source") or ""),
                        str(body.get("effective_from") or ""), str(body.get("effective_to") or ""),
                        title=str(body.get("title") or ""), keywords=body.get("keywords"))
                    return _json(self, res, 200 if res.get("ok") else 400)
                finally:
                    conn2.close()
            if path == "/v1/knowledge/approve":
                conn2 = db.connect()
                try:
                    res = knowledge.approve(conn2, int(body.get("id") or 0))
                    return _json(self, res, 200 if res.get("ok") else 400)
                finally:
                    conn2.close()
            if path == "/v1/knowledge/publish":
                conn2 = db.connect()
                try:
                    res = knowledge.publish(conn2, int(body.get("id") or 0))
                    return _json(self, res, 200 if res.get("ok") else 400)
                finally:
                    conn2.close()
            if path == "/v1/knowledge/expire":
                conn2 = db.connect()
                try:
                    return _json(self, {"retired": knowledge.expire_due(conn2)})
                finally:
                    conn2.close()
            if path == "/v1/tickets/update":
                allowed = ("open", "in_progress", "resolved")
                status = str(body.get("status") or "")
                if status not in allowed:
                    return _json(self, {"code": "E_PARAM", "message": "状态不合法"}, 400)
                cur = conn.execute(
                    "UPDATE support_tickets SET status=? WHERE ticket_no=?",
                    (status, str(body.get("ticket_no") or "")),
                )
                return _json(self, {"ok": bool(cur.rowcount), "status": status})
            if path == "/v1/flags":
                name = str(body.get("name") or "").strip()
                if not name:
                    return _json(self, {"code": "E_PARAM", "message": "name 不能为空"}, 400)
                enabled = 1 if body.get("enabled") else 0
                rollout = max(0, min(100, int(body.get("rollout") or 0)))
                conn.execute(
                    "INSERT INTO feature_flags(name,enabled,rollout,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled, rollout=excluded.rollout, "
                    "updated_at=excluded.updated_at",
                    (name, enabled, rollout, config.now_iso()),
                )
                return _json(self, {"ok": True, "name": name, "enabled": bool(enabled), "rollout": rollout})
            if path == "/v1/security/selftest":
                return _json(self, selftest.run(str(body.get("case") or "")))
            if path == "/v1/feedback":
                rating = str(body.get("rating") or "").strip()
                if rating not in ("up", "down"):
                    return _json(self, {"code": "E_PARAM", "message": "rating 必须是 up/down"}, 400)
                conn.execute(
                    "INSERT INTO feedback(trace_id,session_id,user_id,rating,reason,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (str(body.get("trace_id") or ""), str(body.get("session_id") or ""),
                     config.DEFAULT_USER_ID, rating, str(body.get("reason") or "")[:200],
                     config.now_iso()),
                )
                return _json(self, {"ok": True})
            if path == "/v1/chat":
                text = (body.get("text") or "").strip()
                if not text:
                    return _json(self, {"code": "E_PARAM", "message": "text 不能为空"}, 400)
                if body.get("user_id") not in (None, config.DEFAULT_USER_ID):
                    return _json(self, {"code": "E_USER_BIND", "message": "会话与客户号强绑定，禁止跨用户"}, 403)
                session_id = body.get("session_id") or "s_demo"
                device_id = str(body.get("device_id") or self.headers.get("X-Device-Id") or "").strip()
                act_as = str(body.get("act_as") or "").strip()
                requested_brain = str(body.get("brain") or "").strip()
                if requested_brain not in ("", "rules", "llm"):
                    return _json(self, {"code": "E_PARAM", "message": "brain 只能是 rules/llm"}, 400)
                try:
                    res = orchestrator.handle_chat(conn, session_id, text, channel=body.get("channel") or "app",
                                                   device_id=device_id or None, act_as=act_as or None)
                    effective = "llm" if requested_brain == "llm" and llm.enabled() else "rules"
                    res["brain"] = {"requested": requested_brain or "rules", "effective": effective,
                                    "available": bool(llm.enabled()), "has_key": bool(llm.enabled()),
                                    "note": "切换只影响理解层，不影响安全边界"}
                    if requested_brain == "llm" and not llm.enabled():
                        res["brain"]["note"] = "未配置模型 Key，已回退规则模式；安全边界不变"
                except identity.IdentityError as exc:
                    return _json(self, {"code": exc.code, "message": exc.message}, 409)
            elif path == "/v1/asr":
                audio = body.get("audio") or ""
                if not audio:
                    return _json(self, {"code": "E_PARAM", "message": "audio 不能为空"}, 400)
                contacts = []
                for item in bankcore.list_contacts(conn, config.DEFAULT_USER_ID):
                    token = item.get("alias") or item.get("name")
                    if token:
                        contacts.append(str(token))
                try:
                    result = asr.recognize(audio, contacts)
                except ValueError as exc:
                    return _json(self, {"code": "E_PARAM", "message": str(exc)}, 400)
                except Exception as exc:  # noqa: BLE001
                    return _json(self, {"code": "E_ASR", "message": str(exc)}, 503)
                return _json(self, result, 200)
            elif path == "/v1/im/webhook":
                # IM 渠道（模拟企业微信/微信客服回调）：同一后端、channel=im
                text = (body.get("text") or body.get("Content") or "").strip()
                if not text:
                    return _json(self, {"code": "E_PARAM", "message": "text 不能为空"}, 400)
                who = str(body.get("from") or body.get("FromUserName") or "guest")
                res = orchestrator.handle_chat(conn, f"im:{who}", text, channel="im")
                payload = {"reply": res.get("reply"), "status": res.get("status"),
                           "trace_id": res.get("trace_id"), "channel": "im"}
                _remember(res.get("trace_id", ""), res.get("events") or [])
                return _json(self, payload, 200)
            elif path == "/v1/sandbox/query":
                sql = str(body.get("sql") or "")
                try:
                    rows = sandbox.query(sql, limit=int(body.get("limit") or 50))
                except sandbox.SandboxError as exc:
                    return _json(self, {"code": "E_SANDBOX", "message": str(exc)}, 403)
                return _json(self, {"rows": rows, "count": len(rows)}, 200)
            elif path == "/v1/confirm":
                res = orchestrator.handle_confirm(conn, body.get("session_id") or "s_demo",
                                                  body.get("intent_id") or "", body.get("token") or "")
            elif path == "/v1/stepup/verify":
                res = orchestrator.handle_stepup(conn, body.get("session_id") or "s_demo",
                                                 body.get("challenge_id") or "", body.get("code") or "")
            elif path == "/v1/cancel":
                res = orchestrator.handle_cancel(conn, body.get("session_id") or "s_demo",
                                                 body.get("intent_id") or "")
            else:
                return _json(self, {"code": "E_NOT_FOUND", "message": "not found"}, 404)
            _remember(res.get("trace_id", ""), res.get("events") or [])
            status = 200
            if res.get("status") == "error" and (res.get("error") or {}).get("code") == "E_PARAM":
                status = 400
            return _json(self, res, status)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return _json(self, {"code": "E_INTERNAL", "message": str(e)}, 500)
        finally:
            conn.close()

    # ---------------- 静态与 SSE ----------------
    def _static(self, rel: str):
        base = config.FRONTEND_DIR.resolve()
        target = (base / rel).resolve()
        if not str(target).startswith(str(base)) or not target.is_file():
            return _json(self, {"code": "E_NOT_FOUND", "message": rel}, 404)
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
        }.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, trace_id: str):
        with EVENTS_LOCK:
            events = list(EVENTS.get(trace_id) or [])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            for ev in events:
                payload = json.dumps(ev, ensure_ascii=False)
                self.wfile.write(f"event: {ev.get('type','message')}\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.02)
            self.wfile.write(b"event: end\ndata: {}\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def bootstrap() -> None:
    db.init_db()
    llm.load_runtime_config()
    conn = db.connect()
    try:
        info = seed.seed(conn)
        if info.get("seeded"):
            print(f"[boot] 种子数据已灌入：余额 {bankcore.get_balance(conn, 1)/100:.2f} 元，"
                  f"今日已用 {bankcore.get_daily_used(conn, 1)/100:.2f} 元")
    finally:
        conn.close()


def serve(port: int | None = None) -> None:
    bootstrap()
    port = port or int(os.environ.get("BANK_PORT") or 8000)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[boot] 银行 AI 智能体（仿真）已启动：http://127.0.0.1:{port}")
    print(f"[boot] 数据库：{config.DB_PATH}")
    print("[boot] 演示验证码：123456 ｜ 按 Ctrl+C 停止")
    if os.environ.get("BANK_OPEN_BROWSER") == "1":
        import threading
        import webbrowser

        def _open():
            time.sleep(1.2)
            try:
                webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_open, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[stop] bye")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve()
