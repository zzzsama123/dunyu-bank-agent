"""生成盾语答辩 PPT（16:9，深蓝金，含演讲备注）。"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliverables"
OUT.mkdir(exist_ok=True)

NAVY = RGBColor(0x0D, 0x2B, 0x4E)
NAVY_2 = RGBColor(0x12, 0x3A, 0x63)
GOLD = RGBColor(0xE6, 0xC9, 0x8B)
GOLD_DARK = RGBColor(0x8A, 0x6A, 0x2F)
CREAM = RGBColor(0xF7, 0xF4, 0xEE)
INK = RGBColor(0x16, 0x2A, 0x3A)
MUTED = RGBColor(0x6B, 0x7B, 0x88)
GREEN = RGBColor(0x2E, 0x7D, 0x5B)
RED = RGBColor(0xA8, 0x3A, 0x32)
AMBER = RGBColor(0xB0, 0x7A, 0x1E)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]
FONT = "Microsoft YaHei"


def bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def text(slide, x, y, w, h, value, size=20, color=INK, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.1):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    lines = str(value).split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        r = p.add_run()
        r.text = line
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
    return box


def rect(slide, x, y, w, h, fill, line=None, radius=True, line_w=1.2):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(line_w)
    if radius:
        try:
            shp.adjustments[0] = 0.08
        except Exception:
            pass
    shp.shadow.inherit = False
    return shp


def footer(slide, page, dark=False):
    color = RGBColor(0xC9, 0xD3, 0xDD) if dark else MUTED
    text(slide, 0.6, 7.12, 6.0, 0.25, "盾语 · 银行 AI 智能体（仿真环境）", 9, color)
    text(slide, 11.7, 7.12, 1.0, 0.25, f"{page:02d}", 9, color, align=PP_ALIGN.RIGHT)


def title(slide, kicker, heading, page):
    bg(slide, CREAM)
    rect(slide, 0.6, 0.55, 0.055, 0.45, GOLD, radius=False)
    text(slide, 0.78, 0.48, 11.8, 0.3, kicker, 10, GOLD_DARK, True)
    text(slide, 0.78, 0.78, 11.8, 0.6, heading, 28, NAVY, True)
    footer(slide, page)


def picture(slide, path, x, y, w, h, border=GOLD):
    path = ROOT / path
    if not path.exists():
        return
    with Image.open(path) as im:
        iw, ih = im.size
    scale = min(w / iw, h / ih)
    pw, ph = iw * scale, ih * scale
    px, py = x + (w - pw) / 2, y + (h - ph) / 2
    rect(slide, px - 0.06, py - 0.06, pw + 0.12, ph + 0.12, RGBColor(0x0B, 0x24, 0x40), border, radius=True, line_w=1.4)
    slide.shapes.add_picture(str(path), Inches(px), Inches(py), Inches(pw), Inches(ph))


def notes(slide, value):
    slide.notes_slide.notes_text_frame.text = value


narration = {}

# 1 Cover
s = prs.slides.add_slide(BLANK)
bg(s, NAVY)
rect(s, 0, 0, 0.14, 7.5, GOLD, radius=False)
text(s, 0.95, 0.55, 10.8, 0.3, "Prometheus Ultra｜普罗米修斯 · 极致", 11, GOLD, True)
text(s, 0.95, 0.92, 6.0, 0.25, "DUNYU · 盾语 · 银行 AI 智能体", 9, RGBColor(0xC9, 0xD3, 0xDD))
text(s, 0.95, 1.55, 9.4, 1.1, "盾语", 66, WHITE, True)
text(s, 0.98, 2.75, 10.5, 0.55, "银行 AI 智能体 · 仿真环境", 25, GOLD)
text(s, 0.98, 3.6, 8.6, 0.5, "听得懂，也守得住。", 24, RGBColor(0xDD, 0xE6, 0xEF))
text(s, 0.98, 4.75, 10.8, 0.7,
     "一句话办理账单、订阅、转账与长期计划\n权限分级 · 策略裁决 · 条件 DAG · 全链路哈希链审计",
     15, RGBColor(0xC9, 0xD3, 0xDD), spacing=1.35)
rect(s, 0.98, 6.15, 2.1, 0.38, RGBColor(0x16, 0x3B, 0x62), RGBColor(0x3A, 0x5C, 0x7B))
text(s, 1.15, 6.22, 1.8, 0.22, "零 Key 可演示", 10, GOLD)
text(s, 3.25, 6.22, 5.8, 0.25, "不连接真实银行 · 不产生真实资金", 10, RGBColor(0xC9, 0xD3, 0xDD))
notes(s, "大家好，我们是盾语。一个仿真环境下的银行 AI 智能体。今天用十分钟，讲清楚三件事：它怎么听懂人话，怎么保证不办错，以及怎么证明它守得住。")
narration["1"] = "大家好，我们是盾语，一个仿真环境下的银行 AI 智能体。今天用十分钟，讲清楚三件事：它怎么听懂人话，怎么保证不办错，以及怎么证明它守得住。"

# 2 Why
s = prs.slides.add_slide(BLANK)
title(s, "WHY · 问题", "银行 Agent 的难点，不是多接一个模型", 2)
cards = [
    ("听不懂", "口语、省略、错字、复合句\n规则覆盖不全，硬猜会办错"),
    ("办错账", "刚还完 7,800，进度仍显示旧值\n金额、账单、订阅必须账实一致"),
    ("越权/幻觉", "一句话绕过验证？\n模型编造回执？必须可证伪"),
]
for i, (h, body) in enumerate(cards):
    x = 0.8 + i * 4.05
    rect(s, x, 2.05, 3.65, 1.9, WHITE, RGBColor(0xDD, 0xD6, 0xC8))
    text(s, x + 0.28, 2.25, 3.1, 0.35, h, 17, RED if i != 1 else AMBER, True)
    text(s, x + 0.28, 2.72, 3.1, 0.95, body, 12, MUTED, spacing=1.35)
rect(s, 0.8, 4.35, 11.7, 1.05, NAVY)
text(s, 1.05, 4.52, 11.2, 0.35, "盾语的答案：理解与授权分离。", 20, GOLD, True)
text(s, 1.05, 4.92, 11.2, 0.28, "模型只负责“听懂/提议”，策略、确认、执行、审计由确定性代码负责。", 13, WHITE)
text(s, 0.8, 5.75, 11.6, 0.5, "对话主链路：消息 → 理解 → 计划 DAG → 七条校验 → 策略三级 → 确认/强验证 → 执行 → 事实账本 → 哈希链审计", 11, MUTED)
notes(s, "银行 Agent 的难点不是多接一个模型，而是三件事：听不懂、办错账、越权和幻觉。我们的答案是理解与授权分离。")
narration["2"] = "银行 Agent 的难点不是多接一个模型，而是三件事：听不懂、办错账、越权和幻觉。我们的答案是理解与授权分离。"

# 3 Architecture
s = prs.slides.add_slide(BLANK)
title(s, "ARCHITECTURE · 架构", "五层架构：每一层都可替换、可审计", 3)
layers = [
    ("交互层", "Web / IM / 语音", "会话、确认卡、审计回放"),
    ("理解层", "规则 + 语义帧 + BGE", "可选大模型；失败回退规则"),
    ("决策层", "Planner + Validator + Policy", "条件 DAG、七条校验、三级裁决"),
    ("执行层", "Tools + Saga + 幂等", "复式记账、逆序补偿"),
    ("证据层", "FactLedger + Audit", "事实校验、哈希链、回放"),
]
for i, (name, mid, desc) in enumerate(layers):
    y = 1.75 + i * 0.98
    rect(s, 0.8, y, 2.0, 0.72, NAVY)
    text(s, 1.0, y + 0.16, 1.6, 0.3, name, 15, GOLD, True)
    rect(s, 2.95, y, 5.0, 0.72, WHITE, RGBColor(0xD8, 0xD1, 0xC5))
    text(s, 3.15, y + 0.1, 4.6, 0.25, mid, 12, INK, True)
    text(s, 3.15, y + 0.4, 4.6, 0.22, desc, 9.5, MUTED)
    if i < len(layers) - 1:
        text(s, 2.35, y + 0.72, 0.35, 0.22, "↓", 12, GOLD_DARK, True)
rect(s, 8.35, 1.75, 4.2, 4.55, RGBColor(0x0F, 0x30, 0x54))
text(s, 8.62, 1.98, 3.7, 0.3, "设计原则", 15, GOLD, True)
principles = [
    "模型输出永远不是授权",
    "查询绿色直答；写操作必须确认",
    "失败可回退；执行失败逆序补偿",
    "每个结论都能追到事实/审计",
]
for i, line in enumerate(principles):
    text(s, 8.62, 2.5 + i * 0.72, 3.65, 0.45, "0" + str(i + 1) + "  " + line, 11, WHITE, spacing=1.2)
text(s, 8.62, 5.55, 3.6, 0.35, "仿真边界：不接真实核心", 9.5, RGBColor(0xC9, 0xD3, 0xDD))
notes(s, "整体五层。交互、理解、决策、执行、证据。关键点在决策层：模型可以提议，但不能授权；所有工具调用都要经过校验和策略。")
narration["3"] = "整体五层：交互、理解、决策、执行、证据。关键点在决策层：模型可以提议，但不能授权；所有工具调用都要经过校验和策略。"

# 4 Understanding
s = prs.slides.add_slide(BLANK)
title(s, "UNDERSTANDING · 理解", "不是猜，是分层收敛", 4)
steps = [
    ("01", "规则", "高频说法直接命中", "确定性、可解释"),
    ("02", "语义帧", "槽位/时间/对象结构化", "算金额、找收款人"),
    ("03", "BGE 向量", "没见过的说法做兜底", "≥0.75 直接；0.45–0.75 复述确认"),
    ("04", "澄清", "<0.45 或乱码", "给候选，不硬猜"),
]
for i, (num, h, line1, line2) in enumerate(steps):
    x = 0.8 + i * 3.12
    rect(s, x, 2.15, 2.85, 2.3, WHITE, RGBColor(0xD8, 0xD1, 0xC5))
    text(s, x + 0.22, 2.3, 0.5, 0.3, num, 11, GOLD_DARK, True)
    text(s, x + 0.22, 2.62, 2.4, 0.4, h, 18, NAVY, True)
    text(s, x + 0.22, 3.12, 2.4, 0.55, line1, 11, INK)
    text(s, x + 0.22, 3.72, 2.4, 0.45, line2, 10, MUTED)
rect(s, 0.8, 4.85, 11.7, 1.15, RGBColor(0xEF, 0xE8, 0xDA))
text(s, 1.05, 4.99, 11.2, 0.28, "安全前置：模态/否定/第三人称，先于任何意图匹配", 14, GOLD_DARK, True)
text(s, 1.05, 5.35, 11.2, 0.4, "“提前还款有违约金吗”不进入办理；“我不想还房贷”直接取消；“查张三的余额”拒绝并给下一步。", 11, INK)
notes(s, "理解层四步：规则、语义帧、BGE 向量、澄清。BGE 三档阈值，低置信先复述确认。更重要的是安全前置，咨询句、否定句、第三人称查询不会进入办理计划。")
narration["4"] = "理解层四步：规则、语义帧、BGE 向量、澄清。BGE 分三档，低置信先复述确认。更重要的是安全前置：咨询句、否定句、第三人称查询不会进入办理计划。"

# 5 Conditional DAG
s = prs.slides.add_slide(BLANK)
title(s, "DAG · 条件任务", "条件不是预计算，查询节点真实入图", 5)
text(s, 0.8, 1.55, 11.7, 0.35, "示例：查上个月账单，如果超 1000 就转 500 给张三", 13, GOLD_DARK, True)
nodes = [
    (0.8, "n0", "analyze_spending", "查上月支出", GREEN),
    (3.55, "n1", "get_contacts", "找收款人", GREEN),
    (6.3, "n2", "quote_transfer", "转账试算", GREEN),
    (9.05, "n3", "create_transfer", "条件：n0 > 1000", AMBER),
]
for x, nid, tool, desc, color in nodes:
    rect(s, x, 2.15, 2.15, 1.15, WHITE, color, line_w=1.6)
    text(s, x + 0.2, 2.28, 0.4, 0.25, nid, 10, color, True)
    text(s, x + 0.2, 2.56, 1.8, 0.25, tool, 9, MUTED)
    text(s, x + 0.2, 2.9, 1.8, 0.3, desc, 11, INK, True)
for x in (2.98, 5.73, 8.48):
    text(s, x, 2.6, 0.45, 0.3, "→", 14, GOLD_DARK, True)
# deps
text(s, 6.4, 3.55, 6.0, 0.3, "n3.depends_on = [n0, n1, n2] · condition = {field: total_cents, op: >, value: 100000}", 9.5, MUTED)
rect(s, 0.8, 4.05, 11.7, 1.45, RGBColor(0x0F, 0x30, 0x54))
text(s, 1.05, 4.2, 11.2, 0.3, "执行规则", 13, GOLD, True)
text(s, 1.05, 4.58, 11.2, 0.7, "条件成立 → 确认卡 → 强验证 → 按拓扑序执行\n条件不成立 → 直接回答原因，不生成转账确认卡；否则分支执行 else", 11, WHITE, spacing=1.35)
text(s, 0.8, 5.75, 11.7, 0.65, "边界：支持支出/余额 + 比较 + and + else；不是任意嵌套布尔脚本。条件求值只读，不产生资金动作。", 10, MUTED)
notes(s, "这是今年最关键的改造。条件式不是先在脑子里算完再假装有计划，而是把查询节点 n0 真正写进 DAG，动作节点带 condition 和 depends_on。条件不成立就不会生成确认卡。")
narration["5"] = "这是今年最关键的改造。条件式不是先算完再假装有计划，而是把查询节点 n0 真正写进 DAG，动作节点带 condition 和 depends_on。条件不成立就不会生成确认卡。"

# 6 Permission
s = prs.slides.add_slide(BLANK)
title(s, "POLICY · 权限", "三级权限：模型没有执行权", 6)
cols = [
    ("绿 · 只读", "余额 / 流水 / 账单 / 持仓\n知识咨询 / 主动提醒", "直接执行", GREEN),
    ("黄 · 小额办理", "日累计 ≤1,000 转账\n新收款人 ≤500 / 退订 / 调额", "确认卡：金额/对象/后果", AMBER),
    ("红 · 大额/不可逆", "日累计 >1,000 转账\n新收款人 >500 / 挂失 / 赎回 / 还款", "确认卡 → 强验证", RED),
]
for i, (h, body, action, color) in enumerate(cols):
    x = 0.8 + i * 4.05
    rect(s, x, 2.05, 3.65, 2.5, WHITE, color, line_w=1.6)
    text(s, x + 0.25, 2.25, 3.1, 0.35, h, 15, color, True)
    text(s, x + 0.25, 2.75, 3.1, 0.8, body, 11, INK, spacing=1.4)
    text(s, x + 0.25, 3.75, 3.1, 0.35, action, 11, NAVY, True)
rect(s, 0.8, 4.85, 11.7, 1.25, RGBColor(0xEF, 0xE8, 0xDA))
text(s, 1.05, 4.98, 11.2, 0.3, "确认卡绑定：token · intent_id · params_hash · 过期时间 · 一次性", 13, GOLD_DARK, True)
text(s, 1.05, 5.38, 11.2, 0.55, "改金额 → E_PARAM；改 token → E_CONFIRM_TOKEN；执行前重新跑策略；审计链可校验。", 11, INK)
text(s, 0.8, 6.3, 11.7, 0.35, "硬上限：单笔/计划 50,000 元，超出直接拒绝；IM 300 元、夜间 500 元另计。", 9, MUTED)
notes(s, "三级权限。绿色只读直答；黄色转账按日累计 1000 元、新收款人单笔 500 元以内；红色是超过日累计 1000 或新收款人 500，以及挂失、赎回、还款等。单笔和计划硬上限都是 5 万，超出直接拒绝。确认卡 token、intent_id、params_hash 三者绑定，改一个就失效。")
narration["6"] = "三级权限。绿色只读直答；黄色转账按日累计 1000 元、新收款人单笔 500 元以内；红色是超过日累计 1000 或新收款人 500，以及挂失、赎回、还款等。单笔和计划硬上限都是 5 万，超出直接拒绝。确认卡 token、intent_id、params_hash 三者绑定，改一个就失效。"

# 7 Security
s = prs.slides.add_slide(BLANK)
title(s, "SECURITY · 安全", "四道闸：拦截、校验、确认、审计", 7)
items = [
    ("注入/角色伪装", "前置 deny\n不因一句话跳过验证", RED),
    ("第三人称查询", "只查本人\n拒绝他人余额/流水/手机号", AMBER),
    ("失败 Saga", "逆序补偿\n冻结预算原路释放", GREEN),
    ("哈希链审计", "prev_hash 串联\n改一行即校验失败", NAVY),
]
for i, (h, body, color) in enumerate(items):
    x = 0.8 + (i % 2) * 5.95
    y = 2.0 + (i // 2) * 1.55
    rect(s, x, y, 5.45, 1.25, WHITE, color, line_w=1.3)
    text(s, x + 0.25, y + 0.18, 4.9, 0.3, h, 14, color, True)
    text(s, x + 0.25, y + 0.55, 4.9, 0.55, body, 10, MUTED, spacing=1.3)
rect(s, 0.8, 5.15, 11.7, 0.95, RGBColor(0x0F, 0x30, 0x54))
text(s, 1.05, 5.3, 11.2, 0.6, "评测：448 项测试 · 243/243 评测 · 红队 58/58 · 权限边界 60/60\n已公开 3 条检测漏检，执行层全部拦截，不假装 100%", 11, WHITE, spacing=1.35)
notes(s, "安全四道闸：注入和角色伪装前置拒绝；第三人称查询只查本人；执行失败逆序补偿；审计是哈希链。评测不是全绿口号，我们公开了三条检测漏检，但执行层全部拦截。")
narration["7"] = "安全四道闸：注入和角色伪装前置拒绝；第三人称查询只查本人；执行失败逆序补偿；审计是哈希链。评测不是全绿口号，我们公开了三条检测漏检，但执行层全部拦截。"

# 8 Security screenshot
s = prs.slides.add_slide(BLANK)
title(s, "EVIDENCE · 亲手验证", "评委可以亲手把系统弄坏", 8)
picture(s, "docs/security_panel.png", 0.7, 1.65, 7.55, 4.9)
rect(s, 8.55, 1.65, 4.05, 4.9, RGBColor(0x0F, 0x30, 0x54))
text(s, 8.82, 1.9, 3.5, 0.3, "三个按钮 · 隔离临时库", 13, GOLD, True)
for i, line in enumerate([
    "篡改确认 token → E_CONFIRM_TOKEN",
    "篡改确认金额 → E_PARAM",
    "篡改审计记录 → before=true / after=false",
]):
    text(s, 8.82, 2.45 + i * 1.0, 3.45, 0.75, "0" + str(i + 1) + "  " + line, 10.5, WHITE, spacing=1.25)
text(s, 8.82, 5.65, 3.4, 0.5, "设置 → 安全证据，两次点击即可运行。", 9.5, RGBColor(0xC9, 0xD3, 0xDD))
notes(s, "安全证据不是口头说。设置里三个按钮，在隔离临时库运行：改 token、改金额、改审计，分别触发不同错误。评委可以亲手把系统弄坏。")
narration["8"] = "安全证据不是口头说。设置里三个按钮，在隔离临时库运行：改 token、改金额、改审计，分别触发不同错误。评委可以亲手把系统弄坏。"

# 9 Chat screenshot
s = prs.slides.add_slide(BLANK)
title(s, "EXPERIENCE · 一句话", "结果先行，过程可展开", 9)
picture(s, "docs/chat_no_overview.png", 0.7, 1.65, 7.55, 4.9)
rect(s, 8.55, 1.65, 4.05, 4.9, RGBColor(0xEF, 0xE8, 0xDA))
text(s, 8.82, 1.9, 3.45, 0.3, "对话原则", 13, GOLD_DARK, True)
for i, line in enumerate([
    "先给结果，再给执行详情",
    "咨询句只答不办",
    "缺槽位一次问全 / 用上次金额",
    "“全部展示”看完整流水",
    "审计页只放决策链，不混流水",
]):
    text(s, 8.82, 2.45 + i * 0.72, 3.45, 0.55, "· " + line, 10, INK, spacing=1.2)
notes(s, "体验上坚持结果先行。能用一句话说清就不甩表格；咨询句只答不办；缺金额时用上次同收款人金额，并在卡上标注来源。")
narration["9"] = "体验上坚持结果先行。能用一句话说清就不甩表格；咨询句只答不办；缺金额时用上次同收款人金额，并在卡上标注来源。"

# 10 Consistency
s = prs.slides.add_slide(BLANK)
title(s, "CONSISTENCY · 账实一致", "刚还完 7,800，进度必须立刻变", 10)
rows = [
    ("贷款进度", "读 loans.remaining / monthly", "420,000 → 412,200；23/60 → 24/60"),
    ("信用卡账单", "cards.bill_cents + 本月消费", "还款 300 后 3,280.50 → 2,980.50"),
    ("可用余额", "总余额 − 冻结/计划锁定", "可用 6,945.67（总 7,945.67）"),
    ("订阅列表", "按 status 分组", "在用 4 项 + 已取消 1 项"),
    ("积分", "points 表", "不再承诺兑换"),
]
for i, (k, mid, val) in enumerate(rows):
    y = 1.72 + i * 0.88
    rect(s, 0.8, y, 2.3, 0.7, NAVY)
    text(s, 1.0, y + 0.2, 1.9, 0.28, k, 12, GOLD, True)
    text(s, 3.3, y + 0.12, 4.1, 0.45, mid, 10, MUTED)
    text(s, 7.55, y + 0.12, 4.8, 0.45, val, 10.5, INK, True)
text(s, 0.8, 6.1, 11.7, 0.4, "原则：页面上出现的每个数字，都必须能在数据库或工具结果里找到来源。", 11, GOLD_DARK, True)
notes(s, "账实一致是今年补得最多的地方。贷款进度读库，信用卡账单有 bill_cents，可用余额分总余额和冻结，订阅按状态分组，积分也进了表。页面上每个数字都要有来源。")
narration["10"] = "账实一致是今年补得最多的地方。贷款进度读库，信用卡账单有 bill_cents，可用余额分总余额和冻结，订阅按状态分组，积分也进了表。页面上每个数字都要有来源。"

# 11 Evaluation
s = prs.slides.add_slide(BLANK)
title(s, "EVIDENCE · 评测", "数字要能复跑，不要只写在 PPT 里", 11)
metrics = [("448", "测试项"), ("243/243", "评测集"), ("58/58", "红队"), ("60/60", "权限边界"), ("27/30", "盲测")]
for i, (num, label) in enumerate(metrics):
    x = 0.75 + i * 2.48
    rect(s, x, 1.9, 2.2, 1.55, WHITE, RGBColor(0xD8, 0xD1, 0xC5))
    text(s, x + 0.18, 2.05, 1.85, 0.55, num, 22, NAVY, True)
    text(s, x + 0.18, 2.72, 1.85, 0.3, label, 10, MUTED)
rect(s, 0.8, 3.95, 11.7, 1.55, RGBColor(0x0F, 0x30, 0x54))
text(s, 1.05, 4.12, 11.2, 0.3, "盲测 27/30，我们公开了 3 条失败", 14, GOLD, True)
text(s, 1.05, 4.52, 11.2, 0.8, "不拿盲测结果回头调规则；3 条失败保留在 docs/BLIND_TEST.md。\n已知 3 条检测绕过也公开：均未执行，靠策略兜底。", 10.5, WHITE, spacing=1.35)
text(s, 0.8, 5.8, 11.7, 0.4, "复跑：python -m unittest discover tests -q ｜ python -m eval.runner ｜ python -m eval.blind_runner", 9.5, MUTED)
notes(s, "评测数字：448 项测试、243 条评测、红队 58、权限 60、盲测 27/30。盲测三条失败和三条检测绕过都公开，不拿结果回头调规则。")
narration["11"] = "评测数字：448 项测试、243 条评测、红队 58、权限 60、盲测 27/30。盲测三条失败和三条检测绕过都公开，不拿结果回头调规则。"

# 12 Boundary
s = prs.slides.add_slide(BLANK)
title(s, "BOUNDARY · 边界", "诚实，是安全的一部分", 12)
text(s, 0.8, 1.8, 5.6, 0.35, "已经做到的", 15, GREEN, True)
ok = ["零 Key 可完整演示", "条件 DAG 查询节点入图", "确认卡/强验证/审计可现场验证", "账实一致与失败补偿"]
for i, line in enumerate(ok):
    text(s, 0.8, 2.25 + i * 0.6, 5.6, 0.4, "✓ " + line, 11, INK)
text(s, 6.8, 1.8, 5.7, 0.35, "仍然没做的", 15, RED, True)
no = ["任意嵌套条件脚本", "所有红色动作统一确认卡", "真实人脸/U 盾（演示为模拟）", "PPT/视频之外的实时录屏"]
for i, line in enumerate(no):
    text(s, 6.8, 2.25 + i * 0.6, 5.7, 0.4, "× " + line, 11, INK)
rect(s, 0.8, 5.1, 11.7, 1.0, RGBColor(0xEF, 0xE8, 0xDA))
text(s, 1.05, 5.25, 11.2, 0.55, "仿真环境：不连接真实银行核心，不产生真实资金；所有客户、账户、流水均为合成数据。", 11, GOLD_DARK, True)
notes(s, "边界也要讲。已经做到的和没做到的都列出来。仿真环境，不接真实核心，不产生真实资金。")
narration["12"] = "边界也要讲。已经做到的和没做到的都列出来。仿真环境，不接真实核心，不产生真实资金。"

# 13 End
s = prs.slides.add_slide(BLANK)
bg(s, NAVY)
rect(s, 0, 0, 0.14, 7.5, GOLD, radius=False)
text(s, 0.95, 1.1, 10.8, 0.35, "Prometheus Ultra｜普罗米修斯 · 极致", 11, GOLD, True)
text(s, 0.95, 1.45, 10.8, 0.3, "DUNYU · 盾语 · 银行 AI 智能体（仿真环境）", 9, RGBColor(0xC9, 0xD3, 0xDD))
text(s, 0.95, 2.0, 10.8, 0.9, "听得懂，也守得住。", 44, WHITE, True)
text(s, 0.98, 3.2, 10.8, 0.45, "理解与授权分离 · 条件 DAG · 策略裁决 · 哈希链审计", 17, RGBColor(0xD3, 0xDF, 0xEA))
rect(s, 0.98, 4.2, 11.2, 1.05, RGBColor(0x16, 0x3B, 0x62), RGBColor(0x3A, 0x5C, 0x7B))
text(s, 1.2, 4.35, 10.7, 0.3, "现场演示入口", 10, GOLD, True)
text(s, 1.2, 4.7, 10.7, 0.3, "双击 start.bat → http://127.0.0.1:8000 → 设置 → 安全证据 / 演示", 11, WHITE)
text(s, 0.98, 6.05, 11.2, 0.3, "谢谢，欢迎提问。", 13, RGBColor(0xC9, 0xD3, 0xDD))
notes(s, "最后一句：听得懂，也守得住。现场双击 start.bat 就能跑。谢谢，欢迎提问。")
narration["13"] = "最后一句：听得懂，也守得住。现场双击 start.bat 就能跑。谢谢，欢迎提问。"

out = OUT / "盾语_答辩PPT.pptx"
prs.save(out)
(OUT / "narration.json").write_text(json.dumps(narration, ensure_ascii=False, indent=2), encoding="utf-8")
print(out)
print("slides", len(prs.slides))
