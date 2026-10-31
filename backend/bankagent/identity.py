"""身份与准入（演示实现）：客户号、KYC 等级、设备指纹、会话绑定、代办授权。

不接真实公安/人脸/设备风控；这里是可审计的仿真：
- 首次请求绑定设备，之后同会话换设备直接拒绝（防串话）；
- 授权委托有范围（query/small/all）、有效期、撤销；
- 代办请求必须命中有效授权，并写审计。
"""

from __future__ import annotations

import re
from datetime import timedelta

from . import bankcore, config

_SCOPE_RANK = {"query": 1, "small": 2, "all": 3}
_DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class IdentityError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _ensure(conn, user_id: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise IdentityError("E_USER", "用户不存在")
    data = dict(row)
    updates = {}
    if not data.get("customer_no"):
        updates["customer_no"] = f"CUST{user_id:06d}"
    if not data.get("kyc_level"):
        updates["kyc_level"] = "L2"
    if not data.get("realname_status"):
        updates["realname_status"] = "verified"
    if updates:
        sets = ", ".join(f"{key}=?" for key in updates)
        conn.execute(f"UPDATE users SET {sets} WHERE id=?", (*updates.values(), user_id))
        data.update(updates)
    return data


def _auth_rows(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM authorizations WHERE user_id=? ORDER BY id DESC", (user_id,)
    ).fetchall()
    now = config.now_iso()
    out = []
    for row in rows:
        item = dict(row)
        expired = bool(item.get("expires_at")) and str(item["expires_at"]) < now
        item["status"] = "expired" if expired else item.get("status")
        out.append(item)
    return out


def profile(conn, user_id: int = config.DEFAULT_USER_ID) -> dict:
    user = _ensure(conn, user_id)
    return {
        "user_id": user["id"],
        "name": user.get("name"),
        "phone": user.get("phone"),
        "customer_no": user.get("customer_no"),
        "kyc_level": user.get("kyc_level"),
        "realname_status": user.get("realname_status"),
        "device_bound": bool(user.get("device_id")),
        "device_tail": str(user.get("device_id") or "")[-6:],
        "authorizations": _auth_rows(conn, user_id),
    }


def bind_session(conn, session_id: str, device_id: str, user_id: int = config.DEFAULT_USER_ID) -> dict:
    """首次绑定设备；同会话换设备 → E_DEVICE_MISMATCH。"""
    if not device_id:
        return {"ok": True, "skipped": True}
    if not _DEVICE_RE.match(str(device_id)):
        raise IdentityError("E_DEVICE", "设备指纹格式不合法")
    user = _ensure(conn, user_id)
    bound = user.get("device_id")
    if bound and bound != device_id:
        raise IdentityError("E_DEVICE_MISMATCH", "当前设备与账户绑定设备不一致，请使用已绑定设备或到柜面解绑")
    session = bankcore.get_or_create_session(conn, session_id, user_id, "app")
    session_device = session.get("device_id")
    if session_device and session_device != device_id:
        raise IdentityError("E_DEVICE_MISMATCH", "当前会话已绑定其他设备，禁止跨设备串话")
    if not bound:
        conn.execute("UPDATE users SET device_id=? WHERE id=?", (device_id, user_id))
    if not session_device:
        conn.execute("UPDATE sessions SET device_id=? WHERE id=?", (device_id, session_id))
    return {"ok": True, "bound": True}


def authorize(conn, user_id: int, delegate_name: str, relationship: str,
              scope: str, days: int) -> dict:
    name = str(delegate_name or "").strip()
    if not 1 <= len(name) <= 20:
        raise IdentityError("E_PARAM", "代办人姓名长度需在 1–20 字")
    if scope not in _SCOPE_RANK:
        raise IdentityError("E_PARAM", "授权范围只能是 query / small / all")
    days = int(days or 0)
    if not 1 <= days <= 365:
        raise IdentityError("E_PARAM", "有效期需在 1–365 天")
    expires = (config.now_dt() + timedelta(days=days)).isoformat()
    cur = conn.execute(
        "INSERT INTO authorizations(user_id,delegate_name,relationship,scope,status,expires_at,created_at) "
        "VALUES(?,?,?,?,'active',?,?)",
        (user_id, name, str(relationship or "")[:20], scope, expires, config.now_iso()),
    )
    return {"id": int(cur.lastrowid), "delegate_name": name, "scope": scope, "expires_at": expires}


def revoke(conn, user_id: int, auth_id: int) -> dict:
    row = conn.execute("SELECT id FROM authorizations WHERE id=? AND user_id=?", (auth_id, user_id)).fetchone()
    if not row:
        raise IdentityError("E_NOT_FOUND", "授权不存在")
    conn.execute("UPDATE authorizations SET status='revoked' WHERE id=?", (auth_id,))
    return {"ok": True, "id": auth_id}


def verify_delegation(conn, user_id: int, delegate_name: str, required_scope: str = "query") -> dict:
    """校验代办授权；required_scope 为 query/small/all。"""
    rows = _auth_rows(conn, user_id)
    need = _SCOPE_RANK.get(required_scope, 3)
    for item in rows:
        if item.get("status") != "active":
            continue
        if str(item.get("delegate_name") or "") != str(delegate_name or ""):
            continue
        if _SCOPE_RANK.get(str(item.get("scope")), 0) >= need:
            return item
    raise IdentityError("E_DELEGATION", "该代办人没有有效授权，或授权范围不足")


def require_kyc(conn, user_id: int, level: str = "L2") -> None:
    user = _ensure(conn, user_id)
    order = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
    if order.get(str(user.get("kyc_level")), 0) < order.get(level, 2):
        raise IdentityError("E_KYC", "当前实名等级不足，请先完成实名认证")
