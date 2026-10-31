"""SQLite 连接、建表、视图。金额单位一律为"分"（INTEGER）。"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT, phone TEXT,
  risk_level TEXT DEFAULT 'normal', risk_score INTEGER DEFAULT 10,
  kyc_status TEXT DEFAULT 'passed', birthday TEXT,
  customer_no TEXT, kyc_level TEXT DEFAULT 'L2', realname_status TEXT DEFAULT 'verified',
  device_id TEXT);

CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY, user_id INTEGER, type TEXT,
  opening_balance INTEGER NOT NULL DEFAULT 0, frozen INTEGER NOT NULL DEFAULT 0,
  currency TEXT DEFAULT 'CNY', created_at TEXT);

CREATE TABLE IF NOT EXISTS ledger_entry(
  id INTEGER PRIMARY KEY, txn_id INTEGER NOT NULL, account_id INTEGER NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('D','C')),
  amount INTEGER NOT NULL CHECK(amount > 0), created_at TEXT);

CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY, txn_no TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL,
  type TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT DEFAULT 'CNY',
  status TEXT NOT NULL, idempotency_key TEXT UNIQUE NOT NULL, reversed_by INTEGER,
  memo TEXT, category TEXT, counterparty TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, alias TEXT, phone TEXT,
  card_tail TEXT, is_new INTEGER DEFAULT 0, phone_full TEXT);

CREATE TABLE IF NOT EXISTS points(
  user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, expiring INTEGER DEFAULT 0,
  expire_at TEXT);

CREATE TABLE IF NOT EXISTS cards(
  id INTEGER PRIMARY KEY, user_id INTEGER, card_tail TEXT, status TEXT DEFAULT 'active',
  daily_limit INTEGER, used INTEGER DEFAULT 0, frozen INTEGER DEFAULT 0,
  bill_cents INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS wealth_products(
  id INTEGER PRIMARY KEY, name TEXT, rate REAL, term_days INTEGER,
  risk_level TEXT, min_amount INTEGER);

CREATE TABLE IF NOT EXISTS holdings(
  id INTEGER PRIMARY KEY, user_id INTEGER, product_id INTEGER, amount INTEGER, purchased_at TEXT);

CREATE TABLE IF NOT EXISTS subscriptions(
  id INTEGER PRIMARY KEY, user_id INTEGER, merchant TEXT, amount INTEGER,
  cycle_days INTEGER, next_charge_at TEXT, status TEXT DEFAULT 'active', last_used_at TEXT);

CREATE TABLE IF NOT EXISTS standing_plans(
  id INTEGER PRIMARY KEY, user_id INTEGER, goal TEXT, trigger_rule TEXT,
  actions TEXT, budget INTEGER, status TEXT DEFAULT 'active',
  next_run_at TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS audit_event(
  id INTEGER PRIMARY KEY, trace_id TEXT NOT NULL, plan_id TEXT, node_id TEXT,
  seq INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, payload TEXT,
  decision TEXT, prev_hash TEXT, hash TEXT, ts TEXT NOT NULL,
  UNIQUE(trace_id, seq));

CREATE TABLE IF NOT EXISTS idempotency(
  key TEXT PRIMARY KEY, txn_id INTEGER, status TEXT, response_hash TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS daily_quota(
  user_id INTEGER, biz_date TEXT, used INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, biz_date));

CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY, user_id INTEGER, channel TEXT DEFAULT 'app',
  state TEXT DEFAULT 'NORMAL', recent_entities TEXT DEFAULT '{}', updated_at TEXT,
  device_id TEXT);

CREATE TABLE IF NOT EXISTS freezes(
  id INTEGER PRIMARY KEY, user_id INTEGER, account_id INTEGER, amount INTEGER,
  status TEXT DEFAULT 'active', until TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS reminders(
  id INTEGER PRIMARY KEY, user_id INTEGER, at TEXT, content TEXT, status TEXT DEFAULT 'active');

CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY, user_id INTEGER, item TEXT, amount INTEGER, deliver_at TEXT,
  status TEXT DEFAULT 'placed', created_at TEXT);

CREATE TABLE IF NOT EXISTS collection_requests(
  id INTEGER PRIMARY KEY, user_id INTEGER, payer_contact_id INTEGER,
  amount INTEGER, status TEXT DEFAULT 'pending', created_at TEXT);

CREATE TABLE IF NOT EXISTS pending_intents(
  id TEXT PRIMARY KEY, session_id TEXT, user_id INTEGER, plan_json TEXT,
  card_json TEXT, params_hash TEXT, token TEXT, status TEXT, expires_at TEXT,
  stepup_json TEXT, created_at TEXT, trace_id TEXT);

CREATE TABLE IF NOT EXISTS payee_accounts(
  contact_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS knowledge_versions(
  id INTEGER PRIMARY KEY, entry_id TEXT, version TEXT, title TEXT, content TEXT,
  keywords TEXT, source TEXT, effective_from TEXT, effective_to TEXT,
  status TEXT DEFAULT 'draft', created_at TEXT, approved_at TEXT, published_at TEXT);

CREATE TABLE IF NOT EXISTS loans(
  id INTEGER PRIMARY KEY, user_id INTEGER, principal INTEGER, remaining INTEGER,
  monthly INTEGER, next_due TEXT, status TEXT DEFAULT 'active', created_at TEXT);

CREATE TABLE IF NOT EXISTS risk_profiles(
  user_id INTEGER PRIMARY KEY, level TEXT, score INTEGER, assessed_at TEXT, expires_at TEXT);

CREATE TABLE IF NOT EXISTS password_resets(
  id INTEGER PRIMARY KEY, user_id INTEGER, reset_no TEXT UNIQUE, channel TEXT,
  status TEXT DEFAULT 'pending', created_at TEXT);

CREATE TABLE IF NOT EXISTS authorizations(
  id INTEGER PRIMARY KEY, user_id INTEGER, delegate_name TEXT, relationship TEXT,
  scope TEXT DEFAULT 'query', status TEXT DEFAULT 'active', expires_at TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS support_tickets(
  id INTEGER PRIMARY KEY, ticket_no TEXT UNIQUE, session_id TEXT, user_id INTEGER,
  summary TEXT, status TEXT DEFAULT 'open', created_at TEXT, trace_id TEXT);

CREATE TABLE IF NOT EXISTS feature_flags(
  name TEXT PRIMARY KEY, enabled INTEGER DEFAULT 0, rollout INTEGER DEFAULT 0, updated_at TEXT);

CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY, trace_id TEXT, session_id TEXT, user_id INTEGER,
  rating TEXT, reason TEXT, created_at TEXT);

CREATE INDEX IF NOT EXISTS idx_txn_user_created ON transactions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_entry_account ON ledger_entry(account_id);
CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit_event(trace_id, seq);

CREATE TABLE IF NOT EXISTS budgets(
  user_id INTEGER NOT NULL,
  category TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  updated_at TEXT,
  PRIMARY KEY(user_id, category));

CREATE VIEW IF NOT EXISTS v_account_balance AS
SELECT a.id AS account_id,
       a.opening_balance
       + COALESCE(SUM(CASE e.direction WHEN 'C' THEN e.amount ELSE -e.amount END), 0) AS balance
FROM accounts a
LEFT JOIN ledger_entry e ON e.account_id = a.id
GROUP BY a.id;

-- 沙箱只读视图（不含手机号/姓名等敏感字段，供受限查询使用）
CREATE VIEW IF NOT EXISTS v_transactions AS
SELECT id, txn_no, type, amount, status, category, counterparty,
       substr(created_at, 1, 10) AS day
FROM transactions;

CREATE VIEW IF NOT EXISTS v_subscriptions AS
SELECT id, merchant, amount, cycle_days, next_charge_at, status
FROM subscriptions;

CREATE VIEW IF NOT EXISTS v_cards AS
SELECT id, card_tail, status, daily_limit, used
FROM cards;

CREATE VIEW IF NOT EXISTS v_holdings AS
SELECT id, product_id, amount, purchased_at
FROM holdings;
"""


def db_path(path: str | None = None) -> str:
    return path or config.DB_PATH


def connect(path: str | None = None) -> sqlite3.Connection:
    p = db_path(path)
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error:
        pass
    return conn


def init_db(path: str | None = None) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：老库补列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
    for table, column, ddl in (
        ("users", "customer_no", "ALTER TABLE users ADD COLUMN customer_no TEXT"),
        ("users", "kyc_level", "ALTER TABLE users ADD COLUMN kyc_level TEXT DEFAULT 'L2'"),
        ("users", "realname_status", "ALTER TABLE users ADD COLUMN realname_status TEXT DEFAULT 'verified'"),
        ("users", "device_id", "ALTER TABLE users ADD COLUMN device_id TEXT"),
        ("knowledge_versions", "keywords", "ALTER TABLE knowledge_versions ADD COLUMN keywords TEXT"),
        ("support_tickets", "trace_id", "ALTER TABLE support_tickets ADD COLUMN trace_id TEXT"),
        ("sessions", "device_id", "ALTER TABLE sessions ADD COLUMN device_id TEXT"),
        ("cards", "bill_cents", "ALTER TABLE cards ADD COLUMN bill_cents INTEGER DEFAULT 0"),
        ("contacts", "phone_full", "ALTER TABLE contacts ADD COLUMN phone_full TEXT"),
    ):
        table_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in table_cols:
            try:
                conn.execute(ddl)
            except sqlite3.Error:
                pass

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pending_intents)").fetchall()}
    if "trace_id" not in cols:
        try:
            conn.execute("ALTER TABLE pending_intents ADD COLUMN trace_id TEXT")
        except sqlite3.Error:
            pass


def reset_db(path: str | None = None) -> None:
    p = db_path(path)
    if p != ":memory:" and os.path.exists(p):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(p + suffix)
            except OSError:
                pass
    init_db(p)
