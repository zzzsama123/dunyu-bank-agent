"""可信层：事实账本、模板渲染、幻觉校验、注入检测、审计哈希链。

本模块只依赖 Python 标准库与 ``config``，不依赖银行核心库。设计目标：

* 回复中的金额/单号只能来自工具申报的 facts（FactLedger）；
* 模板负责组织语言，数字由代码注入，避免模型编造；
* postcheck 只强校验金额和交易单号，绝不把百分比/期限/等级/日期/时间误判；
* 不可信文本统一包裹 ``<untrusted>``，并做保守的注入特征扫描；
* 审计事件按 trace 组成 sha256 哈希链，追加时用 ``BEGIN IMMEDIATE`` 串行化。
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import operator
import re
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import now_iso

__all__ = [
    "TEMPLATES",
    "FactLedger",
    "money",
    "render",
    "postcheck",
    "scan_injection",
    "wrap_untrusted",
    "AuditLog",
]


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _strip_zero_width(text: str) -> str:
    """移除常见零宽/格式控制字符（保留普通中文与 emoji）。"""
    return _ZERO_WIDTH_RE.sub("", text)


# ---------------------------------------------------------------------------
# 金额
# ---------------------------------------------------------------------------

def money(cents: int) -> str:
    """把整数分格式化为带千分位的金额字符串。

    >>> money(50000)
    '500.00'
    >>> money(1234567)
    '12,345.67'
    >>> money(-50000)
    '-500.00'
    """
    if isinstance(cents, bool):
        raise TypeError("金额必须为整数分（bool 不接受）")
    try:
        value = operator.index(cents)
    except TypeError as exc:
        raise TypeError("金额必须为整数分（int）") from exc

    sign = "-" if value < 0 else ""
    whole, fraction = divmod(abs(value), 100)
    return f"{sign}{whole:,}.{fraction:02d}"


def _to_cents(value: Any, *, int_is_cents: bool = True) -> int | None:
    """把金额事实/回复片段归一为分。

    * int：按“分”处理（契约规定的唯一内部金额单位）；
    * str / Decimal：按“元”处理，允许 ``¥``、``元``、千分位和全角字符。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if int_is_cents else value * 100
    if isinstance(value, float):
        # 契约禁止 float；不能静默接受，否则容易引入浮点误差。
        return None

    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, str):
        text = _nfkc(value).strip()
        text = re.sub(r"[\s\u00a0]+", "", text)
        for token in ("人民币", "RMB", "CNY", "¥", "￥", "元", "圆", "块钱", "块", "整"):
            text = text.replace(token, "")
        text = text.replace("−", "-").replace("－", "-").replace("＋", "+")
        text = text.replace(",", "")
        text = text.strip("。；;，,")
        if not text:
            return None
        try:
            decimal_value = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None

    if not decimal_value.is_finite():
        return None
    cents = decimal_value * 100
    if cents != cents.to_integral_value():
        return None
    return int(cents)


# ---------------------------------------------------------------------------
# FactLedger
# ---------------------------------------------------------------------------

_VALID_KINDS = frozenset({"money", "id", "ratio", "term", "text"})


class FactLedger:
    """请求级事实账本。工具返回的 facts 累积到这里，回复校验只认这里。"""

    def __init__(self) -> None:
        self._facts: list[dict] = []
        self._auto_id = 0

    def add(self, facts: list[dict]) -> None:
        """追加 facts（保留原始字段；缺 id 时补一个内部 id）。"""
        if isinstance(facts, Mapping):
            facts = [facts]  # 便于单条 fact 的调用方，不改变列表契约
        if isinstance(facts, (str, bytes)) or not isinstance(facts, Iterable):
            raise TypeError("facts 必须是 dict 列表")

        for raw in facts:
            if not isinstance(raw, Mapping):
                raise TypeError("每一条 fact 必须是 dict")
            fact = dict(raw)
            if "value" not in fact:
                raise ValueError("fact 缺少 value")
            key = fact.get("key")
            if not isinstance(key, str) or not key:
                raise ValueError("fact 缺少合法的 key")
            kind = fact.get("kind")
            if not isinstance(kind, str) or kind not in _VALID_KINDS:
                raise ValueError(f"非法 fact.kind: {kind!r}")
            if not fact.get("id"):
                self._auto_id += 1
                fact["id"] = f"f{self._auto_id}"
            self._facts.append(fact)

    def contains(self, value: Any, kinds: Iterable[str] = ("money", "id")) -> bool:
        """只在指定 kind 中查找。

        金额支持 int 分与格式化字符串（``"500.00"`` / ``"12,345.67"``）。
        """
        if value is None:
            return False
        if isinstance(kinds, str):
            kinds = (kinds,)
        wanted = set(kinds)

        for fact in self._facts:
            kind = fact.get("kind")
            if kind not in wanted:
                continue

            if kind == "money":
                target = _to_cents(value)
                actual = _to_cents(fact.get("value"))
                if target is not None and target == actual:
                    return True
            elif kind == "id":
                if isinstance(value, str):
                    if _norm_id(value) == _norm_id(fact.get("value")):
                        return True
            else:
                if _norm_text(value) == _norm_text(fact.get("value")):
                    return True
        return False

    def get(self, key: str) -> dict | None:
        """按 key 取最近一条 fact；不存在返回 None。"""
        for fact in reversed(self._facts):
            if fact.get("key") == key:
                return dict(fact)
        return None

    def all(self) -> list[dict]:
        """返回所有 facts 的浅拷贝，调用方修改不会污染账本。"""
        return [dict(fact) for fact in self._facts]

    def __len__(self) -> int:  # 便于调用方/测试观测
        return len(self._facts)


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    return _nfkc(str(value)).strip().lower()


def _norm_id(value: Any) -> str:
    if value is None:
        return ""
    return _nfkc(str(value)).strip().casefold()


# ---------------------------------------------------------------------------
# 模板渲染
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, str] = {
    "transfer_success": "已向{payee_name}转账{amount}元，交易单号{txn_no}。",
    "transfer_failed": "转账未完成，原因：{reason}。你可以稍后重试。",
    "balance": "你当前可用余额为{balance}元。",
    "bill_summary": "本期共支出{total}元，共{count}笔。",
    "subs_list": "共找到{count}项自动扣费，最近一笔是{merchant}（{amount}元）。",
    "plan_created": "已为你创建计划「{goal}」，计划编号{plan_id}。",
    "frozen": "已锁定{amount}元，当前可用余额{available_balance}元。",
    "cancelled": "已取消{name}，将于{effective_at}生效。",
    "card_lost": "已为尾号{card_tail}的卡片办理挂失，新卡将寄送至预留地址。",
    "wealth_bought": "已申购{product_name}{amount}元，确认单号{order_no}。",
    "error": "抱歉，操作未完成：{message}（{code}）。",
    "confirm_intro": "请确认以下操作：{summary}。确认后才会执行。",
    "stepup_intro": "该操作需要强验证，请完成{factors}验证后继续。",
}

# 模板里以展示名出现的金额字段；调用方即使传 int 分也自动格式化。
_MONEY_DISPLAY_FIELDS = frozenset({
    "amount",
    "balance",
    "available",
    "available_balance",
    "frozen",
    "frozen_amount",
    "total",
    "total_amount",
    "largest",
    "largest_amount",
    "fee",
    "limit",
    "budget",
})

# 常见 ToolResult 字段名到模板展示名的兼容别名。
_CENTS_ALIASES: dict[str, tuple[str, ...]] = {
    "amount_cents": ("amount",),
    "balance_cents": ("balance",),
    "balance_after_cents": ("balance", "balance_after"),
    "available_cents": ("available", "available_balance"),
    "available_balance_cents": ("available_balance", "available"),
    "frozen_cents": ("frozen", "frozen_amount", "amount"),
    "frozen_amount_cents": ("frozen_amount", "frozen"),
    "total_cents": ("total", "total_amount"),
    "total_amount_cents": ("total_amount", "total"),
    "largest_cents": ("largest", "largest_amount"),
    "fee_cents": ("fee",),
    "limit_cents": ("limit",),
    "budget_cents": ("budget",),
}


def _format_amount_field(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return money(value)
    return value


def render(template_id: str, **fields: Any) -> str:
    """按模板渲染回复。缺字段抛 KeyError，未知模板也抛 KeyError。

    约定：字段名以 ``_cents`` 结尾即按“分”自动 ``money()``，并同时暴露去掉
    后缀的别名，因此 ``amount_cents=50000`` 可被 ``{amount}`` 使用。
    """
    try:
        template = TEMPLATES[template_id]
    except KeyError:
        raise KeyError(template_id) from None

    prepared: dict[str, Any] = {}
    aliases: dict[str, Any] = {}

    for name, raw_value in fields.items():
        value = raw_value
        if name.endswith("_cents"):
            value = _format_amount_field(raw_value)
            base = name[: -len("_cents")]
            if base:
                aliases.setdefault(base, value)
            for alias in _CENTS_ALIASES.get(name, ()):
                aliases.setdefault(alias, value)
        elif name in _MONEY_DISPLAY_FIELDS:
            value = _format_amount_field(raw_value)
        prepared[name] = value

    # 显式传入的字段优先于由 *_cents 推导出的别名。
    for name, value in aliases.items():
        prepared.setdefault(name, value)

    return template.format_map(prepared)


# ---------------------------------------------------------------------------
# postcheck：只校验回复中的金额与交易单号
# ---------------------------------------------------------------------------

# 数字：整数、千分位、最多两位小数。只有出现“金额线索”（币种/元/小数/千分位）
# 才进入校验，避免把 90 天、27%、R2、11-03、23:30 等正常信息打成兜底。
_NUMBER_PATTERN = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{2})?"
_MONEY_TOKEN_RE = re.compile(
    r"(?<![0-9A-Za-z_.,:/\-])"
    r"(?P<sign>[+\-−])?"
    r"(?P<currency>[¥￥])?\s*"
    rf"(?P<number>{_NUMBER_PATTERN})"
    r"(?P<suffix>\s*(?:元|圆|块(?:钱)?))?"
    r"(?![0-9]|,[0-9]|\.[0-9])"
)

# 交易单号：T + 至少 8 位数字，例如 T20261029001。
_TXN_NO_RE = re.compile(r"(?<![0-9A-Za-z])T[0-9]{8,}(?![0-9A-Za-z])", re.IGNORECASE)

# 这些单位跟在数字后时，说明它是期限/计数/比率，不是金额。
_NON_MONEY_UNITS = (
    "%", "‰", "天", "日", "个月", "月", "年", "周", "星期", "小时", "时",
    "分钟", "分", "秒", "期", "次", "笔", "项", "条", "个", "位", "人",
    "名", "岁", "折", "星", "级", "楼", "层", "张", "份", "户", "号",
    "克", "公斤", "千米", "公里", "米", "倍", "万", "千",
    "days", "day", "months", "month", "years", "year", "hours", "hour",
    "minutes", "minute", "seconds", "second", "times", "percent",
)

_DATE_PREFIX_RE = re.compile(
    r"(?:日期|账单日|还款日|到期日|交易日|时间|date|time)\s*[:：]?\s*$",
    re.IGNORECASE,
)


def _money_match_is_candidate(match: re.Match[str], text: str) -> bool:
    groups = match.groupdict()
    number = groups["number"]
    has_money_cue = bool(
        groups["currency"]
        or groups["suffix"]
        or "." in number
        or "," in number
    )
    if not has_money_cue:
        return False

    tail = text[match.end():]
    tail_compact = tail.lstrip()
    if not tail_compact:
        return True
    lowered_tail = tail_compact.lower()

    for unit in _NON_MONEY_UNITS:
        if lowered_tail.startswith(unit):
            return False

    # 时间/日期字段后面的数字不当金额（例如“还款日 11.03”）。
    prefix = text[max(0, match.start() - 16):match.start()]
    if _DATE_PREFIX_RE.search(prefix):
        return False
    return True


def _decode_payload(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text


def postcheck(reply: str, ledger: FactLedger) -> tuple[bool, list[str]]:
    """发出前事实校验。

    只抽取并强校验：
    * 有金额线索的数字（``¥500`` / ``500.00`` / ``12,345.67`` / ``500 元``）；
    * ``T2026...`` 形式的交易单号。

    返回 ``(ok, offenders)``。百分比、期限、风险等级、日期、时间不参与校验。
    这里宁可漏检，也不把正常回复误判成兜底。
    """
    if reply is None:
        return True, []
    if not isinstance(reply, str):
        reply = str(reply)
    if not reply:
        return True, []

    text = _nfkc(_strip_zero_width(reply))

    candidates: list[tuple[int, str, str]] = []
    for match in _MONEY_TOKEN_RE.finditer(text):
        if not _money_match_is_candidate(match, text):
            continue
        token = match.group(0).strip()
        if token:
            candidates.append((match.start(), token, "money"))

    for match in _TXN_NO_RE.finditer(text):
        token = match.group(0).strip()
        if token:
            candidates.append((match.start(), token, "id"))

    candidates.sort(key=lambda item: (item[0], 0 if item[2] == "id" else 1))

    offenders: list[str] = []
    seen: set[str] = set()
    for _, token, _kind in candidates:
        dedupe_key = token.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        if not ledger.contains(token, kinds=("money", "id")):
            offenders.append(token)

    return (not offenders), offenders


# ---------------------------------------------------------------------------
# 注入检测
# ---------------------------------------------------------------------------

_ZERO_WIDTH_RE = re.compile(
    "[\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]"
)
_FULLWIDTH_ALNUM_RE = re.compile(r"[\uff10-\uff19\uff21-\uff3a\uff41-\uff5a]")

_OVERRIDE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:忽略|无视|不要理会|别管|忘记|忘掉)\s*(?:掉|了)?\s*"
        r"(?:以上|之前|先前|上述|前面|所有|全部|你(?:之前|刚才)?的)\s*"
        r"(?:的)?\s*(?:所有|全部)?\s*"
        r"(?:指令|提示|规则|要求|设定|内容|命令|限制|安全策略)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:忽略|无视|不要理会|别管)\s*(?:系统|平台|开发者|银行)(?:的)?\s*"
        r"(?:指令|提示|规则|要求|设定)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:跳过|略过|绕过|越过)\s*(?:掉|了)?\s*"
        r"(?:确认|二次确认|验证|强验证|安全验证|身份验证|授权|短信|密码|风控|审核)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:无需|不用|不需要|免去|免除|免)\s*(?:再次|二次)?\s*"
        r"(?:确认|验证|强验证|授权|短信|密码)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:把|将)?\s*(?:刚才的|本次|这次)?\s*(?:确认|验证)\s*"
        r"(?:当作|视为|视作|标记为|算作)\s*"
        r"(?:已完成|已通过|通过|完成|已确认)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ignore|disregard|forget|override)\s+"
        r"(?:all\s+|any\s+|the\s+)?"
        r"(?:previous|prior|above|earlier|system|developer|your)?\s*"
        r"(?:instructions?|prompts?|rules?|directions?|messages?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ignore|disregard)\s+(?:all\s+|the\s+)?"
        r"(?:previous|prior|above|earlier)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:skip|bypass|circumvent|avoid)\s+(?:the\s+)?"
        r"(?:confirmation|verification|authentication|authorization|approval|2fa|otp|sms|password)\b",
        re.IGNORECASE,
    ),
)

_ROLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:我是|作为|身为|以)\s*(?:本行|银行)?\s*"
        r"(?:行长|管理员|系统管理员|超级管理员|admin|administrator|root)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\[(?:system|系统|admin|管理员|instruction|指令)\]",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[\n\r。；;])\s*(?:system|admin|administrator|developer|assistant|"
        r"管理员|行长|系统)\s*[:：]",
        re.IGNORECASE,
    ),
    re.compile(r"\bsystem\s+(?:message|prompt)\b", re.IGNORECASE),
    re.compile(
        r"\byou\s+are\s+(?:now\s+)?(?:the\s+)?(?:system|admin|administrator|root)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi\s+am\s+(?:the\s+)?(?:bank\s+)?(?:president|manager|administrator|admin|ceo)\b",
        re.IGNORECASE,
    ),
)

_DELIMITER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?m)^[ \t]*(?:-{3,}|={3,}|\*{3,}|_{3,})[ \t]*$"),
    re.compile(
        r"(?:-{3,}|={3,})\s*(?=(?:忽略|ignore|disregard|system\b|admin\s*[:：]|"
        r"我是|跳过|直接|bypass|you\s+are|\[system\]))",
        re.IGNORECASE,
    ),
    re.compile(r"[\[【]\s*(?:指令|instruction|system|admin)\s*[:：]", re.IGNORECASE),
)

_BASE64_RE = re.compile(
    r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])"
)


def _contains_any(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _looks_like_base64(token: str) -> bool:
    """降低普通长英文/哈希被误判为 base64 的概率。"""
    if "=" in token or "+" in token or "/" in token:
        return True
    has_upper = any("A" <= ch <= "Z" for ch in token)
    has_lower = any("a" <= ch <= "z" for ch in token)
    has_digit = any("0" <= ch <= "9" for ch in token)
    return has_upper and has_lower and has_digit


def _has_base64_fragment(text: str) -> bool:
    for match in _BASE64_RE.finditer(text):
        token = match.group(0)
        if _looks_like_base64(token):
            return True

        # 对“没有明显特征”的长串做一次保守解码，能解出注入语义也算命中。
        try:
            padded = token + "=" * ((4 - len(token) % 4) % 4)
            decoded = base64.b64decode(padded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        lowered = decoded.lower()
        if _contains_any(_OVERRIDE_PATTERNS, lowered) or _contains_any(_ROLE_PATTERNS, lowered):
            return True
    return False


def scan_injection(text: str) -> dict:
    """扫描不可信文本中的常见注入特征。

    返回 ``{"hit": bool, "patterns": [...], "normalized": str}``。
    ``normalized`` 执行 NFKC、去零宽字符、转小写。
    """
    if text is None:
        raw = ""
    elif isinstance(text, str):
        raw = text
    else:
        raw = str(text)

    patterns: list[str] = []

    if _ZERO_WIDTH_RE.search(raw):
        patterns.append("zero_width")

    stripped = _strip_zero_width(raw)
    if _FULLWIDTH_ALNUM_RE.search(stripped):
        patterns.append("fullwidth")

    normalized = _nfkc(stripped).lower()
    scan_text = _nfkc(stripped)

    if _contains_any(_OVERRIDE_PATTERNS, normalized):
        patterns.append("instruction_override")
    if _contains_any(_ROLE_PATTERNS, normalized):
        patterns.append("role_impersonation")
    if _contains_any(_DELIMITER_PATTERNS, scan_text):
        patterns.append("delimiter_injection")
    if _has_base64_fragment(scan_text):
        patterns.append("base64")

    # 去重并保持固定类别顺序。
    deduped = list(dict.fromkeys(patterns))
    return {"hit": bool(deduped), "patterns": deduped, "normalized": normalized}


def wrap_untrusted(label: str, text: str) -> str:
    """把不可信文本放进定界标签，并转义伪造闭合标签所需的字符。"""
    safe_label = html.escape(_as_text(label), quote=True)
    safe_text = html.escape(_as_text(text), quote=False)
    return f'<untrusted source="{safe_label}">{safe_text}</untrusted>'


# ---------------------------------------------------------------------------
# 审计哈希链
# ---------------------------------------------------------------------------

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit_event (
    id INTEGER PRIMARY KEY,
    trace_id TEXT NOT NULL,
    plan_id TEXT,
    node_id TEXT,
    seq INTEGER NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    payload TEXT,
    decision TEXT,
    prev_hash TEXT,
    hash TEXT,
    ts TEXT NOT NULL,
    UNIQUE(trace_id, seq)
)
"""

# 同一进程内串行化审计写入；跨进程/多连接仍由 BEGIN IMMEDIATE 保证。
_AUDIT_APPEND_LOCK = threading.RLock()
_AUDIT_COLUMNS = (
    "id, trace_id, plan_id, node_id, seq, actor, action, payload, "
    "decision, prev_hash, hash, ts"
)
_MAX_BUSY_RETRIES = 8


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return _json_dumps(value)


def _event_hash(
    *,
    trace_id: str,
    plan_id: str | None,
    node_id: str | None,
    seq: int,
    actor: str,
    action: str,
    payload_text: str | None,
    decision: str | None,
    prev_hash: str | None,
    ts: str,
) -> str:
    material = _json_dumps({
        "trace_id": trace_id,
        "plan_id": plan_id,
        "node_id": node_id,
        "seq": seq,
        "actor": actor,
        "action": action,
        "payload": payload_text,
        "decision": decision,
        "prev_hash": prev_hash,
        "ts": ts,
    })
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _is_busy_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _row_to_event(row: tuple) -> dict:
    return {
        "id": row[0],
        "trace_id": row[1],
        "plan_id": row[2],
        "node_id": row[3],
        "seq": row[4],
        "actor": row[5],
        "action": row[6],
        "payload": _decode_payload(row[7]),
        "decision": row[8],
        "prev_hash": row[9],
        "hash": row[10],
        "ts": row[11],
    }


class AuditLog:
    """按 trace 追加的审计哈希链。

    ``append`` 在单个 ``BEGIN IMMEDIATE`` 事务内完成“读上一条 -> 计算 seq/hash
    -> 插入”，并对同一 trace 唯一约束 ``(trace_id, seq)`` 做兜底。多线程共享
    同一个连接时由模块级锁串行化；多连接/多进程由 SQLite 写锁串行化。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._savepoint_seq = 0
        with _AUDIT_APPEND_LOCK:
            try:
                conn.execute("PRAGMA busy_timeout = 10000")
            except sqlite3.Error:
                pass
            conn.execute(AUDIT_DDL)

    def append(
        self,
        trace_id: str,
        actor: str,
        action: str,
        payload: Any = None,
        decision: Any = None,
        plan_id: str | None = None,
        node_id: str | None = None,
    ) -> dict:
        if not isinstance(trace_id, str):
            raise ValueError("trace_id 必须是非空字符串")
        if not isinstance(actor, str):
            raise ValueError("actor 必须是非空字符串")
        if not isinstance(action, str):
            raise ValueError("action 必须是非空字符串")
        trace_id = trace_id.strip()
        actor = actor.strip()
        action = action.strip()
        if not trace_id or not actor or not action:
            raise ValueError("trace_id/actor/action 不能为空")
        plan_id = None if plan_id is None else str(plan_id)
        node_id = None if node_id is None else str(node_id)
        payload_text = None if payload is None else _json_dumps(payload)
        decision_text = _text_or_none(decision)

        with _AUDIT_APPEND_LOCK:
            for attempt in range(_MAX_BUSY_RETRIES):
                try:
                    return self._append_once(
                        trace_id=trace_id,
                        actor=actor,
                        action=action,
                        payload_text=payload_text,
                        decision_text=decision_text,
                        plan_id=plan_id,
                        node_id=node_id,
                    )
                except sqlite3.OperationalError as exc:
                    if not _is_busy_error(exc) or attempt == _MAX_BUSY_RETRIES - 1:
                        raise
                    time.sleep(0.02 * (attempt + 1))

        raise RuntimeError("unreachable")  # pragma: no cover

    def _append_once(
        self,
        *,
        trace_id: str,
        actor: str,
        action: str,
        payload_text: str | None,
        decision_text: str | None,
        plan_id: str | None,
        node_id: str | None,
    ) -> dict:
        conn = self.conn
        own_transaction = not conn.in_transaction
        savepoint: str | None = None

        try:
            if own_transaction:
                conn.execute("BEGIN IMMEDIATE")
            else:
                self._savepoint_seq += 1
                savepoint = f"bankagent_audit_append_{self._savepoint_seq}"
                conn.execute(f"SAVEPOINT {savepoint}")

            previous = conn.execute(
                "SELECT seq, hash FROM audit_event "
                "WHERE trace_id = ? ORDER BY seq DESC LIMIT 1",
                (trace_id,),
            ).fetchone()
            seq = (int(previous[0]) + 1) if previous else 1
            prev_hash = previous[1] if previous else None

            ts = now_iso()
            event_hash = _event_hash(
                trace_id=trace_id,
                plan_id=plan_id,
                node_id=node_id,
                seq=seq,
                actor=actor,
                action=action,
                payload_text=payload_text,
                decision=decision_text,
                prev_hash=prev_hash,
                ts=ts,
            )

            cursor = conn.execute(
                "INSERT INTO audit_event "
                "(trace_id, plan_id, node_id, seq, actor, action, payload, "
                "decision, prev_hash, hash, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trace_id,
                    plan_id,
                    node_id,
                    seq,
                    actor,
                    action,
                    payload_text,
                    decision_text,
                    prev_hash,
                    event_hash,
                    ts,
                ),
            )

            event = {
                "id": cursor.lastrowid,
                "trace_id": trace_id,
                "plan_id": plan_id,
                "node_id": node_id,
                "seq": seq,
                "actor": actor,
                "action": action,
                "payload": _decode_payload(payload_text),
                "decision": decision_text,
                "prev_hash": prev_hash,
                "hash": event_hash,
                "ts": ts,
            }

            if own_transaction:
                conn.commit()
            else:
                conn.execute(f"RELEASE {savepoint}")
            return event
        except BaseException:
            if own_transaction:
                if conn.in_transaction:
                    conn.rollback()
            elif savepoint is not None:
                try:
                    conn.execute(f"ROLLBACK TO {savepoint}")
                    conn.execute(f"RELEASE {savepoint}")
                except sqlite3.Error:
                    pass
            raise

    def list(self, trace_id: str) -> list[dict]:
        """按 seq 升序返回某条 trace 的事件（payload 已 JSON 解码）。"""
        if isinstance(trace_id, str):
            trace_id = trace_id.strip()
        with _AUDIT_APPEND_LOCK:
            rows = self.conn.execute(
                f"SELECT {_AUDIT_COLUMNS} FROM audit_event "
                "WHERE trace_id = ? ORDER BY seq ASC, id ASC",
                (trace_id,),
            ).fetchall()
        return [_row_to_event(tuple(row)) for row in rows]

    def verify_chain(self, trace_id: str) -> bool:
        """校验 seq 连续、prev_hash 链接正确、每行 hash 未被篡改。"""
        if isinstance(trace_id, str):
            trace_id = trace_id.strip()
        with _AUDIT_APPEND_LOCK:
            rows = self.conn.execute(
                f"SELECT {_AUDIT_COLUMNS} FROM audit_event "
                "WHERE trace_id = ? ORDER BY seq ASC, id ASC",
                (trace_id,),
            ).fetchall()

        expected_prev: str | None = None
        for index, raw_row in enumerate(rows, start=1):
            row = tuple(raw_row)
            seq = row[4]
            stored_prev = row[9]
            stored_hash = row[10]
            if seq != index or stored_prev != expected_prev:
                return False
            if not isinstance(stored_hash, str):
                return False

            expected_hash = _event_hash(
                trace_id=row[1],
                plan_id=row[2],
                node_id=row[3],
                seq=seq,
                actor=row[5],
                action=row[6],
                payload_text=row[7],
                decision=row[8],
                prev_hash=stored_prev,
                ts=row[11],
            )
            if stored_hash != expected_hash:
                return False
            expected_prev = stored_hash
        return True

    def list_traces(self, limit: int = 200, offset: int = 0, query: str = "") -> dict:
        """全量审计索引：按 trace 聚合，带首条用户消息与事件数。"""
        limit = max(1, min(int(limit or 200), 1000))
        offset = max(0, int(offset or 0))
        where = ""
        params: list = []
        if query:
            where = ("WHERE trace_id IN (SELECT trace_id FROM audit_event "
                     "WHERE payload LIKE ? OR action LIKE ?)")
            params = [f"%{query}%", f"%{query}%"]
        total = int(self.conn.execute(
            f"SELECT COUNT(DISTINCT trace_id) FROM audit_event {where}", params
        ).fetchone()[0])
        rows = self.conn.execute(
            f"SELECT trace_id, MIN(ts) AS started_at, MAX(ts) AS ended_at, COUNT(*) AS events "
            f"FROM audit_event {where} GROUP BY trace_id ORDER BY started_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        traces = []
        for row in rows:
            trace_id = row["trace_id"]
            first = self.conn.execute(
                "SELECT payload FROM audit_event WHERE trace_id=? AND actor='user' "
                "AND action='message' ORDER BY seq ASC LIMIT 1", (trace_id,),
            ).fetchone()
            summary = ""
            if first and first["payload"]:
                try:
                    payload = json.loads(first["payload"])
                    summary = str(payload.get("text") or "")[:80]
                except (ValueError, TypeError):
                    summary = str(first["payload"])[:80]
            last = self.conn.execute(
                "SELECT action, decision FROM audit_event WHERE trace_id=? "
                "ORDER BY seq DESC LIMIT 1", (trace_id,),
            ).fetchone()
            traces.append({
                "trace_id": trace_id,
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "events": int(row["events"]),
                "summary": summary,
                "last_action": last["action"] if last else "",
                "decision": last["decision"] if last else "",
            })
        return {"traces": traces, "total": total, "limit": limit, "offset": offset}
