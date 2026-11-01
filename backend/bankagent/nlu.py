"""NLU 意图理解（纯函数，禁止 I/O）。

对外唯一入口：

    parse(text: str, ctx: dict) -> dict

返回字段严格遵循 CONTRACT.md §8：
    {"name", "slots", "missing", "ambiguity", "spans", "confidence"}

为支持契约 §5.5.1 的「再转一次 / 批量取消 / 指代消解」，额外增加三个
可选的顶层字段（不影响按 .get 读取的调用方）：
    carry_over       bool  —— 本轮继承了 ctx["last_intent"]["slots"]
    carry_over_text  str   —— 被继承的原话片段（若上下文里有）
    batch            bool  —— 批量取消
    from_context     bool  —— 槽位由 ctx["recent_entities"] 指代消解得到

设计约定：
* 金额一律整数分（`amount_cents`），绝不使用 float；
* 任何输入（None、非字符串、异常文本、异常 ctx）都不会抛异常，
  最差返回 name="unknown"、confidence<=0.3；
* 不读数据库、不联网、不读文件。
"""

from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .normalize import normalize

INTENT_NAMES = (
    "scheduled_transfer",
    "transfer",
    "bill_analysis",
    "subscription",
    "wealth",
    "card",
    "service",
    "standing_plan",
    "unknown",
)

# 同分时的优先级：越靠前越"具体"。transfer 的"转"太常见，放最后。
_INTENT_PRIORITY = [
    "scheduled_transfer",
    "service",
    "standing_plan",
    "subscription",
    "wealth",
    "card",
    "bill_analysis",
    "transfer",
]

# ---------------------------------------------------------------------------
# 文本归一化
# ---------------------------------------------------------------------------
_TRANSLATE = str.maketrans(
    {
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
        "，": ",", "．": ".", "％": "%", "　": " ",
    }
)

_ARABIC = "0-9"

# 中文数字 / 单位 -----------------------------------------------------------
_CN_DIGITS = {
    "零": 0, "〇": 0,
    "一": 1, "壹": 1,
    "二": 2, "两": 2, "贰": 2,
    "三": 3, "叁": 3,
    "四": 4, "肆": 4,
    "五": 5, "伍": 5,
    "六": 6, "陆": 6,
    "七": 7, "柒": 7,
    "八": 8, "捌": 8,
    "九": 9, "玖": 9,
}
_CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_ALL = set(_CN_DIGITS) | set(_CN_UNITS) | set("万萬亿億")

# 阿拉伯数字金额：500 / 1,200 / 0.5万 / 5百 / 5k / 1200块 / 5万元
_AR_AMOUNT = re.compile(
    r"(?<![\d.,])"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*(万|萬|w|W|千|k|K|百))?"
    r"(?:\s*(块钱|块|元|人民币|rmb|RMB|¥|￥))?"
)

# 中文数字金额：五百 / 一千二 / 五万三 / 半千 / 五百块
_CN_AMOUNT = re.compile(
    r"(半\s*(?:万|萬|千|百)|"
    r"[零〇一二两三四五六七八九十百千万萬亿億壹贰叁肆伍陆柒捌玖拾佰仟]{1,12})"
    r"(?:\s*(块钱|块|元|人民币))?"
)

# 紧跟在数字后、说明它不是金额的字（日期/数量/时间/编号/百分数）
_NOISE_FOLLOW = set("号月日年点时秒岁人位个张笔次天周折%％些起定样直般边共种半层届期分")
_NOISE_PREFIX = (
    "尾号", "卡号", "手机号", "账号", "单号", "订单号", "验证码",
    "编号", "序号", "生日", "日期", "月份", "年龄", "第",
)

# 能说明"前面这个数字是金额"的左侧词
_AMOUNT_LEFT = (
    "转", "打钱", "打", "汇款", "付款", "付", "还", "借", "消费", "花",
    "预算", "超过", "不超过", "别超过", "最多", "以内", "额度",
    "存", "取", "充", "买", "申购", "赎回", "各", "每人", "人均",
    "摊", "AA", "欠", "收", "吃饭", "一共", "总共", "合计",
)
_AMOUNT_RIGHT = ("给", "到", "整", "块", "元", "万", "千", "百")

_CURRENCY = ("块钱", "块", "元", "人民币", "rmb", "RMB", "¥", "￥")


def _normalize(text: str) -> str:
    return (text or "").translate(_TRANSLATE).replace("\u3000", " ").strip()


# ---------------------------------------------------------------------------
# 中文数字 -> int（元，不含分）
# ---------------------------------------------------------------------------
def _cn_section(s: str) -> int:
    """解析不含万/亿的一段中文数字。"""
    digits_only = [c for c in s if c in _CN_DIGITS]
    if digits_only and not any(c in _CN_UNITS for c in s):
        # "一二三" 这类连写数字
        if len(digits_only) > 1:
            return int("".join(str(_CN_DIGITS[c]) for c in digits_only))
        return _CN_DIGITS[digits_only[0]]

    total = 0
    num = 0
    last_unit = 0
    zero_seen = False
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if num == 0:
                num = 1
            total += num * unit
            last_unit = unit
            num = 0
            zero_seen = False
        elif ch in "零〇":
            zero_seen = True
    if num:
        # 口语省略：一千二=1200、两百五=250、一百五=150；一百零五=105
        if last_unit >= 10 and not zero_seen:
            total += num * (last_unit // 10)
        else:
            total += num
    return total


def _cn_to_int(s: str) -> int:
    s = (s or "").replace("萬", "万").replace("億", "亿").strip()
    if not s:
        return 0
    if "亿" in s:
        left, right = s.split("亿", 1)
        left_v = _cn_to_int(left) if left else 1
        return left_v * 100_000_000 + (_cn_to_int(right) if right else 0)
    if "万" in s:
        left, right = s.split("万", 1)
        left_v = _cn_to_int(left) if left else 1
        right_v = _cn_to_int(right) if right else 0
        # 口语：五万三=53000、一万二=12000
        if len(right) == 1 and right in _CN_DIGITS:
            right_v = _CN_DIGITS[right] * 1000
        return left_v * 10000 + right_v
    return _cn_section(s)


def _to_cents(yuan: Decimal) -> int:
    return int((yuan * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# 金额候选
# ---------------------------------------------------------------------------
def _is_noise_number(text: str, start: int, end: int) -> bool:
    after = text[end:end + 1]
    if after and after in _NOISE_FOLLOW:
        return True
    if after and after in "-/:":
        return True
    if after == "." and text[end + 1:end + 2].isdigit():
        return True
    if after == "," and re.match(r"\d{3}", text[end + 1:end + 4] or ""):
        return True
    before = text[max(0, start - 6):start]
    if any(before.endswith(p) for p in _NOISE_PREFIX):
        return True
    prev = text[start - 1:start]
    if prev and prev in "-/:.":
        return True
    digits = re.sub(r"\D", "", text[start:end])
    if len(digits) == 11 and digits.startswith("1"):
        return True
    return False


def _has_amount_context(text: str, start: int, end: int) -> bool:
    left = text[max(0, start - 10):start]
    right = text[end:end + 4]
    if any(w in left for w in _AMOUNT_LEFT):
        return True
    if any(right.startswith(w) for w in _AMOUNT_RIGHT):
        return True
    return False


def _amount_candidates(text: str) -> list[dict]:
    """返回所有可信金额候选（整数分）。"""
    candidates: list[dict] = []
    arabic_spans: list[tuple[int, int]] = []

    # 1) 阿拉伯数字
    for m in _AR_AMOUNT.finditer(text):
        raw_num, unit, suffix = m.group(1), m.group(2), m.group(3)
        start, end = m.start(), m.end()
        if _is_noise_number(text, start, end):
            continue
        try:
            base = Decimal(raw_num.replace(",", ""))
        except (InvalidOperation, ValueError):
            continue
        mult = {
            "万": 10000, "萬": 10000, "w": 10000, "W": 10000,
            "千": 1000, "k": 1000, "K": 1000, "百": 100,
        }.get(unit or "", 1)
        try:
            cents = _to_cents(base * mult)
        except (InvalidOperation, ValueError):
            continue
        if cents <= 0:
            continue
        has_unit = bool(unit)
        has_currency = bool(suffix)
        has_context = _has_amount_context(text, start, end)
        if not (has_unit or has_currency or has_context):
            continue
        score = 0.0
        if has_currency:
            score += 5
        if has_unit:
            score += 4
        if has_context:
            score += 3
        if text[end:end + 1] == "给":
            score += 2
        candidates.append(
            {
                "value": cents,
                "start": start,
                "end": end,
                "raw": text[start:end].strip(),
                "span": text[start:end].strip(),
                "score": score,
            }
        )
        arabic_spans.append((start, end))

    # 2) 中文数字
    for m in _CN_AMOUNT.finditer(text):
        raw, suffix = m.group(1), m.group(2)
        start, end = m.start(), m.end()
        # 已经被阿拉伯数字候选覆盖（如 5百 中的“百”）
        if any(not (end <= s or start >= e) for s, e in arabic_spans):
            continue
        raw_clean = raw.replace(" ", "")
        if raw_clean in ("千万", "万一", "十万火急"):
            continue
        after = text[end:end + 1]
        if raw_clean == "千万" and after and after in "别不要得":
            continue
        if after and after in "儿":
            continue
        if _is_noise_number(text, start, end):
            continue

        half = re.fullmatch(r"半\s*(万|萬|千|百)", raw)
        if half:
            mult = {"万": 10000, "萬": 10000, "千": 1000, "百": 100}.get(half.group(1), 1)
            cents = _to_cents(Decimal("0.5") * mult)
        else:
            try:
                value = _cn_to_int(raw_clean)
            except (ValueError, InvalidOperation):
                continue
            if value <= 0:
                continue
            cents = value * 100

        has_currency = bool(suffix)
        has_unit = any(c in _CN_UNITS or c in "万萬亿億" for c in raw_clean)
        has_context = _has_amount_context(text, start, end)
        if not (has_currency or has_unit or has_context):
            continue
        # 单个中文数字（如"张三"里的"三"）必须紧跟在金额动词后，避免把人名当金额
        if not has_currency and not has_unit and len(raw_clean) == 1:
            prev_text = text[max(0, start - 2):start]
            if not any(prev_text.endswith(w) for w in
                       ("转", "打", "付", "还", "各", "买", "存", "取", "充", "花", "摊", "给", "欠", "收", "借", "汇")):
                continue
        score = 0.0
        if has_currency:
            score += 5
        if has_unit:
            score += 4
        if has_context:
            score += 3
        if text[end:end + 1] == "给":
            score += 2
        candidates.append(
            {
                "value": cents,
                "start": start,
                "end": end,
                "raw": text[start:end].strip(),
                "span": raw,
                "score": score,
            }
        )

    # 去重（同一区间只留分数最高 / 最早）
    candidates.sort(key=lambda c: (c["start"], -c["score"]))
    deduped: list[dict] = []
    for cand in candidates:
        if any(not (cand["end"] <= x["start"] or cand["start"] >= x["end"]) for x in deduped):
            continue
        deduped.append(cand)
    return deduped


def _pick_amount(text: str, candidates: list[dict], intent: str,
                 prefer_budget: bool = False) -> dict | None:
    if not candidates:
        return None
    if prefer_budget:
        for c in candidates:
            left = text[max(0, c["start"] - 8):c["start"]]
            if any(w in left for w in ("预算", "超过", "不超过", "最多", "以内", "别超")):
                return c
    if intent == "card":
        for c in candidates:
            left = text[max(0, c["start"] - 8):c["start"]]
            if "额度" in left:
                return c
    if intent == "aa":
        for c in candidates:
            left = text[max(0, c["start"] - 10):c["start"]]
            if any(w in left for w in ("吃饭", "消费", "一共", "总共", "合计", "花了", "账单")):
                return c
    return max(candidates, key=lambda c: (c["score"], -c["start"]))


# ---------------------------------------------------------------------------
# 人称 / 范围 / AA / 批量
# ---------------------------------------------------------------------------
_PAYEE_CUT = set("转打付汇准过买送发帮给看的查说讲介推提列分算要把吧吗呢了啊的")
_PAYEE_STOP = {
    "我", "俺", "咱", "你", "他", "她", "它", "我们", "他们", "自己",
    "大家", "银行", "卡", "账户", "账号", "钱", "多少", "一下", "上次",
    "那个", "这笔", "这个", "哪个", "谁", "到", "了", "吧", "吗", "呢",
    "啊", "的", "挂失", "解挂", "消费", "账单", "订阅", "会员", "理财",
}
_KIN = (
    "老婆|老公|妈妈|妈|爸爸|爸|母亲|父亲|女儿|儿子|"
    "女朋友|男朋友|媳妇|爱人|太太|先生|朋友"
)
_TIME_WORDS = ("今天", "明天", "后天", "昨天", "下周", "这周", "下周", "下个", "这个", "那个")


def _payee_span(raw: str) -> str:
    s = re.split(r"\d", raw or "", maxsplit=1)[0]
    cut = len(s)
    for ch in _PAYEE_CUT:
        i = s.find(ch)
        if 0 <= i < cut:
            cut = i
    return s[:cut].strip(" \t，,。.!！?？、:：;；的")


def _clean_payee(raw: str) -> str | None:
    s = _payee_span(raw)
    if s.startswith(("我", "俺", "咱")) and len(s) > 1:
        s = s[1:]
    s = s.strip(" \t的")
    if not s or s in _PAYEE_STOP:
        return None
    return s


def _extract_payee(text: str) -> tuple[str | None, str | None]:
    """返回 (payee, span)。payee 存别名/姓名/手机号/卡后四位，span 存用户原话片段。"""
    m_phone = re.search(r"(1[3-9]\d{9})", text)
    if m_phone:
        return m_phone.group(1), m_phone.group(1)
    m_tail = re.search(r"(?:尾号|后四位|末四位)\s*(\d{4})", text)
    if m_tail:
        return m_tail.group(1), m_tail.group(0)
    # 给/向/帮/替/为 + 对象，例如：给我妈转500 / 转500给张三 / 给房东转2500
    for m in re.finditer(r"(?:给|向|帮|替|为)\s*([\u4e00-\u9fa5A-Za-z]{1,12})", text):
        raw = m.group(1)
        cleaned = _clean_payee(raw)
        if cleaned:
            return cleaned, _payee_span(raw)

    # 生日 / 纪念日：老婆、李娜……
    m = re.search(
        rf"(?:我的?|俺|咱)?\s*({_KIN})\s*(?:下?个?月)?\s*(?:的)?\s*(?:生日|纪念日)",
        text,
    )
    if m:
        return m.group(1), m.group(1)

    m = re.search(
        r"(?:我的?|俺|咱)?\s*([\u4e00-\u9fa5]{2,4}?)\s*(?:下?个?月)?\s*(?:的)?\s*(?:生日|纪念日)",
        text,
    )
    if m:
        cand = m.group(1)
        if cand not in _TIME_WORDS and not any(ch in cand for ch in "月日年下这那明今昨"):
            return cand, cand
    return None, None


_RANGE_PATTERNS = (
    (r"今天|今日", "today"),
    (r"昨天|昨日", "yesterday"),
    (r"前天", "day_before"),
    (r"本周|这周|这礼拜", "this_week"),
    (r"上周|上个星期|上星期", "last_7d"),
    (r"最近三个月|近三个月|最近3个月|近3个月", "last_3m"),
    (r"最近一个月|近一个月|最近30天|近30天|最近三十天|近三十天", "last_30d"),
    (r"最近一周|近一周|最近7天|近7天|最近七天|近七天", "last_7d"),
    (r"这个月|本月|这月", "this_month"),
    (r"上个月|上月", "last_month"),
    (r"今年|本年度", "this_year"),
    (r"全部|所有|历史", "all"),
)


def _extract_range(text: str) -> tuple[str | None, str | None]:
    for pattern, value in _RANGE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            return value, m.group(0)
    m = re.search(r"(\d{4})[-/年](\d{1,2})月?", text)
    if m:
        try:
            year, month = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12:
                return f"{year:04d}-{month:02d}", m.group(0)
        except ValueError:
            pass
    return None, None


def _extract_person_count(text: str) -> tuple[int | None, str | None]:
    m = re.search(r"(\d+|[一二两三四五六七八九十]+)\s*(?:个)?\s*人", text)
    if not m:
        return None, None
    raw = m.group(1)
    try:
        count = int(raw) if raw.isdigit() else _cn_to_int(raw)
    except (ValueError, InvalidOperation):
        return None, None
    if not 1 <= count <= 100:
        return None, None
    return count, m.group(0)


def _detect_aa(text: str) -> bool:
    if re.search(r"(?<![A-Za-z])AA(?![A-Za-z])", text, re.I):
        return True
    return any(w in text for w in ("各转", "各付", "各出", "平摊", "均摊", "分摊", "每人", "人均"))


def _detect_batch_cancel(text: str) -> bool:
    if re.search(r"(?:都|全都|全部|所有|统一)[^,。！？!?]{0,8}(?:退|取消|关掉|关闭|停掉|停了)", text):
        return True
    if re.search(r"(?:退(?:了|掉)?吧|取消(?:了|掉)?吧|退订吧)", text):
        return True
    if re.search(r"(?:退订|取消).{0,4}(?:全部|所有|都)", text):
        return True
    if re.search(r"(全部|所有|都|全)\s*(?:关|关掉|关闭|退|取消)", text):
        return True
    return False


def _detect_carry(text: str) -> bool:
    return bool(
        re.search(r"再(?:来|转)?(?:一|1)次|同上|照旧|和上次一样|再来一遍|再转", text)
    )


_ANOMALY_RE = re.compile(r"不对劲|异常|可疑|有没有问题|有问题|奇怪|不太对")
_BILL_STATS_RE = re.compile(r"账单|消费|支出|花哪|花钱|花销|开销|流水|明细|月报|报告|分类|统计")


def _last_intent(ctx: dict):
    """上一轮意图；兼容 dict / 字符串以及 recent_entities 内嵌。"""
    last = ctx.get("last_intent")
    if isinstance(last, (dict, str)) and last:
        return last
    entities = ctx.get("recent_entities")
    if isinstance(entities, dict):
        last = entities.get("last_intent")
        if isinstance(last, (dict, str)) and last:
            return last
    return None


def _is_anomaly_followup(text: str, ctx: dict) -> bool:
    """账单场景的追问："有没有不对劲的" → bill_analysis + focus=anomaly。"""
    last = _last_intent(ctx)
    name = last.get("name") if isinstance(last, dict) else last
    return name == "bill_analysis" and bool(_ANOMALY_RE.search(text))


def _has_subscription_context(ctx: dict) -> bool:
    if isinstance(ctx.get("subscriptions"), list) and ctx["subscriptions"]:
        return True
    entities = ctx.get("recent_entities")
    if isinstance(entities, dict):
        if isinstance(entities.get("subscriptions"), list) and entities["subscriptions"]:
            return True
        last = entities.get("last_intent")
        if isinstance(last, dict) and last.get("name") == "subscription":
            return True
    last = _last_intent(ctx)
    return isinstance(last, dict) and last.get("name") == "subscription"


def _wants_recent(text: str) -> bool:
    return bool(re.search(r"上次(?:那|这)(?:个|笔|张|单)|上次那个|那个订单", text))


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# 上下文实体 / 订阅候选
# ---------------------------------------------------------------------------
_ENTITY_INTENT_SYNONYMS = {
    "bill": "bill_analysis",
    "bill_analysis": "bill_analysis",
    "sub": "subscription",
    "subscription": "subscription",
    "wealth": "wealth",
    "card": "card",
    "transfer": "transfer",
    "standing": "standing_plan",
    "standing_plan": "standing_plan",
    "plan": "standing_plan",
}


def _entity_name(obj: dict) -> str | None:
    for key in ("intent", "name", "type", "kind"):
        value = obj.get(key)
        if isinstance(value, str):
            mapped = _ENTITY_INTENT_SYNONYMS.get(value.lower())
            if mapped:
                return mapped
    if obj.get("sub_id") or obj.get("sub_ids") or obj.get("subscriptions"):
        return "subscription"
    if obj.get("card_id") or obj.get("card_tail"):
        return "card"
    if obj.get("product_id") or obj.get("holding_id"):
        return "wealth"
    if obj.get("goal") or obj.get("birthday"):
        return "standing_plan"
    if obj.get("range") or obj.get("month") or obj.get("bill_month"):
        return "bill_analysis"
    if obj.get("payee") or obj.get("contact_id") or obj.get("txn_id") or obj.get("txn_no"):
        return "transfer"
    return None


def _as_cents(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        candidates = _amount_candidates(_normalize(value))
        if candidates:
            return max(candidates, key=lambda c: c["score"])["value"]
    return None


def _entity_slots(obj: dict) -> dict:
    slots: dict = {}
    raw_slots = obj.get("slots")
    if isinstance(raw_slots, dict):
        slots.update(deepcopy(raw_slots))

    amount = _as_cents(obj.get("amount"))
    if amount:
        slots.setdefault("amount", amount)
    for key in ("payee", "range", "merchant", "goal", "when", "action"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            slots.setdefault(key, value)
    for key in ("sub_id", "card_id", "product_id", "holding_id", "contact_id"):
        value = obj.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            slots.setdefault(key, value)
    sub_ids = obj.get("sub_ids") or obj.get("subscription_ids")
    if isinstance(sub_ids, list):
        slots.setdefault("sub_ids", [x for x in sub_ids if isinstance(x, int)])
    subscriptions = obj.get("subscriptions")
    if isinstance(subscriptions, list):
        ids = []
        for item in subscriptions:
            if isinstance(item, dict):
                sid = item.get("id") or item.get("sub_id")
                if isinstance(sid, int) and not isinstance(sid, bool):
                    ids.append(sid)
            elif isinstance(item, int) and not isinstance(item, bool):
                ids.append(item)
        if ids:
            slots.setdefault("sub_ids", ids)
    budget = _as_cents(obj.get("budget"))
    if budget:
        slots.setdefault("budget", budget)
    new_limit = _as_cents(obj.get("new_limit"))
    if new_limit:
        slots.setdefault("new_limit", new_limit)
    count = obj.get("payer_count") or obj.get("person_count")
    if isinstance(count, int) and not isinstance(count, bool):
        slots.setdefault("payer_count" if obj.get("payer_count") else "person_count", count)
    if isinstance(obj.get("payee"), str) and obj["payee"]:
        slots.setdefault("payee", obj["payee"])
    return slots


def _recent_resolution(ctx: dict) -> tuple[str | None, dict, dict | None]:
    entities = ctx.get("recent_entities")
    candidates: list[dict] = []
    if isinstance(entities, dict):
        last = entities.get("last_intent")
        if isinstance(last, dict):
            candidates.append(last)
        candidates.append(entities)
        for key in ("last_entity", "entity", "current_entity", "last"):
            value = entities.get(key)
            if isinstance(value, dict):
                candidates.append(value)
    elif isinstance(entities, list):
        candidates.extend([x for x in reversed(entities) if isinstance(x, dict)])
    last_intent = ctx.get("last_intent")
    if isinstance(last_intent, dict):
        candidates.append(last_intent)

    for obj in candidates:
        name = _entity_name(obj)
        if name:
            return name, _entity_slots(obj), obj
    return None, {}, None


def _collect_subscriptions(ctx: dict) -> tuple[list[int], list[str]]:
    raw_lists: list = []
    if isinstance(ctx.get("subscriptions"), list):
        raw_lists.append(ctx["subscriptions"])
    entities = ctx.get("recent_entities")
    if isinstance(entities, dict):
        for key in ("subscriptions", "subs", "sub_ids", "subscription_ids"):
            value = entities.get(key)
            if isinstance(value, list):
                raw_lists.append(value)
        if "sub_id" in entities:
            raw_lists.append([entities["sub_id"]])
        for key in ("last_entity", "entity", "last"):
            value = entities.get(key)
            if isinstance(value, dict):
                for sub_key in ("subscriptions", "sub_ids"):
                    if isinstance(value.get(sub_key), list):
                        raw_lists.append(value[sub_key])
                if value.get("sub_id"):
                    raw_lists.append([value["sub_id"]])
    elif isinstance(entities, list):
        raw_lists.append(entities)
    if isinstance(ctx.get("sub_ids"), list):
        raw_lists.append(ctx["sub_ids"])

    ids: list[int] = []
    merchants: list[str] = []
    for items in raw_lists:
        for item in items:
            if isinstance(item, dict):
                sid = item.get("id") or item.get("sub_id") or item.get("subscription_id")
                if isinstance(sid, int) and not isinstance(sid, bool) and sid not in ids:
                    ids.append(sid)
                merchant = item.get("merchant") or item.get("name")
                if isinstance(merchant, str) and merchant and merchant not in merchants:
                    merchants.append(merchant)
            elif isinstance(item, int) and not isinstance(item, bool):
                if item not in ids:
                    ids.append(item)
            elif isinstance(item, str) and item.isdigit():
                sid = int(item)
                if sid not in ids:
                    ids.append(sid)
    return ids, merchants


def _recent_value(ctx: dict, keys: tuple[str, ...]):
    entities = ctx.get("recent_entities")
    scopes: list[dict] = []
    if isinstance(entities, dict):
        scopes.append(entities)
        for name in ("last_entity", "entity", "last"):
            value = entities.get(name)
            if isinstance(value, dict):
                scopes.append(value)
    elif isinstance(entities, list):
        scopes.extend([x for x in reversed(entities) if isinstance(x, dict)])
    if isinstance(ctx.get("last_intent"), dict):
        scopes.append(ctx["last_intent"])
    for scope in scopes:
        for key in keys:
            value = scope.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _extract_payers(text: str, ctx: dict) -> tuple[list[str], str | None]:
    """AA 收款人：优先上下文联系人，其次"给张三、李四各转"这类原话。"""
    names: list[str] = []
    span: str | None = None

    def add(name: str) -> None:
        name = name.strip(" \t，,。.!！?？、:：;；")
        if not name or len(name) > 8 or name in _PAYEE_STOP:
            return
        if name not in names:
            names.append(name)

    sources: list = []
    for key in ("payers", "contacts"):
        value = ctx.get(key)
        if isinstance(value, list):
            sources.append(value)
    entities = ctx.get("recent_entities")
    scopes = []
    if isinstance(entities, dict):
        scopes.append(entities)
        for key in ("last_entity", "entity", "last"):
            if isinstance(entities.get(key), dict):
                scopes.append(entities[key])
    elif isinstance(entities, list):
        scopes.extend([x for x in entities if isinstance(x, dict)])
    for scope in scopes:
        for key in ("payers", "contacts", "contact_names"):
            if isinstance(scope.get(key), list):
                sources.append(scope[key])

    for items in sources:
        for item in items:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                add(str(item.get("name") or item.get("alias") or ""))

    m = re.search(r"(?:给|和|与|跟)\s*([\u4e00-\u9fa5A-Za-z、，,和与及\s]{2,30}?)(?:各|一起|AA|平摊|均摊|分摊)", text, re.I)
    if m:
        span = m.group(0)
        for part in re.split(r"[、，,和与及\s]+", m.group(1)):
            add(part)

    # "张三李四各转80" 不拆，避免把两个名字粘成一个
    return names[:7], span


# ---------------------------------------------------------------------------
# 意图打分
# ---------------------------------------------------------------------------
_KEYWORD_RULES = (
    ("transfer", r"转账", 4),
    ("transfer", r"打钱|汇款|付款", 4),
    ("transfer", r"打(?:钱|款|[\d零〇一二两三四五六七八九十百千万][^,。！？]{0,6})给", 4),
    ("transfer", r"转", 3),
    ("transfer", r"再转", 2),
    ("bill_analysis", r"账单", 4),
    ("bill_analysis", r"花哪|钱都去哪|花哪儿|钱花哪|钱去哪", 5),
    ("bill_analysis", r"花钱|花了多少|花多少|花销|开销", 4),
    ("bill_analysis", r"不对劲|异常交易|可疑交易|交易异常|异常消费", 4),
    ("bill_analysis", r"消费", 3),
    ("bill_analysis", r"支出", 3),
    ("bill_analysis", r"流水|明细", 3),
    ("bill_analysis", r"记录", 2),
    ("subscription", r"会员", 4),
    ("subscription", r"订阅", 4),
    ("subscription", r"自动扣费|自动续费", 5),
    ("subscription", r"全关|都关|全部关", 7),
    ("service", r"(撤销|取消|冲正|退回).{0,8}(转账|转的|汇款|那笔)", 10),
    ("subscription", r"代扣", 4),
    ("subscription", r"续费", 4),
    ("subscription", r"扣费", 3),
    ("subscription", r"退订", 4),
    ("wealth", r"理财", 4),
    ("wealth", r"基金", 4),
    ("wealth", r"申购|赎回", 5),
    ("wealth", r"买\s*(?:\d|那个|这个|哪个|基金|理财|产品)", 4),
    ("wealth", r"持仓", 4),
    ("wealth", r"收益|年化|利率", 2),
    ("wealth", r"稳(?:健|一点|赚)", 2),
    ("card", r"挂失", 5),
    ("card", r"额度|限额|交易限制|单笔限制", 6),
    ("card", r"信用卡|借记卡|银行卡|储蓄卡", 4),
    ("card", r"虚拟卡|补卡", 4),
    ("card", r"卡", 2),
    ("card", r"解挂|解锁", 4),
    ("standing_plan", r"生日", 5),
    ("standing_plan", r"纪念日", 5),
    ("standing_plan", r"提醒我|提醒一下", 5),
    ("standing_plan", r"定.{0,3}提醒", 5),
    ("service", r"还信用卡|信用卡还款|还.{0,4}信用卡", 9),
    ("scheduled_transfer", r"(下个月|下周|明天|后天).{0,15}(转|打|汇)", 9),
    ("service", r"还款|还房贷|还贷款", 7),
    ("service", r"风险测评|风险测试|风险承受|测评", 6),
    ("service", r"重置密码|忘记密码|改密码|密码忘了|密码重置", 8),
    ("standing_plan", r"日程|安排(?:一下)?(?:下?个?月|明天|后天|下周|日程|计划|生日|纪念日|的事|事项)", 4),
    ("standing_plan", r"计划", 3),
    ("standing_plan", r"定时|定期", 4),
)


def _score_intents(text: str) -> dict:
    scores = {name: 0 for name in INTENT_NAMES if name != "unknown"}
    for intent, pattern, weight in _KEYWORD_RULES:
        if re.search(pattern, text, re.I):
            scores[intent] += weight
    if _detect_aa(text):
        scores["transfer"] += 6
    if _detect_batch_cancel(text):
        scores["subscription"] += 6
    if "卡" in text and re.search(r"丢|挂失|找不到|解挂|额度", text):
        scores["card"] += 2
    if re.search(r"(?:转账|消费|支出|扣款).{0,4}(?:记录|流水|明细)", text):
        scores["bill_analysis"] += 6
    if re.search(r"(?:生成|来|做).{0,3}(?:一份)?(?:月报|账单报告|年报)", text):
        scores["bill_analysis"] += 3
    return scores


def _pick_intent(text: str, ctx: dict, carry: bool, wants_recent: bool) -> tuple[str, int, bool]:
    """返回 (intent, keyword_score, from_context)。"""
    last = _last_intent(ctx)
    if carry and isinstance(last, dict):
        name = last.get("name")
        if name in INTENT_NAMES and name != "unknown":
            return name, 5, False

    if _is_anomaly_followup(text, ctx):
        return "bill_analysis", 4, True

    if wants_recent:
        name, _, _ = _recent_resolution(ctx)
        if name:
            return name, 4, True

    scores = _score_intents(text)
    best_name = "unknown"
    best_score = 0
    for name in _INTENT_PRIORITY:
        if scores.get(name, 0) > best_score:
            best_name = name
            best_score = scores[name]
    if best_name == "unknown":
        # 兜底：没有明确关键词时，至少尝试用 recent_entities 解析指代
        if wants_recent:
            name, _, _ = _recent_resolution(ctx)
            if name:
                return name, 0, True
        return "unknown", 0, False
    return best_name, best_score, False


# ---------------------------------------------------------------------------
# 各意图的槽位
# ---------------------------------------------------------------------------
def _confidence(name: str, slots: dict, missing: list[str], ambiguity: list[str],
                keyword_score: int, carry: bool, aa: bool, batch: bool,
                from_context: bool) -> float:
    if name == "unknown":
        return 0.2
    if carry:
        base = 0.90
    elif keyword_score >= 6:
        base = 0.84
    elif keyword_score >= 5:
        base = 0.80
    elif keyword_score >= 4:
        base = 0.74
    elif keyword_score >= 3:
        base = 0.66
    elif keyword_score >= 2:
        base = 0.58
    else:
        base = 0.52
    if slots.get("amount") or slots.get("budget") or slots.get("new_limit"):
        base += 0.04
    if slots.get("payee"):
        base += 0.03
    if slots.get("range"):
        base += 0.03
    if aa:
        base += 0.04
    if batch:
        base += 0.04
    if from_context:
        base += 0.03
    base -= 0.08 * len(missing)
    base -= 0.03 * len(ambiguity)
    return round(min(max(base, 0.30), 0.98), 2)


def _unknown(confidence: float = 0.20, ambiguity: list[str] | None = None) -> dict:
    return {
        "name": "unknown",
        "slots": {},
        "missing": [],
        "ambiguity": ambiguity or ["未识别意图"],
        "spans": {},
        "confidence": round(min(max(confidence, 0.0), 0.30), 2),
    }


def _parse(text: str, ctx: dict) -> dict:
    original = text if isinstance(text, str) else ""
    text = _normalize(original)
    if not text:
        return _unknown(0.10, ["输入为空或不是文本"])

    carry = _detect_carry(text)
    wants_recent = _wants_recent(text)
    aa = _detect_aa(text)
    batch = _detect_batch_cancel(text)
    followup = _is_anomaly_followup(text, ctx) and not carry
    if not batch and _has_subscription_context(ctx) and re.search(r"不需要了|不用了|都不要了", text):
        batch = True

    name, keyword_score, from_context = _pick_intent(text, ctx, carry, wants_recent)

    # 强制语义：AA -> transfer；批量退订 -> subscription
    if aa:
        name = "transfer"
    if batch and name not in ("standing_plan",):
        name = "subscription"

    last = _last_intent(ctx)
    if not isinstance(last, dict):
        last = None
    recent_name, recent_slots, _recent_obj = _recent_resolution(ctx) if wants_recent else (None, {}, None)

    # 继承：先铺底，再用本轮显式槽位覆盖
    slots: dict = {}
    spans: dict = {}
    if carry and last and isinstance(last.get("slots"), dict):
        slots.update(deepcopy(last["slots"]))
    elif wants_recent and recent_slots:
        slots.update(deepcopy(recent_slots))
    elif followup and last and isinstance(last.get("slots"), dict):
        # 账单追问沿用上一轮的 range（属于非资金上下文参数）
        slots.update(deepcopy(last["slots"]))

    missing: list[str] = []
    ambiguity: list[str] = []
    range_value, range_span = _extract_range(text)
    payee, payee_span = _extract_payee(text)
    candidates = _amount_candidates(text)

    if carry and not last:
        ambiguity.append("没有可继承的上文")

    if name == "scheduled_transfer":
        payee, payee_span = _extract_payee(text)
        if payee:
            slots["payee"] = payee
            spans["payee"] = payee_span or payee
        else:
            missing.append("payee")
        chosen = _pick_amount(text, candidates, "transfer")
        if chosen:
            slots["amount"] = chosen["value"]
            spans["amount"] = chosen["span"]
        else:
            missing.append("amount")
        m_when = re.search(
            r"(下个月\s*\d{1,2}\s*[号日]|下周[一二三四五六日天]|明天|后天|"
            r"\d{1,2}\s*月\s*\d{1,2}\s*[号日]|每月\s*\d{1,2}\s*[号日])", text)
        if m_when:
            slots["when"] = m_when.group(0)
            spans["when"] = m_when.group(0)
        else:
            missing.append("when")

    elif name == "transfer":
        aa = aa or slots.get("mode") == "collection"
        slots["mode"] = "collection" if aa else "single"
        if aa:
            person_count, person_span = _extract_person_count(text)
            payers, payer_span = _extract_payers(text, ctx)

            # 显式人均表达：各转80 / 每人80 / 人均80
            per = None
            per_span = None
            m_per = re.search(r"(?:各转|各付|各出|每人|每个人|人均)\s*", text)
            if m_per:
                for c in candidates:
                    if m_per.end() - 1 <= c["start"] <= m_per.end() + 8:
                        per, per_span = c["value"], c["span"]
                        break

            total = None
            total_span = None
            if per is None:
                chosen = _pick_amount(text, candidates, "aa")
                if chosen:
                    total, total_span = chosen["value"], chosen["span"]
                    if person_count and person_count > 0:
                        # total 已经是分，这里只做整数分除法，不能再 ×100
                        per = int(
                            (Decimal(total) / Decimal(person_count)).quantize(
                                Decimal("1"), rounding=ROUND_HALF_UP
                            )
                        )
            else:
                if person_count and person_count > 0:
                    total = per * person_count
                elif payers:
                    total = per * len(payers)

            if per is not None:
                slots["amount"] = per
                spans["amount"] = per_span or total_span or ""
            if total is not None:
                slots["total_amount"] = total
                spans["total_amount"] = total_span or per_span or ""
            if person_count is not None:
                slots["person_count"] = person_count
                if person_span:
                    spans["person_count"] = person_span

            payer_count = None
            if payers:
                payer_count = len(payers)
                slots["payers"] = payers
                if payer_span:
                    spans["payers"] = payer_span
            elif person_count is not None:
                payer_count = max(person_count - 1, 0)
            if payer_count is not None:
                slots["payer_count"] = payer_count

            if slots.get("amount") is None:
                missing.append("amount")
            if payer_count is None and not payers:
                missing.append("payers")
            if person_count is None and per is not None:
                ambiguity.append("AA 人数未指定")
            if not payers:
                ambiguity.append("AA 收款人未指定，将按最近联系人匹配")
        else:
            chosen = _pick_amount(text, candidates, "transfer")
            if chosen:
                slots["amount"] = chosen["value"]
                spans["amount"] = chosen["span"]
            if payee:
                slots["payee"] = payee
                spans["payee"] = payee_span or payee
            if slots.get("amount") is None:
                missing.append("amount")
            if not slots.get("payee"):
                missing.append("payee")
                ambiguity.append("收款人未指明")

    elif name == "bill_analysis":
        anomaly_query = bool(_ANOMALY_RE.search(text)) and not _BILL_STATS_RE.search(text)
        if followup or anomaly_query:
            slots["focus"] = "anomaly"
            m_anomaly = _ANOMALY_RE.search(text)
            if m_anomaly:
                spans["focus"] = m_anomaly.group(0)
        if range_value:
            slots["range"] = range_value
            spans["range"] = range_span or ""
        elif not slots.get("range"):
            slots["range"] = "this_month"
        if re.search(r"报告|月报|年报", text):
            slots["report"] = True
        if not slots.get("range"):
            missing.append("range")

    elif name == "subscription":
        if batch:
            slots["batch"] = True
            slots["action"] = "cancel"
        else:
            if re.search(r"取消|退订|关掉|关闭|停掉|停了|退了|不要了|不再续", text):
                slots["action"] = "cancel"
            elif re.search(r"续费|提醒", text):
                slots["action"] = "renew"
            else:
                slots.setdefault("action", "query")
        if slots.get("action") == "cancel":
            ids, merchants = _collect_subscriptions(ctx)
            if ids:
                slots["sub_ids"] = ids
                slots["batch_count"] = len(ids)
            if merchants:
                slots.setdefault("merchants", merchants)
            if batch:
                slots.setdefault("batch_count", len(slots.get("sub_ids") or []))
                if not slots.get("sub_ids"):
                    ambiguity.append("批量取消对象未指定，需先查询订阅列表")
            elif not slots.get("sub_ids"):
                sid = _recent_value(ctx, ("sub_id", "subscription_id"))
                if isinstance(sid, int) and not isinstance(sid, bool):
                    slots["sub_id"] = sid
                merchant = _recent_value(ctx, ("merchant",))
                if isinstance(merchant, str) and merchant:
                    slots.setdefault("merchant", merchant)
        if range_value:
            slots["range"] = range_value
            spans["range"] = range_span or ""

    elif name == "wealth":
        if re.search(r"赎回|卖出|取回|到期", text):
            slots["action"] = "redeem"
        elif re.search(r"买|申购|买入|购买|投", text):
            slots["action"] = "purchase"
        else:
            slots["action"] = "query"

        risk = re.search(r"[Rr][1-5]", text)
        if risk:
            slots["risk_level"] = risk.group(0).upper()
            spans["risk_level"] = risk.group(0)
        elif re.search(r"稳健|低风险|保守", text):
            slots["risk_level"] = "稳健"
            spans["risk_level"] = "稳健"
        elif re.search(r"高风险|激进", text):
            slots["risk_level"] = "高风险"
            spans["risk_level"] = "高风险"

        chosen = _pick_amount(text, candidates, "wealth")
        if chosen and slots.get("action") in ("purchase", "redeem"):
            slots["amount"] = chosen["value"]
            spans["amount"] = chosen["span"]
        product_id = _recent_value(ctx, ("product_id",))
        if isinstance(product_id, int) and not isinstance(product_id, bool):
            slots["product_id"] = product_id
        holding_id = _recent_value(ctx, ("holding_id",))
        m_holding = re.search(r"第\s*(\d+)\s*笔", text)
        if m_holding:
            holding_id = int(m_holding.group(1))
            spans["holding_id"] = m_holding.group(0)
        if isinstance(holding_id, int) and not isinstance(holding_id, bool):
            slots["holding_id"] = holding_id
        if slots.get("action") == "purchase":
            if slots.get("amount") is None:
                missing.append("amount")
            if not slots.get("product_id"):
                missing.append("product")
        elif slots.get("action") == "redeem":
            if not slots.get("holding_id"):
                ambiguity.append("未指定要赎回的持仓")

    elif name == "card":
        if re.search(r"挂失|丢了|丢失|找不到|被盗", text):
            slots["action"] = "lost"
        elif re.search(r"解挂|解锁|解冻", text):
            slots["action"] = "unlock"
        elif re.search(r"额度|限额|交易限制|单笔限制", text) and re.search(r"调|改|提|降|设置|调整|设为|设成|设置成|调成", text):
            slots["action"] = "limit"
        elif re.search(r"申请|虚拟卡|补卡|办卡|新卡", text):
            slots["action"] = "apply"
        else:
            slots["action"] = "query"

        m = re.search(r"尾号\s*(\d{4})", text) or re.search(r"卡号.{0,8}?(\d{4})", text)
        if m:
            slots["card_tail"] = m.group(1)
            spans["card_tail"] = m.group(0)
        card_id = _recent_value(ctx, ("card_id",))
        if isinstance(card_id, int) and not isinstance(card_id, bool):
            slots["card_id"] = card_id
        if slots.get("action") == "limit":
            chosen = _pick_amount(text, candidates, "card")
            if chosen:
                slots["new_limit"] = chosen["value"]
                spans["new_limit"] = chosen["span"]
            else:
                m_limit = re.search(r"(?:调到|调成|调整为|提到|设为|设成|设置成|改到|改写成)\s*(\d+(?:\.\d+)?)\s*(万)?", text)
                if m_limit:
                    value = float(m_limit.group(1)) * (10000 if m_limit.group(2) else 1)
                    slots["new_limit"] = int(round(value * 100))
                    spans["new_limit"] = m_limit.group(0)
                else:
                    missing.append("new_limit")

    elif name == "standing_plan":
        if re.search(r"生日", text):
            slots["goal"] = "生日准备"
        elif re.search(r"纪念日", text):
            slots["goal"] = "纪念日准备"
        elif re.search(r"提醒", text):
            slots["goal"] = "定时提醒"
        else:
            slots["goal"] = "长期计划"
        if payee:
            slots["payee"] = payee
            spans["payee"] = payee_span or payee
        chosen = _pick_amount(text, candidates, "standing_plan", prefer_budget=True)
        if chosen:
            slots["budget"] = chosen["value"]
            spans["budget"] = chosen["span"]
        when = None
        m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", text)
        if m:
            when = f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
            spans["when"] = m.group(0)
        else:
            m = re.search(r"下个月|这个月|明天|后天|下周", text)
            if m:
                when = m.group(0)
                spans["when"] = m.group(0)
        if when:
            slots["when"] = when
        if re.search(r"提醒", text) and not when:
            ambiguity.append("提醒时间未指明")

    elif name == "service":
        if re.search(r"(撤销|取消|冲正|退回).{0,8}(转账|转的|汇款|那笔)", text):
            slots["action"] = "reverse"
            missing = [m for m in missing if m != "action"]
        elif re.search(r"信用卡", text) and re.search(r"还|还款", text):
            slots["action"] = "repay_card"
            chosen = _pick_amount(text, candidates, "transfer")
            if chosen:
                slots["amount"] = chosen["value"]
                spans["amount"] = chosen["span"]
            else:
                missing.append("amount")
        elif re.search(r"还房贷|还贷款|还款", text):
            slots["action"] = "repay"
            chosen = _pick_amount(text, candidates, "transfer")
            if chosen:
                slots["amount"] = chosen["value"]
                spans["amount"] = chosen["span"]
        elif re.search(r"密码", text):
            slots["action"] = "password"
            slots["channel"] = "email" if re.search(r"邮箱|email", text, re.I) else "sms"
        else:
            slots["action"] = "risk"
            if re.search(r"保守|谨慎|低风险", text):
                slots["level"] = "保守型"
            elif re.search(r"进取|激进|高风险", text):
                slots["level"] = "进取型"
            else:
                slots["level"] = "稳健型"

    else:
        return _unknown(0.20, ["未识别意图"])

    # 继承的指代/省略信息
    if carry:
        ambiguity.append("已按上一轮上下文继承")
    if name == "transfer" and slots.get("mode") == "collection" and not aa:
        aa = True

    missing = _unique(missing)
    ambiguity = _unique(ambiguity)
    result = {
        "name": name,
        "slots": {k: v for k, v in slots.items() if v not in (None, "", [])},
        "missing": missing,
        "ambiguity": ambiguity,
        "spans": {k: v for k, v in spans.items() if v},
        "confidence": _confidence(
            name, slots, missing, ambiguity, keyword_score,
            carry, aa, batch, from_context,
        ),
    }
    if carry:
        result["carry_over"] = True
        carry_text = ""
        if isinstance(ctx.get("last_text"), str):
            carry_text = ctx["last_text"]
        elif isinstance(last, dict):
            carry_text = last.get("text") or last.get("carry_over_text") or ""
        if carry_text:
            result["carry_over_text"] = carry_text
    if batch:
        result["batch"] = True
    if from_context:
        result["from_context"] = True
    return result


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------
def parse(text: str, ctx: dict | None = None) -> dict:
    """把用户原话解析为 Intent dict。

    Args:
        text: 用户本轮原话。
        ctx:  {"user_id", "recent_entities", "last_intent", "channel"}，
              允许缺省/非 dict。

    Returns:
        契约 §8 的 Intent；任何情况下都不抛异常。
    """
    try:
        safe_ctx = ctx if isinstance(ctx, dict) else {}
        return _parse(normalize(text), safe_ctx)
    except Exception:  # noqa: BLE001 —— 契约要求 NLU 绝不把异常抛给编排器
        return _unknown(0.0, ["解析异常，已降级为未知意图"])
