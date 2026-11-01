"""生成技术文档 DOCX、安全自评 DOCX、架构图与 DAG 图。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deliverables"
OUT.mkdir(exist_ok=True)

NAVY = RGBColor(0x0D, 0x2B, 0x4E)
GOLD = RGBColor(0x8A, 0x6A, 0x2F)
INK = RGBColor(0x20, 0x2A, 0x33)
MUTED = RGBColor(0x6B, 0x7B, 0x88)
FONT = "微软雅黑"
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"


def _font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def _round(draw, box, fill, outline, width=3, radius=18):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def make_architecture():
    w, h = 1800, 980
    img = Image.new("RGB", (w, h), "#fbf8f2")
    d = ImageDraw.Draw(img)
    d.text((80, 55), "盾语 · 系统架构（五层）", fill="#0d2b4e", font=_font(48))
    d.text((82, 118), "模型只负责理解/提议；策略、确认、执行、审计由确定性代码负责", fill="#6b7b88", font=_font(25))
    layers = [
        ("交互层", "Web / IM / 语音", "会话 · 确认卡 · 审计回放"),
        ("理解层", "规则 + 语义帧 + BGE", "可选 LLM；失败自动回退规则"),
        ("决策层", "Planner + Validator + Policy", "条件 DAG · 七条校验 · 三级裁决"),
        ("执行层", "Tools + Saga + 幂等", "复式记账 · 逆序补偿"),
        ("证据层", "FactLedger + Audit", "事实校验 · 哈希链 · 回放"),
    ]
    y = 190
    for i, (name, mid, desc) in enumerate(layers):
        _round(d, (80, y, 300, y + 105), "#0d2b4e", "#0d2b4e")
        d.text((105, y + 35), name, fill="#e6c98b", font=_font(30))
        _round(d, (350, y, 1180, y + 105), "#ffffff", "#d8d1c5", width=2)
        d.text((380, y + 15), mid, fill="#162a3a", font=_font(28))
        d.text((380, y + 60), desc, fill="#6b7b88", font=_font(22))
        if i < len(layers) - 1:
            d.line((195, y + 105, 195, y + 135), fill="#8a6a2f", width=4)
            d.polygon([(195, y + 145), (187, y + 130), (203, y + 130)], fill="#8a6a2f")
        y += 145
    _round(d, (1260, 190, 1730, 820), "#0f3054", "#0f3054")
    d.text((1290, 220), "四个不可越过的约束", fill="#e6c98b", font=_font(27))
    rules = ["模型输出不是授权", "写操作必须确认", "失败可逆序补偿", "结论必须可追溯"]
    for i, r in enumerate(rules):
        d.text((1290, 300 + i * 105), f"0{i+1}", fill="#e6c98b", font=_font(23))
        d.text((1345, 295 + i * 105), r, fill="#ffffff", font=_font(25))
    d.text((80, 900), "仿真环境：不连接真实银行核心，不产生真实资金。", fill="#8a6a2f", font=_font(23))
    img.save(OUT / "architecture.png")


def make_dag():
    w, h = 1800, 760
    img = Image.new("RGB", (w, h), "#fbf8f2")
    d = ImageDraw.Draw(img)
    d.text((70, 45), "条件 DAG：查询节点真实入图", fill="#0d2b4e", font=_font(46))
    d.text((72, 108), "查上个月账单，如果超 1000 就转 500 给张三", fill="#8a6a2f", font=_font(26))
    boxes = [
        (70, 210, 390, 390, "n0", "analyze_spending", "查上月支出", "#2e7d5b"),
        (490, 210, 810, 390, "n1", "get_contacts", "找收款人", "#2e7d5b"),
        (910, 210, 1230, 390, "n2", "quote_transfer", "转账试算", "#2e7d5b"),
        (1330, 210, 1700, 390, "n3", "create_transfer", "条件 n0 > 1000", "#b07a1e"),
    ]
    for x1, y1, x2, y2, nid, tool, desc, color in boxes:
        _round(d, (x1, y1, x2, y2), "#ffffff", color, width=4)
        d.text((x1 + 24, y1 + 20), nid, fill=color, font=_font(25))
        d.text((x1 + 24, y1 + 60), tool, fill="#6b7b88", font=_font(20))
        d.text((x1 + 24, y1 + 120), desc, fill="#162a3a", font=_font(27))
    for x in (410, 830, 1250):
        d.line((x, 300, x + 55, 300), fill="#8a6a2f", width=5)
        d.polygon([(x + 65, 300), (x + 48, 290), (x + 48, 310)], fill="#8a6a2f")
    _round(d, (70, 440, 1700, 650), "#0f3054", "#0f3054")
    d.text((100, 465), "执行规则", fill="#e6c98b", font=_font(30))
    d.text((100, 520), "条件成立 → 确认卡 → 强验证 → 拓扑序执行", fill="#ffffff", font=_font(26))
    d.text((100, 570), "条件不成立 → 直接回答原因，不生成确认卡；否则分支执行 else", fill="#ffffff", font=_font(26))
    d.text((100, 625), "n3.depends_on = [n0, n1, n2] · condition = {field: total_cents, op: >, value: 100000}", fill="#c9d3dd", font=_font(19))
    d.text((70, 700), "边界：支持支出/余额 + 比较 + and + else；不是任意嵌套布尔脚本。", fill="#6b7b88", font=_font(21))
    img.save(OUT / "conditional_dag.png")


def setup_doc():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21.0)
    sec.page_height = Cm(29.7)
    sec.left_margin = Cm(2.2)
    sec.right_margin = Cm(2.2)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = INK
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    for name, size, color in (("Heading 1", 18, NAVY), ("Heading 2", 13, GOLD), ("Heading 3", 11.5, NAVY)):
        st = doc.styles[name]
        st.font.name = FONT
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = color
        st._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    return doc


def footer_page(doc):
    sec = doc.sections[0]
    p = sec.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("盾语 · 银行 AI 智能体（仿真环境） · 第 ")
    run.font.size = Pt(8)
    run.font.color.rgb = MUTED
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    p._p.append(fld)
    run2 = p.add_run(" 页")
    run2.font.size = Pt(8)
    run2.font.color.rgb = MUTED


def para(doc, text, size=10.5, color=INK, bold=False, space=6, align=None):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space)
    p.paragraph_format.line_spacing = 1.35
    r = p.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    return p


def bullet(doc, text, size=10.5):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.line_spacing = 1.3
    r = p.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)


def shade(cell, color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), color)
    tcPr.append(shd)


def table(doc, headers, rows, widths=None, header_fill="0D2B4E"):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        p = c.paragraphs[0]
        r = p.add_run(h)
        r.font.bold = True
        r.font.size = Pt(9.5)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        r.font.name = FONT
        r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
        shade(c, header_fill)
    for row in rows:
        cells = t.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            r = p.add_run(str(value))
            r.font.size = Pt(9)
            r.font.name = FONT
            r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = Cm(w)
    return t


def code(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.3)
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(8)
    r = p.add_run(text)
    r.font.name = "Consolas"
    r.font.size = Pt(8.5)
    r.font.color.rgb = RGBColor(0x24, 0x3B, 0x53)
    pPr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), "F1F4F7")
    pPr.append(shd)


def add_image(doc, path, width_cm=16.2, caption=""):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(str(path), width=Cm(width_cm))
    if caption:
        para(doc, caption, 8.5, MUTED, align=WD_ALIGN_PARAGRAPH.CENTER, space=10)


def build_tech():
    make_architecture()
    make_dag()
    doc = setup_doc()
    footer_page(doc)

    para(doc, "盾语 · 银行 AI 智能体", 12, GOLD, True, space=2)
    para(doc, "技术文档", 30, NAVY, True, space=6)
    para(doc, "系统架构 · 核心算法 · 安全设计", 14, MUTED, space=6)
    para(doc, "Prometheus Ultra｜普罗米修斯 · 极致", 11, GOLD, True, space=18)
    table(doc, ["项目", "内容"], [
        ["作品名称", "盾语 · 银行 AI 智能体（仿真环境）"],
        ["参赛队伍", "Prometheus Ultra｜普罗米修斯 · 极致"],
        ["文档版本", "v1.0（与提交包 2026-11-01 对齐）"],
        ["运行形态", "Windows 便携包，零 Key 可运行；不连接真实银行"],
        ["技术栈", "Python 标准库 + SQLite WAL + 原生 JS；可选 ONNX/BGE、Vosk"],
    ], widths=[3.2, 13.2])
    para(doc, "本文件说明系统如何把一句口语变成可审计的业务动作，以及为什么模型不会越权。", 10, MUTED, space=18)
    doc.add_page_break()

    doc.add_heading("1. 定位与边界", level=1)
    para(doc, "盾语是一个面向银行零售场景的 AI 智能体仿真作品。目标不是“接一个大模型”，而是解决三个问题：口语理解、复杂任务编排、以及在高风险金融场景里让模型没有执行权。")
    bullet(doc, "输入：文字 / 语音 / IM 渠道的一句话，允许口语、省略、错字、复合句。")
    bullet(doc, "输出：查询结果、方案建议、确认卡、强验证、执行回执、审计链路。")
    bullet(doc, "边界：所有客户、账户、流水、贷款、信用卡均为合成数据；不连接真实银行核心，不产生真实资金。")
    bullet(doc, "模型边界：大模型只参与“理解/提议”，不参与授权、不直接调用工具、不生成最终回执。")

    doc.add_heading("2. 总体架构", level=1)
    add_image(doc, OUT / "architecture.png", 16.2, "图 1  五层架构：交互、理解、决策、执行、证据")
    para(doc, "主链路是固定的：消息 → 理解 → 计划 DAG → 七条校验 → 策略三级 → 确认/强验证 → 执行 → 事实账本 → 哈希链审计。任何模型输出都必须经过这条链路，模型没有旁路。")

    doc.add_heading("3. 对话处理流水线", level=1)
    table(doc, ["阶段", "做什么", "关键代码"], [
        ["会话/安全前置", "注入、角色伪装、密码修改、第三人称查询、否定/咨询", "orchestrator.handle_chat"],
        ["理解", "规则 / 语义帧 / BGE / 澄清；模型可选", "nlu.py, understanding.py, semantic.py"],
        ["计划", "把意图编译成 DAG；条件任务插入查询节点", "planner.py, conditional.py"],
        ["校验", "节点数、环、工具白名单、参数、溯源、单笔/计划上限", "validator.py"],
        ["策略", "绿/黄/红三级；返回规则 ID 与理由", "policy.py, rules/policy.rego"],
        ["确认/强验证", "确认卡 token/hash/过期；红色确认卡→MFA", "orchestrator.py, trust.py"],
        ["执行", "拓扑序执行、幂等键、失败逆序补偿", "executor.py, tools.py"],
        ["证据", "事实账本、发出前校验、哈希链审计", "trust.py, audit"],
    ], widths=[3.0, 10.0, 4.0])

    doc.add_heading("4. 理解层：分层收敛，不硬猜", level=1)
    para(doc, "理解层按确定性从高到低逐层收敛，任何一层失败都可以回退，不允许“猜一个就执行”。")
    table(doc, ["层", "输入", "输出", "约束"], [
        ["规则", "高频说法/关键词", "意图 + 槽位", "可解释、可回归"],
        ["语义帧", "时间、金额、对象、动作", "结构化槽位", "金额必须换算为分"],
        ["BGE 向量", "规则未命中的短句", "候选意图 + 相似度", "≥0.75 直接；0.45–0.75 复述确认；<0.45 澄清"],
        ["大模型（可选）", "自然语言", "候选意图/计划", "无 Key 回退；非法 JSON/表外工具回退"],
    ], widths=[2.6, 4.2, 4.4, 6.2])
    para(doc, "安全前置先于理解：咨询句（“提前还款有违约金吗”）、否定句（“我不想还房贷”）、第三人称查询（“查张三的余额”）不会生成办理计划。")

    doc.add_heading("5. 条件 DAG 与执行", level=1)
    add_image(doc, OUT / "conditional_dag.png", 16.2, "图 2  条件任务的 4 节点计划")
    para(doc, "条件任务不是“先算完再假装有计划”，而是把查询节点 n0 写进 plan，动作节点 n3 带 condition 与 depends_on。条件成立才出确认卡；不成立直接回答原因，不生成转账卡；有否则分支时执行 else。")
    code(doc, 'n3.condition = [{"node": "n0", "field": "total_cents", "op": ">", "value": 100000}]\nn3.depends_on = ["n1", "n2", "n0"]')
    para(doc, "支持范围：本月/上月支出、余额、比较符、and、else。明确不支持任意嵌套布尔脚本；条件求值只读，不产生资金动作。")

    doc.add_heading("6. 权限与安全设计", level=1)
    table(doc, ["级别", "范围", "控制"], [
        ["绿", "余额/流水/账单/持仓/咨询/提醒", "直接执行，无确认"],
        ["黄", "日累计 ≤1,000 转账（新收款人单笔 ≤500）、退订、调额、风险测评、虚拟卡", "确认卡：金额、对象、后果、params_hash、token、倒计时"],
        ["红", "日累计 >1,000 转账 / 新收款人 >500、挂失、解挂、赎回、还款、密码重置链接", "还款/信用卡/赎回：确认卡→MFA；其余红色：摘要→MFA；密码修改恒拒绝"],
    ], widths=[1.6, 8.6, 7.0])
    para(doc, "确认卡不是一张图片：token、intent_id、params_hash、过期时间、一次性使用共同绑定。改金额触发 E_PARAM，改 token 触发 E_CONFIRM_TOKEN，执行前重新跑策略，审计链可校验。", space=4)
    para(doc, "七条硬校验（validator.py）：节点数、无环、工具白名单、必填参数/类型、资金参数溯源、单笔 5 万、计划净流出 5 万。任何一条不过就不进入执行。", space=4)
    para(doc, "审计哈希链：每条事件 prev_hash 串联，篡改任意一行会导致后续校验失败；安全证据面板在隔离临时库现场演示 token/金额/审计三种篡改。", space=4)
    para(doc, "失败补偿：执行器按逆序调用 compensate；生日计划注入 create_standing_plan 失败时，已冻结预算原路释放。", space=4)
    para(doc, "熔断：连续失败 3 次 OBSERVE、5 次 LOCKED 15 分钟；密码修改、注入、越权查询前置拒绝。", space=4)

    doc.add_heading("7. 账实一致与数据模型", level=1)
    table(doc, ["对象", "数据来源", "一致性规则"], [
        ["贷款进度", "loans.principal/remaining/monthly/next_due", "已还期数按金额差推算；还款后立即变化"],
        ["信用卡账单", "cards.bill_cents + 本月消费", "账单/最低还款/额度/已用/可用同源"],
        ["可用余额", "总余额 − 冻结 − active freezes", "显示总余额与冻结明细"],
        ["订阅", "subscriptions.status", "在用/已取消分组，已取消不计入未使用建议"],
        ["积分", "points 表", "演示环境明确不支持兑换"],
    ], widths=[3.0, 7.2, 7.0])
    para(doc, "原则：页面上出现的每个数字，都必须能在数据库或工具结果里找到来源；不允许模板写死。")

    doc.add_heading("8. 评测与证据", level=1)
    table(doc, ["项目", "结果", "说明"], [
        ["单元/集成测试", "448 项：最终便携版 448 全通过", "开发机 447 通过 / 1 条环境相关跳过"],
        ["评测集", "243 / 243", "意图、权限、红队、端到端"],
        ["红队注入", "58 / 58", "端到端拦截"],
        ["权限边界", "60 / 60", "三级矩阵与拒绝路径"],
        ["盲测", "27 / 30", "公开 3 条失败，不回头调规则"],
        ["已知绕过", "3 条", "检测层漏检、执行层全部拦截"],
    ], widths=[4.0, 6.0, 7.0])
    para(doc, "复跑命令：python -m unittest discover tests -q；python -m eval.runner；python -m eval.blind_runner。")

    doc.add_heading("9. 部署与复跑", level=1)
    bullet(doc, "便携包：解压后双击 start.bat；Python 3.12 embedded 运行时，无需安装。")
    bullet(doc, "开发：cd backend; python -m bankagent.api；默认 http://127.0.0.1:8000。")
    bullet(doc, "LLM：设置 → 对话 → API Key，保存并启用；也可用环境变量 LLM_API_KEY；失败自动回退规则。")
    bullet(doc, "演示时钟：默认固定 2026-10-31（与种子数据同月）；需要真实时间设置 BANK_REAL_CLOCK=1。")

    doc.add_heading("10. 已知边界", level=1)
    para(doc, "不把没做的说成做了：")
    bullet(doc, "通用条件 DAG 只支持支出/余额 + 比较 + and + else，不支持任意嵌套/循环/跨用户条件。")
    bullet(doc, "只有还款、信用卡还款、赎回做了“确认卡→MFA”双闸；其他红色仍是摘要→MFA。")
    bullet(doc, "人脸为模拟因子，U 盾未实现；生产需替换 StepUpAuthProvider。")
    bullet(doc, "知识库支持新建/审批/发布，但前端运营页仍以改为主；文档已标注。")
    bullet(doc, "不连接真实银行核心，不产生真实资金。")

    out = OUT / "盾语_技术文档.docx"
    doc.save(out)
    return out


def build_safety():
    doc = setup_doc()
    footer_page(doc)
    para(doc, "盾语 · 银行 AI 智能体", 12, GOLD, True, space=2)
    para(doc, "安全自评报告", 30, NAVY, True, space=6)
    para(doc, "权限分级实现说明 + 已知风险清单", 14, MUTED, space=6)
    para(doc, "Prometheus Ultra｜普罗米修斯 · 极致", 11, GOLD, True, space=18)
    table(doc, ["项目", "内容"], [
        ["作品", "盾语 · 银行 AI 智能体（仿真环境）"],
        ["适用范围", "本地演示 / 评审复现；不连接真实银行"],
        ["结论", "三级权限、确认/强验证、哈希链审计可现场复现；残余风险已列出"],
    ], widths=[3.0, 13.4])

    doc.add_heading("1. 权限分级实现", level=1)
    table(doc, ["级别", "范围", "触发", "实现"], [
        ["绿", "余额、流水、账单、持仓、知识咨询、主动提醒", "不涉及资金/状态变更", "直接执行；policy.py green；无确认"],
        ["黄", "日累计 ≤1,000 转账（新收款人 ≤500）、退订、风险测评、调额、虚拟卡", "写操作但可逆/低金额", "确认卡（金额/对象/后果/params_hash/token/倒计时）"],
        ["红", "日累计 >1,000 转账 / 新收款人 >500、挂失、解挂、赎回、还款、密码重置链接", "资金流出/不可逆", "还款/信用卡/赎回：确认卡→MFA；其余红色：摘要→MFA；密码修改恒拒绝"],
    ], widths=[1.4, 7.2, 4.0, 4.6])
    para(doc, "补充规则：日累计 1,000 元、新收款人 500 元；单笔/计划硬上限 50,000 元，超出直接拒绝；IM 300 元、夜间 500 元另计。LOCKED、风险分 >90、计划净流出超限 → deny；注入/角色伪装/第三人称查询 → 前置拒绝。")

    doc.add_heading("2. 安全机制与证据", level=1)
    table(doc, ["机制", "实现", "证据"], [
        ["参数绑定", "params_hash + token + intent_id + 过期", "改金额 E_PARAM；改 token E_CONFIRM_TOKEN"],
        ["审计哈希链", "prev_hash 串联，SHA-256", "改一行 payload → chain_ok=false"],
        ["注入防护", "前置规则 + 不可信内容包裹", "红队 58/58"],
        ["权限边界", "policy.py 三级矩阵 + validator 七条", "60/60"],
        ["失败补偿", "逆序 compensate", "注入 create_standing_plan 失败 → freeze released"],
        ["熔断", "3 次 OBSERVE / 5 次 LOCKED 15 分钟", "stepup 连续失败测试"],
    ], widths=[3.2, 7.0, 6.0])
    para(doc, "现场复现：设置 → 安全证据，三个按钮分别在隔离临时库运行；首屏欢迎卡也有“亲手验证一次”入口。")

    doc.add_heading("3. 已知风险清单", level=1)
    table(doc, ["风险", "现状", "缓解/边界"], [
        ["非真实 KYC/活体", "模拟 L2 实名、设备指纹", "生产接公安/人脸 SDK；文档明确仿真"],
        ["强验证因子", "短信码 + 人脸模拟，演示码 123456", "生产替换短信网关/活体/U 盾；不改编排"],
        ["复杂条件 DAG", "单条件模板 + and + else", "文档标注；复杂场景转人工"],
        ["其他红色动作", "摘要 + MFA，非独立确认卡", "下一版统一状态机"],
        ["多轮指代", "覆盖上轮/改金额/全部展示", "通用 DST 待做"],
        ["Key 下发前端", "确认 token 含一次性凭证", "生产改 HttpOnly Cookie/二次签名"],
        ["LLM 模式", "默认关闭；失败回退", "零 Key 演示不受影响"],
        ["知识库", "关键词+向量混合；无文档解析/重排", "文档标注能力边界"],
    ], widths=[4.0, 6.8, 6.0])

    doc.add_heading("4. 残余风险与诚实声明", level=1)
    bullet(doc, "项目是仿真 Demo，不连接真实银行核心，不产生真实资金。")
    bullet(doc, "已知 3 条检测层绕过未执行，靠策略兜底；不声称 100% 检出。")
    bullet(doc, "盲测 27/30，3 条失败公开保留；不拿盲测结果回头调规则。")
    bullet(doc, "人脸/U 盾为模拟或未实现；生产需替换 StepUpAuthProvider。")
    bullet(doc, "安全不是“模型更聪明”，而是确认、策略、执行、审计四层都不允许模型越权。")

    doc.add_heading("5. 复跑", level=1)
    code(doc, "cd backend\npython -m unittest discover tests -q      # 448 项\npython -m eval.runner                     # 243 条\npython -m eval.blind_runner               # 30 条盲测")
    out = OUT / "盾语_安全自评报告.docx"
    doc.save(out)
    return out


if __name__ == "__main__":
    print(build_tech())
    print(build_safety())
