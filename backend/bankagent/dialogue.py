"""会话层：处理问候、感谢、确认语气、能力询问、转人工、闲聊等"不是业务指令"的话。

设计原则：
- 只认**整句**是社交/元对话的情况，避免把"这三个会员我早就不用了"误判；
- 不产生计划、不触发策略、不动账；只回一句话，并记一条审计；
- 用户说"好的/嗯"时，如果正有确认卡待处理，要提示"点按钮才算确认"（安全语义）。
"""

from __future__ import annotations

import re
from typing import Any

_PUNCT = r"[!！。,.，、?？~～…\s]*$"

RULES: list[tuple[str, str]] = [
    ("greeting", r"^(你好|您好|hi|hello|嗨|哈喽|在吗|在不在|有人吗|早上好|上午好|下午好|晚上好)" + _PUNCT),
    ("thanks", r"^(谢谢|多谢|感谢|谢了|thanks|thank\s*you|辛苦了)" + _PUNCT),
    ("goodbye", r"^(再见|拜拜|bye|byebye|回头见|下次聊|晚安)" + _PUNCT),
    ("affirm", r"^(好的?|好|嗯+|哦+|噢+|行|可以|ok|okay|收到|明白了?|知道了)" + _PUNCT),
    ("identity", r"(你是谁|你是什么|你叫什么|你哪位)"),
    ("capability", r"(你能做什么|你会什么|你有什么功能|能帮我做什么|怎么用|有啥功能)"),
    ("handoff", r"(转人工|人工客服|找客服|叫客服|投诉|举报|找经理)"),
    ("complain", r"(太复杂|太难用|不好用|麻烦死|什么破|烦死)"),
    ("offtopic", r"(天气|气温|下雨|新闻|股票|几点|星期几|笑话|唱歌)"),
    ("offdomain", r"((写|作).{0,12}(诗|词)|代码|编程|翻译|作业|论文|菜谱|做菜|星座|游戏|电影推荐|讲故事|体检|处方|法律|离婚|算命|讲个笑话)"),
    ("lost", r"^(我忘了|忘了|不知道|随便|都行|你看着办|帮我看看|看情况)" + _PUNCT),
]

COMPILED = [(kind, re.compile(pat, re.IGNORECASE)) for kind, pat in RULES]


def match(text: str) -> str | None:
    t = (text or "").strip()
    if not t or len(t) > 30:
        return None
    for kind, rx in COMPILED:
        if rx.search(t):
            return kind
    return None


def reply_for(kind: str, ctx: dict[str, Any] | None = None) -> str:
    ctx = ctx or {}
    has_pending = bool(ctx.get("has_pending"))
    level = ctx.get("pending_level")
    last = ctx.get("last_intent") or {}

    if kind == "greeting":
        if last.get("name") == "bill_analysis":
            return "你好，又见面了。上次给你看了本月支出，要接着看异常交易，还是办别的事？"
        return ("你好，我是你的银行智能助手。你可以直接说：\n"
                "· 我这个月钱都去哪了\n· 转 500 给我妈\n· 把没用的会员退掉")
    if kind == "thanks":
        return "不客气。还有要办的吗？"
    if kind == "goodbye":
        return "好，需要时随时叫我。"
    if kind == "affirm":
        if has_pending and level == "yellow":
            return "收到。确认请点上面确认卡里的「确认执行」——**文字回复不算授权**，这一点是故意的。"
        if has_pending and level == "red":
            return "收到。这笔需要先完成强验证，点上面的验证弹窗输入验证码就可以。"
        return "好。要办什么直接说，比如「查下本月账单」。"
    if kind == "identity":
        return ("我是这家银行的 AI 智能助手（演示环境，全部为合成数据）。\n"
                "和其它助手不一样的一点：我不持有执行权——每笔操作都要过策略引擎，"
                "涉及资金还要你点确认或完成强验证。")
    if kind == "capability":
        return ("我能办六类事：\n"
                "① 查余额 / 流水 / 本月账单与异常消费\n"
                "② 转账、AA 收款、定时转账\n"
                "③ 订阅与自动扣费查询、逐条取消\n"
                "④ 理财查询、申购/赎回\n"
                "⑤ 卡片挂失、解挂、额度调整\n"
                "⑥ 长期计划（生日、房租这类到点提醒并执行）")
    if kind == "handoff":
        return ("已经帮你转接人工客服（演示环境为模拟坐席，不会真的外呼）。\n"
                "你也可以直接告诉我要办什么，大部分事我当场就能办完。")
    if kind == "complain":
        return "抱歉让你觉得麻烦。你可以直接说目的，我来拆步骤；比如「把这个月账单发我」。"
    if kind == "offtopic":
        return "这个我查不到。不过账户上的事我都在行——要看本月账单，还是先查余额？"
    if kind == "offdomain":
        return ("这个不属于银行业务，我就不展开了。\n"
                "我能帮你办的是：查账与账单分析、转账/AA、订阅管理、理财查询与申购、卡片挂失与额度、长期计划。")
    if kind == "lost":
        return "没关系，我们从头来。你想办哪件事？查账、转账、退订阅、查理财、卡片，都可以。"
    return "我在。你说要办什么就行。"


def fallback_reply() -> str:
    return ("这个我还理解不了。你可以换个说法，比如：\n"
            "· 我这个月钱都去哪了\n· 再给我妈转 800\n· 我卡丢了")
