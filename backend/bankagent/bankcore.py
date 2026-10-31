"""银行仿真核心：复式记账、幂等、冲正、日额度、账户/卡/理财/订阅/长期计划。

设计约束（对应方案 5.4）：
- 余额只由 v_account_balance 聚合得出，`accounts.opening_balance` 开户后不可变；
- 每笔交易两条分录，借贷相等；
- 永不删除，撤销 = 反向分录 + 关联原单；
- 写操作必须带幂等键。
"""

from __future__ import annotations

import contextlib
import json
import random
import sqlite3
import uuid
from datetime import date, datetime, time as dtime, timedelta

from . import config


class BankError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------- 基础设施
@contextlib.contextmanager
def tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _err(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _now() -> str:
    return config.now_iso()


def _row(r) -> dict:
    return dict(r) if r is not None else {}


def _txn_no() -> str:
    """交易单号：时间戳到微秒 + 两位随机，避免同秒并发撞唯一键。"""
    ts = datetime.now(config.TZ).strftime("%Y%m%d%H%M%S%f")[:20]
    return "T" + ts + f"{random.randint(10, 99)}"


# ---------------------------------------------------------------- 账户
def get_balance(conn, account_id: int) -> int:
    row = conn.execute("SELECT balance FROM v_account_balance WHERE account_id = ?", (account_id,)).fetchone()
    return int(row["balance"]) if row else 0


def frozen_total(conn, account_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS s FROM freezes WHERE account_id=? AND status='active'",
        (account_id,),
    ).fetchone()
    return int(row["s"])


def available(conn, account_id: int) -> int:
    acc = conn.execute("SELECT frozen FROM accounts WHERE id=?", (account_id,)).fetchone()
    frozen_col = int(acc["frozen"]) if acc else 0
    return get_balance(conn, account_id) - frozen_col - frozen_total(conn, account_id)


def list_accounts(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, type, opening_balance, frozen, currency FROM accounts "
        "WHERE user_id=? AND type IN ('current','fixed','credit') ORDER BY id",
        (user_id,),
    ).fetchall()
    out = []
    for r in rows:
        bal = get_balance(conn, r["id"])
        out.append(
            {
                "id": r["id"],
                "type": r["type"],
                "type_name": {"current": "活期", "fixed": "定期", "credit": "信用"}.get(r["type"], r["type"]),
                "balance_cents": bal,
                "frozen_cents": frozen_total(conn, r["id"]),
                "available_cents": available(conn, r["id"]),
                "currency": r["currency"] or "CNY",
            }
        )
    return out


def get_user(conn, user_id: int = config.DEFAULT_USER_ID) -> dict:
    return _row(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


def main_account(conn, user_id: int) -> int:
    row = conn.execute(
        "SELECT id FROM accounts WHERE user_id=? AND type='current' ORDER BY id LIMIT 1", (user_id,)
    ).fetchone()
    if not row:
        raise BankError("E_INTERNAL", "主账户不存在")
    return int(row["id"])


def _internal_account(conn, kind: str) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE type=? ORDER BY id LIMIT 1", (kind,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO accounts(user_id,type,opening_balance,frozen,currency,created_at) VALUES(0,?,0,0,'CNY',?)",
        (kind, _now()),
    )
    return int(cur.lastrowid)


def _payee_account(conn, contact_id: int) -> int:
    row = conn.execute("SELECT account_id FROM payee_accounts WHERE contact_id=?", (contact_id,)).fetchone()
    if row:
        return int(row["account_id"])
    cur = conn.execute(
        "INSERT INTO accounts(user_id,type,opening_balance,frozen,currency,created_at) VALUES(0,'payee',0,0,'CNY',?)",
        (_now(),),
    )
    acc_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO payee_accounts(contact_id,account_id) VALUES(?,?)", (contact_id, acc_id)
    )
    return acc_id


def _post_entry(conn, txn_id: int, account_id: int, direction: str, amount: int) -> None:
    conn.execute(
        "INSERT INTO ledger_entry(txn_id,account_id,direction,amount,created_at) VALUES(?,?,?,?,?)",
        (txn_id, account_id, direction, amount, _now()),
    )


# ---------------------------------------------------------------- 日额度
def get_daily_used(conn, user_id: int, biz_date: str | None = None) -> int:
    d = biz_date or config.today_str()
    row = conn.execute("SELECT used FROM daily_quota WHERE user_id=? AND biz_date=?", (user_id, d)).fetchone()
    return int(row["used"]) if row else 0


def occupy_quota(conn, user_id: int, amount_cents: int, limit_cents: int = config.LIMIT_DAILY) -> bool:
    """条件更新做原子占用：无返回行 = 超限。"""
    d = config.today_str()
    cur = conn.execute(
        """
        INSERT INTO daily_quota(user_id,biz_date,used) VALUES(?,?,?)
        ON CONFLICT(user_id,biz_date) DO UPDATE
           SET used = daily_quota.used + excluded.used
         WHERE daily_quota.used + excluded.used <= ?
        RETURNING used
        """,
        (user_id, d, amount_cents, limit_cents),
    )
    return cur.fetchone() is not None


def add_quota(conn, user_id: int, amount_cents: int) -> int:
    """记录当日已用额度（不做上限判断——限额是策略层的事）。

    强验证通过的大额转账必须能落账，所以账务层不能再用 1,000 元硬拦；
    `occupy_quota` 保留给策略侧的并发占用演示与边界测试。
    """
    d = config.today_str()
    conn.execute(
        "INSERT INTO daily_quota(user_id,biz_date,used) VALUES(?,?,?) "
        "ON CONFLICT(user_id,biz_date) DO UPDATE SET used = daily_quota.used + excluded.used",
        (user_id, d, amount_cents),
    )
    row = conn.execute("SELECT used FROM daily_quota WHERE user_id=? AND biz_date=?", (user_id, d)).fetchone()
    return int(row["used"]) if row else amount_cents


def release_quota(conn, user_id: int, amount_cents: int, biz_date: str | None = None) -> None:
    d = biz_date or config.today_str()
    conn.execute(
        "UPDATE daily_quota SET used = MAX(0, used - ?) WHERE user_id=? AND biz_date=?",
        (amount_cents, user_id, d),
    )


# ---------------------------------------------------------------- 联系人
def list_contacts(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id,name,alias,phone,card_tail,is_new FROM contacts WHERE user_id=? ORDER BY id", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def find_contact(conn, user_id: int, alias_or_name: str) -> list[dict]:
    q = (alias_or_name or "").strip()
    if not q:
        return list_contacts(conn, user_id)
    rows = conn.execute(
        "SELECT id,name,alias,phone,card_tail,is_new,phone_full FROM contacts "
        "WHERE user_id=? AND (alias LIKE ? OR name LIKE ? OR phone LIKE ? OR card_tail=? OR phone_full=?) ORDER BY id",
        (user_id, f"%{q}%", f"%{q}%", f"%{q}%", q, q),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 转账 / 冲正
def _idem_result(conn, txn_row, from_account_id: int | None = None) -> dict:
    status = txn_row["status"]
    if status == "succeeded":
        acc = from_account_id or main_account(conn, txn_row["user_id"])
        return {
            "ok": True,
            "idempotent": True,
            "txn_id": txn_row["id"],
            "txn_no": txn_row["txn_no"],
            "amount_cents": txn_row["amount"],
            "balance_after": get_balance(conn, acc),
            "payee_name": txn_row["counterparty"] or "",
        }
    if status == "pending":
        return _err("E_CONFLICT", "该笔交易正在处理中，请勿重复提交")
    return _err("E_CONFLICT", "上一次同键交易已失败，请重新发起")


def create_transfer(
    conn,
    user_id: int,
    from_account_id: int,
    contact_id: int,
    amount_cents: int,
    memo: str = "",
    idem_key: str | None = None,
    txn_type: str = "transfer",
) -> dict:
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        return _err("E_PARAM", "金额必须为正整数（分）")
    idem = idem_key or f"tr-{uuid.uuid4().hex}"
    row = conn.execute("SELECT * FROM transactions WHERE idempotency_key=?", (idem,)).fetchone()
    if row:
        return _idem_result(conn, row, from_account_id)

    contact = conn.execute(
        "SELECT * FROM contacts WHERE id=? AND user_id=?", (contact_id, user_id)
    ).fetchone()
    if not contact:
        return _err("E_CONTACT_NOT_FOUND", "收款人不存在")

    try:
        with tx(conn):
            row = conn.execute("SELECT * FROM transactions WHERE idempotency_key=?", (idem,)).fetchone()
            if row:
                return _idem_result(conn, row, from_account_id)

            if available(conn, from_account_id) < amount_cents:
                raise BankError("E_INSUFFICIENT_FUND", "可用余额不足")

            if txn_type == "transfer":
                add_quota(conn, user_id, amount_cents)

            txn_no = _txn_no()
            cur = conn.execute(
                "INSERT INTO transactions(txn_no,user_id,type,amount,currency,status,"
                "idempotency_key,memo,category,counterparty,created_at) "
                "VALUES(?,?,?,?,?,'pending',?,?,?,?,?)",
                (txn_no, user_id, txn_type, amount_cents, "CNY", idem, memo or "",
                 "转账", contact["name"], _now()),
            )
            txn_id = int(cur.lastrowid)
            payee_acc = _payee_account(conn, contact_id)
            _post_entry(conn, txn_id, from_account_id, "D", amount_cents)
            _post_entry(conn, txn_id, payee_acc, "C", amount_cents)
            conn.execute("UPDATE transactions SET status='succeeded' WHERE id=?", (txn_id,))

        return {
            "ok": True,
            "txn_id": txn_id,
            "txn_no": txn_no,
            "amount_cents": amount_cents,
            "balance_after": get_balance(conn, from_account_id),
            "payee_name": contact["name"],
        }
    except BankError as e:
        return _err(e.code, e.message)
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT * FROM transactions WHERE idempotency_key=?", (idem,)).fetchone()
        if row:
            return _idem_result(conn, row, from_account_id)
        return _err("E_CONFLICT", "并发冲突，请重试")


def submit_transfer(conn, *args, **kwargs) -> dict:
    """大额转账：账务语义与 create_transfer 相同，差异在策略层。"""
    kwargs.setdefault("txn_type", "transfer")
    return create_transfer(conn, *args, **kwargs)


def reverse_transaction(conn, txn_id: int, idem_key: str | None = None) -> dict:
    idem = idem_key or f"rev:{txn_id}"
    origin = conn.execute("SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
    if not origin:
        return _err("E_NOT_FOUND", "原交易不存在")
    if origin["status"] == "reversed":
        return _err("E_CONFLICT", "该交易已冲正")
    if origin["status"] != "succeeded":
        return _err("E_CONFLICT", "只有成功的交易可以冲正")

    row = conn.execute("SELECT * FROM transactions WHERE idempotency_key=?", (idem,)).fetchone()
    if row:
        return {"ok": True, "idempotent": True, "reversal_txn_id": row["id"], "reversal_txn_no": row["txn_no"]}

    try:
        with tx(conn):
            txn_no = _txn_no()
            cur = conn.execute(
                "INSERT INTO transactions(txn_no,user_id,type,amount,currency,status,idempotency_key,"
                "memo,category,counterparty,created_at) VALUES(?,?,?,?,?,'succeeded',?,?,?,?,?)",
                (txn_no, origin["user_id"], "reversal", origin["amount"], "CNY", idem,
                 f"冲正 {origin['txn_no']}", "冲正", origin["counterparty"], _now()),
            )
            rid = int(cur.lastrowid)
            entries = conn.execute("SELECT * FROM ledger_entry WHERE txn_id=?", (txn_id,)).fetchall()
            for e in entries:
                _post_entry(conn, rid, e["account_id"], "C" if e["direction"] == "D" else "D", e["amount"])
            conn.execute(
                "UPDATE transactions SET status='reversed', reversed_by=? WHERE id=?", (rid, txn_id)
            )
        if origin["created_at"][:10] == config.today_str():
            release_quota(conn, origin["user_id"], origin["amount"])
        return {"ok": True, "reversal_txn_id": rid, "reversal_txn_no": txn_no}
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT * FROM transactions WHERE idempotency_key=?", (idem,)).fetchone()
        if row:
            return {"ok": True, "idempotent": True, "reversal_txn_id": row["id"], "reversal_txn_no": row["txn_no"]}
        return _err("E_CONFLICT", "并发冲突，请重试")


_DIRECTION = {
    "transfer": "out", "purchase": "out", "order": "out",
    "transfer_in": "in", "reversal": "in", "collection": "in", "redeem": "in", "refund": "in",
}


def list_transactions(conn, user_id: int, start: str | None = None, end: str | None = None,
                      limit: int = 200) -> list[dict]:
    start = start or "1970-01-01"
    end = end or "2999-12-31"
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id=? AND substr(created_at,1,10) BETWEEN ? AND ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, start, end, int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["direction"] = _DIRECTION.get(r["type"], "out")
        d["amount_cents"] = int(r["amount"])
        out.append(d)
    return out


# ---------------------------------------------------------------- 订阅
def list_subscriptions(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id,merchant,amount,cycle_days,next_charge_at,status,last_used_at "
        "FROM subscriptions WHERE user_id=? ORDER BY id",
        (user_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["amount_cents"] = int(r["amount"])
        d["unused_days"] = _days_since(r["last_used_at"]) if r["last_used_at"] else None
        out.append(d)
    return out


def _days_since(iso_ts: str) -> int:
    try:
        return (config.now_dt().date() - date.fromisoformat(iso_ts[:10])).days
    except Exception:
        return 0


def cancel_subscription(conn, user_id: int, sub_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE id=? AND user_id=?", (sub_id, user_id)
    ).fetchone()
    if not row:
        return _err("E_NOT_FOUND", "订阅不存在")
    if row["status"] != "active":
        return _err("E_CONFLICT", "该订阅已不是生效状态")
    conn.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (sub_id,))
    return {
        "ok": True,
        "sub_id": sub_id,
        "merchant": row["merchant"],
        "effective_at": row["next_charge_at"],
        "message": f"{row['merchant']} 已取消，当前周期可用至 {row['next_charge_at'][:10]}",
    }


# ---------------------------------------------------------------- 理财
def list_products(conn, risk_level: str | None = None, term_days: int | None = None) -> list[dict]:
    sql = "SELECT * FROM wealth_products WHERE 1=1"
    args: list = []
    if risk_level:
        sql += " AND risk_level=?"
        args.append(risk_level)
    if term_days:
        sql += " AND term_days=?"
        args.append(int(term_days))
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def list_holdings(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT h.*, p.name AS product_name, p.rate, p.term_days, p.risk_level "
        "FROM holdings h LEFT JOIN wealth_products p ON p.id=h.product_id WHERE h.user_id=?",
        (user_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["amount_cents"] = int(r["amount"])
        out.append(d)
    return out


def purchase_wealth(conn, user_id: int, product_id: int, amount_cents: int) -> dict:
    product = conn.execute("SELECT * FROM wealth_products WHERE id=?", (product_id,)).fetchone()
    if not product:
        return _err("E_NOT_FOUND", "理财产品不存在")
    if amount_cents < int(product["min_amount"]):
        return _err("E_PARAM", "低于起购金额")
    acc = main_account(conn, user_id)
    if available(conn, acc) < amount_cents:
        return _err("E_INSUFFICIENT_FUND", "可用余额不足")
    pool = _internal_account(conn, "wealth_pool")
    cur = conn.execute(
        "INSERT INTO transactions(txn_no,user_id,type,amount,currency,status,idempotency_key,"
        "memo,category,counterparty,created_at) VALUES(?,?,'purchase',?,'CNY','succeeded',?,?,?,?,?)",
        (_txn_no(), user_id, amount_cents, f"pw-{uuid.uuid4().hex}", "理财申购", "理财",
         product["name"], _now()),
    )
    txn_id = int(cur.lastrowid)
    _post_entry(conn, txn_id, acc, "D", amount_cents)
    _post_entry(conn, txn_id, pool, "C", amount_cents)
    h = conn.execute(
        "INSERT INTO holdings(user_id,product_id,amount,purchased_at) VALUES(?,?,?,?)",
        (user_id, product_id, amount_cents, _now()),
    )
    return {
        "ok": True, "txn_id": txn_id, "txn_no": conn.execute(
            "SELECT txn_no FROM transactions WHERE id=?", (txn_id,)).fetchone()["txn_no"],
        "holding_id": int(h.lastrowid), "product_name": product["name"],
        "amount_cents": amount_cents, "balance_after": get_balance(conn, acc),
    }


def redeem_wealth(conn, user_id: int, holding_id: int, amount_cents: int | None = None) -> dict:
    h = conn.execute("SELECT * FROM holdings WHERE id=? AND user_id=?", (holding_id, user_id)).fetchone()
    if not h:
        return _err("E_NOT_FOUND", "持仓不存在")
    amt = int(amount_cents or h["amount"])
    if amt > int(h["amount"]) or amt <= 0:
        return _err("E_PARAM", "赎回金额超出持仓")
    acc = main_account(conn, user_id)
    pool = _internal_account(conn, "wealth_pool")
    cur = conn.execute(
        "INSERT INTO transactions(txn_no,user_id,type,amount,currency,status,idempotency_key,"
        "memo,category,counterparty,created_at) VALUES(?,?,'redeem',?,'CNY','succeeded',?,?,?,?,?)",
        (_txn_no(), user_id, amt, f"rw-{uuid.uuid4().hex}", "理财赎回", "理财", "理财赎回", _now()),
    )
    txn_id = int(cur.lastrowid)
    _post_entry(conn, txn_id, pool, "D", amt)
    _post_entry(conn, txn_id, acc, "C", amt)
    conn.execute("UPDATE holdings SET amount=amount-? WHERE id=?", (amt, holding_id))
    return {"ok": True, "txn_id": txn_id, "amount_cents": amt, "balance_after": get_balance(conn, acc)}


# ---------------------------------------------------------------- 卡片
def list_cards(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id,card_tail,status,daily_limit,used,frozen FROM cards WHERE user_id=?", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def report_card_lost(conn, user_id: int, card_id: int) -> dict:
    row = conn.execute("SELECT * FROM cards WHERE id=? AND user_id=?", (card_id, user_id)).fetchone()
    if not row:
        return _err("E_NOT_FOUND", "卡片不存在")
    conn.execute("UPDATE cards SET status='lost', frozen=1 WHERE id=?", (card_id,))
    return {"ok": True, "card_id": card_id, "card_tail": row["card_tail"],
            "ticket_no": "WO" + uuid.uuid4().hex[:8].upper(), "replace_days": "3-5 个工作日"}


def unlock_card(conn, user_id: int, card_id: int) -> dict:
    row = conn.execute("SELECT * FROM cards WHERE id=? AND user_id=?", (card_id, user_id)).fetchone()
    if not row:
        return _err("E_NOT_FOUND", "卡片不存在")
    conn.execute("UPDATE cards SET status='active', frozen=0 WHERE id=?", (card_id,))
    return {"ok": True, "card_id": card_id, "card_tail": row["card_tail"], "status": "active"}


def adjust_card_limit(conn, user_id: int, card_id: int, new_limit_cents: int) -> dict:
    row = conn.execute("SELECT * FROM cards WHERE id=? AND user_id=?", (card_id, user_id)).fetchone()
    if not row:
        return _err("E_NOT_FOUND", "卡片不存在")
    if new_limit_cents <= 0:
        return _err("E_PARAM", "额度必须为正")
    direction = "up" if new_limit_cents > int(row["daily_limit"] or 0) else "down"
    conn.execute("UPDATE cards SET daily_limit=? WHERE id=?", (new_limit_cents, card_id))
    return {"ok": True, "card_id": card_id, "new_limit_cents": new_limit_cents, "direction": direction}


def apply_virtual_card(conn, user_id: int, card_type: str = "virtual") -> dict:
    tail = f"{random.randint(1000, 9999)}"
    cur = conn.execute(
        "INSERT INTO cards(user_id,card_tail,status,daily_limit,used,frozen) VALUES(?,?,'active',?,0,0)",
        (user_id, tail, 500_000),
    )
    return {"ok": True, "card_id": int(cur.lastrowid), "card_tail": tail, "card_type": card_type}


# ---------------------------------------------------------------- 冻结
def freeze_amount(conn, user_id: int, account_id: int | None, amount_cents: int, until: str | None = None) -> dict:
    acc = int(account_id or main_account(conn, user_id))
    if available(conn, acc) < amount_cents:
        return _err("E_INSUFFICIENT_FUND", "可用余额不足，无法锁定")
    cur = conn.execute(
        "INSERT INTO freezes(user_id,account_id,amount,status,until,created_at) VALUES(?,?,?,'active',?,?)",
        (user_id, acc, amount_cents, until, _now()),
    )
    return {"ok": True, "freeze_id": int(cur.lastrowid), "amount_cents": amount_cents,
            "available_cents": available(conn, acc)}


def release_freeze(conn, freeze_id: int) -> dict:
    row = conn.execute("SELECT * FROM freezes WHERE id=?", (freeze_id,)).fetchone()
    if not row:
        return _err("E_NOT_FOUND", "冻结记录不存在")
    if row["status"] != "active":
        return _err("E_CONFLICT", "该冻结已释放")
    conn.execute("UPDATE freezes SET status='released' WHERE id=?", (freeze_id,))
    return {"ok": True, "freeze_id": freeze_id, "released_cents": int(row["amount"]),
            "available_cents": available(conn, int(row["account_id"]))}


# ---------------------------------------------------------------- 长期计划 / 其他
def _next_run_at(conn, user_id: int, trigger_rule: dict) -> str:
    """把 trigger_rule 翻译成具体时间点（生日/每月/每周/每日/一次性）。"""
    if trigger_rule.get("at"):
        return str(trigger_rule["at"])
    now = config.now_dt()
    today = now.date()
    t = trigger_rule.get("type")
    try:
        hh, mm = [int(x) for x in str(trigger_rule.get("time") or "09:00").split(":")[:2]]
    except (ValueError, TypeError):
        hh, mm = 9, 0
    clock = dtime(hh, mm)

    if t in ("birthday", "anniversary"):
        row = conn.execute("SELECT birthday FROM users WHERE id=?", (user_id,)).fetchone()
        raw = row["birthday"] if row and row["birthday"] else None
        if raw:
            try:
                month, day = int(raw[5:7]), int(raw[8:10])
                run = date(today.year, month, day)
                if run < today:
                    run = date(today.year + 1, month, day)
                run = run - timedelta(days=int(trigger_rule.get("days_before") or 2))
                return f"{run.isoformat()}T{hh:02d}:{mm:02d}:00+08:00"
            except (ValueError, TypeError):
                pass
        return config.now_iso()

    if t == "monthly":
        day = max(1, min(int(trigger_rule.get("day") or 1), 28))
        run = date(today.year, today.month, day)
        if run < today or (run == today and now.time() > clock):
            run = (run.replace(day=28) + timedelta(days=4)).replace(day=day)
        return f"{run.isoformat()}T{hh:02d}:{mm:02d}:00+08:00"

    if t == "weekly":
        weekday = int(trigger_rule.get("weekday") or 0)  # 0=周一
        delta = (weekday - today.weekday()) % 7
        if delta == 0 and now.time() > clock:
            delta = 7
        return f"{(today + timedelta(days=delta)).isoformat()}T{hh:02d}:{mm:02d}:00+08:00"

    if t == "daily":
        run = today if now.time() < clock else today + timedelta(days=1)
        return f"{run.isoformat()}T{hh:02d}:{mm:02d}:00+08:00"

    return config.now_iso()


def create_standing_plan(conn, user_id: int, goal: str, trigger_rule: dict,
                         actions: list[dict], budget_cents: int = 0) -> dict:
    next_run = _next_run_at(conn, user_id, trigger_rule)
    cur = conn.execute(
        "INSERT INTO standing_plans(user_id,goal,trigger_rule,actions,budget,status,next_run_at,created_at) "
        "VALUES(?,?,?,?,?,'active',?,?)",
        (user_id, goal, json.dumps(trigger_rule, ensure_ascii=False),
         json.dumps(actions, ensure_ascii=False), int(budget_cents), next_run, _now()),
    )
    return {"ok": True, "plan_id": int(cur.lastrowid), "next_run_at": next_run,
            "goal": goal, "budget_cents": int(budget_cents), "actions": actions}


def list_standing_plans(conn, user_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM standing_plans WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["trigger_rule"] = json.loads(r["trigger_rule"] or "{}")
        d["actions"] = json.loads(r["actions"] or "[]")
        out.append(d)
    return out


def cancel_standing_plan(conn, plan_id: int) -> dict:
    row = conn.execute("SELECT * FROM standing_plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        return _err("E_NOT_FOUND", "计划不存在")
    conn.execute("UPDATE standing_plans SET status='cancelled' WHERE id=?", (plan_id,))
    return {"ok": True, "plan_id": plan_id, "status": "cancelled"}


def set_reminder(conn, user_id: int, at: str, content: str) -> dict:
    cur = conn.execute(
        "INSERT INTO reminders(user_id,at,content,status) VALUES(?,?,?,'active')", (user_id, at, content)
    )
    return {"ok": True, "reminder_id": int(cur.lastrowid), "at": at, "content": content}


def create_collection_request(conn, user_id: int, payer_contact_ids: list[int],
                              amount_each_cents: int) -> dict:
    ids = []
    for cid in payer_contact_ids:
        cur = conn.execute(
            "INSERT INTO collection_requests(user_id,payer_contact_id,amount,status,created_at) "
            "VALUES(?,?,?,'pending',?)",
            (user_id, int(cid), int(amount_each_cents), _now()),
        )
        ids.append(int(cur.lastrowid))
    return {"ok": True, "request_ids": ids, "count": len(ids), "amount_each_cents": amount_each_cents,
            "total_cents": len(ids) * amount_each_cents, "flow": "in"}


def order_gift(conn, user_id: int, item: str, amount_cents: int, deliver_at: str | None = None) -> dict:
    acc = main_account(conn, user_id)
    if available(conn, acc) < amount_cents:
        return _err("E_INSUFFICIENT_FUND", "可用余额不足")
    pool = _internal_account(conn, "merchant_pool")
    cur = conn.execute(
        "INSERT INTO transactions(txn_no,user_id,type,amount,currency,status,idempotency_key,"
        "memo,category,counterparty,created_at) VALUES(?,?,'order',?,'CNY','succeeded',?,?,?,?,?)",
        (_txn_no(), user_id, amount_cents, f"og-{uuid.uuid4().hex}", f"下单 {item}", "商户订单",
         "外部商户", _now()),
    )
    txn_id = int(cur.lastrowid)
    _post_entry(conn, txn_id, acc, "D", amount_cents)
    _post_entry(conn, txn_id, pool, "C", amount_cents)
    o = conn.execute(
        "INSERT INTO orders(user_id,item,amount,deliver_at,status,created_at) VALUES(?,?,?,?,'placed',?)",
        (user_id, item, amount_cents, deliver_at or _now(), _now()),
    )
    return {"ok": True, "order_id": int(o.lastrowid), "item": item, "amount_cents": amount_cents,
            "txn_id": txn_id, "deliver_at": deliver_at, "reversible": False}


def notify(conn, user_id: int, content: str) -> dict:
    return {"ok": True, "delivered": True, "to": user_id, "content": content}


# ---------------------------------------------------------------- 会话
def get_session(conn, session_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def get_or_create_session(conn, session_id: str, user_id: int, channel: str = "app") -> dict:
    s = get_session(conn, session_id)
    if s:
        return s
    conn.execute(
        "INSERT INTO sessions(id,user_id,channel,state,recent_entities,updated_at) VALUES(?,?,?,'NORMAL','{}',?)",
        (session_id, user_id, channel, _now()),
    )
    return get_session(conn, session_id)


def update_session(conn, session_id: str, state: str | None = None,
                   recent_entities: dict | None = None) -> None:
    if state is not None:
        conn.execute("UPDATE sessions SET state=?, updated_at=? WHERE id=?", (state, _now(), session_id))
    if recent_entities is not None:
        conn.execute(
            "UPDATE sessions SET recent_entities=?, updated_at=? WHERE id=?",
            (json.dumps(recent_entities, ensure_ascii=False), _now(), session_id),
        )


# ---------------------------------------------------------------- 办理闭环（还款/测评/密码）


def loan_view(conn, user_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM loans WHERE user_id=? AND status='active' ORDER BY id LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    principal = int(row["principal"] or 0)
    remaining = int(row["remaining"] or 0)
    monthly = int(row["monthly"] or 0)
    paid = max(0, round((principal - remaining) / monthly)) if monthly else 0
    return {"id": int(row["id"]), "principal_cents": principal, "remaining_cents": remaining,
            "monthly_cents": monthly, "paid_periods": min(paid, 60), "term_months": 60,
            "next_due": row["next_due"]}


def repay_loan(conn, user_id: int, amount_cents: int | None = None) -> dict:
    loan = conn.execute(
        "SELECT * FROM loans WHERE user_id=? AND status='active' ORDER BY id LIMIT 1",
        (user_id,),
    ).fetchone()
    if not loan:
        return _err("E_NOT_FOUND", "没有待还贷款")
    remaining = int(loan["remaining"])
    amount = int(amount_cents or loan["monthly"])
    if amount <= 0:
        return _err("E_PARAM", "还款金额必须大于 0")
    if amount > remaining:
        return _err("E_PARAM", "还款金额超过剩余本金")
    acc = main_account(conn, user_id)
    if available(conn, acc) < amount:
        total = get_balance(conn, acc)
        frozen = frozen_total(conn, acc) + int(
            conn.execute("SELECT frozen FROM accounts WHERE id=?", (acc,)).fetchone()["frozen"] or 0)
        return _err("E_INSUFFICIENT_FUND",
                    f"可用余额不足：可用 {available(conn, acc) / 100:.2f} 元，"
                    f"总余额 {total / 100:.2f} 元，其中 {frozen / 100:.2f} 元已被计划锁定")
    pool = _internal_account(conn, "loan_pool")
    cur = conn.execute(
        "INSERT INTO transactions(txn_no,user_id,type,amount,currency,status,idempotency_key,"
        "memo,category,counterparty,created_at) VALUES(?,?,'repayment',?,'CNY','succeeded',?,?,?,?,?)",
        (_txn_no(), user_id, amount, f"rp-{uuid.uuid4().hex}", "贷款还款", "还款",
         "房贷", _now()),
    )
    txn_id = int(cur.lastrowid)
    _post_entry(conn, txn_id, acc, "D", amount)
    _post_entry(conn, txn_id, pool, "C", amount)
    conn.execute("UPDATE loans SET remaining=remaining-? WHERE id=?", (amount, loan["id"]))
    return {
        "ok": True, "txn_id": txn_id,
        "txn_no": conn.execute("SELECT txn_no FROM transactions WHERE id=?", (txn_id,)).fetchone()["txn_no"],
        "amount_cents": amount, "remaining_cents": remaining - amount,
        "balance_after": get_balance(conn, acc),
    }


def save_risk_assessment(conn, user_id: int, level: str, score: int) -> dict:
    now = _now()
    expires = (config.now_dt() + timedelta(days=365)).isoformat()
    conn.execute(
        "INSERT INTO risk_profiles(user_id,level,score,assessed_at,expires_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET level=excluded.level, score=excluded.score, "
        "assessed_at=excluded.assessed_at, expires_at=excluded.expires_at",
        (user_id, level, score, now, expires),
    )
    return {"ok": True, "level": level, "score": score, "assessed_at": now, "expires_at": expires}


def create_password_reset(conn, user_id: int, channel: str = "sms") -> dict:
    reset_no = "R" + config.now_dt().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:4].upper()
    conn.execute(
        "INSERT INTO password_resets(user_id,reset_no,channel,status,created_at) "
        "VALUES(?,?,?,'pending',?)",
        (user_id, reset_no, channel, _now()),
    )
    return {"ok": True, "reset_no": reset_no, "channel": channel,
            "notice": "已发送一次性重置链接，15 分钟内有效；不会在对话中收集新密码"}




def get_points(conn, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM points WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {"balance": 0, "expiring": 0, "expire_at": ""}
    return {"balance": int(row["balance"] or 0), "expiring": int(row["expiring"] or 0),
            "expire_at": str(row["expire_at"] or "")}


def credit_card_view(conn, user_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM cards WHERE user_id=? ORDER BY id LIMIT 1", (user_id,)).fetchone()
    if not row:
        return None
    limit = int(row["daily_limit"] or 0)
    used = int(row["used"] or 0)
    bill = int(row["bill_cents"] or 0)
    return {"id": int(row["id"]), "card_tail": row["card_tail"], "status": row["status"],
            "limit_cents": limit, "used_cents": used, "bill_cents": bill,
            "available_cents": max(0, limit - used), "minimum_cents": round(bill * 0.1)}


def repay_credit_card(conn, user_id: int, amount_cents: int) -> dict:
    card = conn.execute("SELECT * FROM cards WHERE user_id=? AND status='active' ORDER BY id LIMIT 1",
                        (user_id,)).fetchone()
    if not card:
        return _err("E_NOT_FOUND", "没有可还款的信用卡")
    used = int(card["used"] or 0)
    amount = int(amount_cents or 0)
    if amount <= 0:
        return _err("E_PARAM", "还款金额必须大于 0")
    if amount > used:
        return _err("E_PARAM", "还款金额超过信用卡已用额度")
    acc = main_account(conn, user_id)
    if available(conn, acc) < amount:
        total = get_balance(conn, acc)
        frozen = frozen_total(conn, acc) + int(
            conn.execute("SELECT frozen FROM accounts WHERE id=?", (acc,)).fetchone()["frozen"] or 0)
        return _err("E_INSUFFICIENT_FUND",
                    f"可用余额不足：可用 {available(conn, acc) / 100:.2f} 元，"
                    f"总余额 {total / 100:.2f} 元，其中 {frozen / 100:.2f} 元已被计划锁定")
    pool = _internal_account(conn, "card_pool")
    cur = conn.execute(
        "INSERT INTO transactions(txn_no,user_id,type,amount,currency,status,idempotency_key,"
        "memo,category,counterparty,created_at) VALUES(?,?,'credit_repayment',?,'CNY','succeeded',?,?,?,?,?)",
        (_txn_no(), user_id, amount, f"cc-{uuid.uuid4().hex}", "信用卡还款", "还款",
         f"信用卡{card['card_tail']}", _now()),
    )
    txn_id = int(cur.lastrowid)
    _post_entry(conn, txn_id, acc, "D", amount)
    _post_entry(conn, txn_id, pool, "C", amount)
    conn.execute("UPDATE cards SET used=MAX(0,used-?), bill_cents=MAX(0,bill_cents-?) WHERE id=?",
                 (amount, amount, card["id"]))
    return {"ok": True, "txn_id": txn_id,
            "txn_no": conn.execute("SELECT txn_no FROM transactions WHERE id=?", (txn_id,)).fetchone()["txn_no"],
            "amount_cents": amount, "used_after": used - amount,
            "balance_after": get_balance(conn, acc), "card_tail": card["card_tail"]}
