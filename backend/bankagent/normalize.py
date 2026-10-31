"""文本归一化：口语、错别字、中文数字、时间说法 → 解析层能直接处理的规范形式。

不依赖任何第三方库，也不调模型；只做"把说法变标准"，不改变用户原意。
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- 领域错别字 / 同音字
TYPO_MAP = {
    "转帐": "转账", "帐户": "账户", "帐号": "账号", "帐单": "账单", "支负": "支付", "余鹅": "余额",
    "云储存": "云存储", "视屏": "视频", "冲值": "充值", "续沸": "续费", "花被": "花呗",
    "信用卡片": "信用卡", "挂失卡": "挂失", "解挂卡": "解挂", "理财通": "理财",
}

# ---------------------------------------------------------------- 口语 → 书面
COLLOQUIAL = [
    ("瞅一眼", "看一下"), ("瞅瞅", "看看"), ("瞄一眼", "看一下"), ("瞧一瞧", "看一下"),
    ("咋办", "怎么办"), ("咋", "怎么"), ("啥", "什么"), ("哪样", "什么"),
    ("查查", "查一下"), ("看看看", "看看"),
    ("给我整", "给我办"), ("整一个", "办一个"), ("搞一下", "办一下"),
    ("多少钱儿", "多少钱"), ("多少米", "多少钱"), ("几块钱", "多少钱"),
    ("退了", "取消"), ("退掉", "取消"), ("停掉", "取消"), ("关掉", "取消"),
    ("不要了", "取消"), ("不用了", "取消"), ("停了吧", "取消"),
    ("老妈", "妈"), ("母亲", "妈"), ("娘", "妈"), ("媳妇", "老婆"), ("爱人", "老婆"),
    ("房东先生", "房东"), ("我老爸", "爸"), ("我老爹", "爸"),
    ("手头有点紧", "余额不足"), ("没银子", "余额不足"),
]

# ---------------------------------------------------------------- 时间说法
TIME_MAP = [
    ("上上周", "前两周"), ("这礼拜", "本周"), ("这周", "本周"), ("下礼拜", "下周"),
    ("上周三", "上周"), ("上周五", "上周"), ("上礼拜", "上周"),
    ("月初", "这个月"), ("月底", "这个月"), ("月中", "这个月"),
    ("本月初", "这个月"), ("本月末", "这个月"),
    ("年头", "今年"), ("去年底", "去年"),
]

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}


def cn_to_int(text: str) -> int | None:
    """中文数字→整数，支持"五百二 / 一千二 / 一万二 / 一万二千三"这类口语省略。"""
    if not text:
        return None
    total, section, number = 0, 0, 0
    last_unit = 0
    for ch in text:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _UNITS:
            unit = _UNITS[ch]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section, number = 0, 0
            else:
                if number == 0:
                    number = 1
                section += number * unit
                number = 0
            last_unit = unit
        elif ch in ("元", "块"):
            break
        else:
            return None
    if number:
        # 口语省略：五百二 → 520；一千二 → 1200；一万二 → 12000
        if last_unit >= 100 and number < 10:
            section += number * (last_unit // 10)
        else:
            section += number
    return total + section or None


_CN_NUM = r"[零〇一二两三四五六七八九十百千万亿]+"
_AMOUNT_CN = re.compile(rf"({_CN_NUM})\s*(?:元|块钱|块)?")
_WAN = re.compile(r"(\d+(?:\.\d+)?)\s*[wW万]")


def normalize_amounts(text: str) -> str:
    def _wan(m):
        return str(int(float(m.group(1)) * 10000))

    out = _WAN.sub(_wan, text)
    # 只在后面跟"元/块"或位于"转/打/给"之后时替换中文数字，避免误伤"一起""十分"等
    def _cn(m):
        value = cn_to_int(m.group(1))
        if value is None or value < 10:
            return m.group(0)
        return str(value)

    out = re.sub(rf"({_CN_NUM})\s*(元|块钱|块)", lambda m: (_cn(m) + m.group(2)) if _cn(m) != m.group(1) else m.group(0), out)
    # "转 五百二 给妈 / 花了一万二"：动词后的中文数字也换算（不要求带单位）
    verbs = r"(转|转给|给|打|付|还|存|取|花|花了|用了|消费|借)"
    out = re.sub(rf"{verbs}\s*({_CN_NUM})(?=\s|给|到|元|块|，|。|、|$)",
                 lambda m: m.group(1) + (str(cn_to_int(m.group(2))) if (cn_to_int(m.group(2)) or 0) >= 10 else m.group(2)),
                 out)
    return out


def normalize(text: str) -> str:
    """归一化入口：宽窄字符、错别字、口语词、时间、金额。"""
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", str(text)).strip()
    out = re.sub(r"\s+", " ", out)
    for src, dst in TYPO_MAP.items():
        out = out.replace(src, dst)
    for src, dst in COLLOQUIAL:
        out = out.replace(src, dst)
    for src, dst in TIME_MAP:
        out = out.replace(src, dst)
    # "给张三打 100 块" → "给张三转 100 块"（打/汇 在转账语境里等价）
    out = re.sub(r"(给[^，。\s]{1,8})\s*(打|汇)\s*(\d)", r"\1转\3", out)
    out = normalize_amounts(out)
    return out.strip()
