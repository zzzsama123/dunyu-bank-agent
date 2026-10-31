# 银行 AI 智能体 · 成品实现契约 v1

> 本文件是**唯一接口真相**。所有模块按此实现，不得擅自改签名；确需变更先改本文件并在 PR 说明。
> 目标：`python -m bankagent.api` 一条命令起服务，浏览器打开即可完整演五幕剧。

---

## 0. 环境与硬约束

| 项 | 约束 |
|---|---|
| 运行环境 | Windows / Python **3.11+**（本机 3.15），**只用标准库** |
| 依赖 | 禁止 pip install、禁止联网、禁止 Docker/Postgres/OPA |
| 存储 | SQLite，单文件 `backend/data/bank.db`（可用环境变量 `BANK_DB` 覆盖） |
| 启动目录 | `bankagent/backend`（`python -m bankagent.api`） |
| 编码 | 全部 UTF-8，无 BOM |
| 金额 | **整数分**（`amount_cents`），禁止 float；渲染时才格式化为 `12,345.67` |
| 时间 | ISO8601 本地时间字符串 `2026-10-29T20:31:07+08:00`，日期键 `2026-10-29` |
| 标识 | `trace_id=tr_xxxx`、`plan_id=pl_xxxx`、`intent_id=it_xxxx`，`secrets.token_hex(4)` 后缀 |

---

## 1. 文件归属（谁能写哪些文件）

| 工作包 | 文件 | 负责人 |
|---|---|---|
| 基础 | `bankagent/config.py` `bankagent/specs.py` `bankagent/db.py` `bankagent/bankcore.py` `bankagent/seed.py` | 主控 |
| WP-POLICY | `bankagent/policy.py` `bankagent/rules/policy.rego` `tests/test_policy.py` | 子智能体 A |
| WP-TRUST | `bankagent/trust.py` `tests/test_trust.py` | 子智能体 B |
| WP-NLU | `bankagent/nlu.py` `bankagent/planner.py` `bankagent/validator.py` `tests/test_nlu.py` | 子智能体 C |
| WP-WEB | `frontend/index.html` `frontend/app.js` `frontend/styles.css` | 子智能体 D |
| WP-TOOLS | `bankagent/tools.py` `tests/test_tools.py` | 子智能体 E |
| 主控 | `bankagent/executor.py` `bankagent/orchestrator.py` `bankagent/api.py` `eval/*` `scripts/*` `README.md` | 主控 |

**只写自己名下的文件。** 需要别的模块时 `import`，不要去改它。

---

## 2. 模块依赖方向（禁止反向 import）

```
config, specs          （无依赖）
db  → bankcore         （db 只管连接与建表）
policy, trust          （纯逻辑，不碰 db）
nlu → planner → validator
tools → bankcore
executor → tools, policy, trust, validator
orchestrator → nlu, planner, validator, executor, trust, bankcore
api → orchestrator
frontend → api (HTTP)
```

---

## 3. 数据库 Schema（精确 DDL）

由 `db.init_db()` 建表；字段名不得改。金额列一律 `INTEGER`（分）。

```sql
users(id INTEGER PRIMARY KEY, name TEXT, phone TEXT, risk_level TEXT DEFAULT 'normal',
      risk_score INTEGER DEFAULT 10, kyc_status TEXT DEFAULT 'passed')
accounts(id INTEGER PRIMARY KEY, user_id INTEGER, type TEXT,
         opening_balance INTEGER NOT NULL DEFAULT 0, frozen INTEGER NOT NULL DEFAULT 0,
         currency TEXT DEFAULT 'CNY', created_at TEXT)
ledger_entry(id INTEGER PRIMARY KEY, txn_id INTEGER NOT NULL, account_id INTEGER NOT NULL,
             direction TEXT NOT NULL CHECK(direction IN ('D','C')),
             amount INTEGER NOT NULL CHECK(amount > 0), created_at TEXT)
transactions(id INTEGER PRIMARY KEY, txn_no TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL,
             type TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT DEFAULT 'CNY',
             status TEXT NOT NULL, idempotency_key TEXT UNIQUE NOT NULL, reversed_by INTEGER,
             memo TEXT, category TEXT, counterparty TEXT, created_at TEXT)
contacts(id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, alias TEXT, phone TEXT,
         card_tail TEXT, is_new INTEGER DEFAULT 0)
cards(id INTEGER PRIMARY KEY, user_id INTEGER, card_tail TEXT, status TEXT DEFAULT 'active',
      daily_limit INTEGER, used INTEGER DEFAULT 0, frozen INTEGER DEFAULT 0)
wealth_products(id INTEGER PRIMARY KEY, name TEXT, rate REAL, term_days INTEGER,
                risk_level TEXT, min_amount INTEGER)
holdings(id INTEGER PRIMARY KEY, user_id INTEGER, product_id INTEGER, amount INTEGER, purchased_at TEXT)
subscriptions(id INTEGER PRIMARY KEY, user_id INTEGER, merchant TEXT, amount INTEGER,
              cycle_days INTEGER, next_charge_at TEXT, status TEXT DEFAULT 'active', last_used_at TEXT)
standing_plans(id INTEGER PRIMARY KEY, user_id INTEGER, goal TEXT, trigger_rule TEXT,
               actions TEXT, budget INTEGER, status TEXT DEFAULT 'active',
               next_run_at TEXT, created_at TEXT)
audit_event(id INTEGER PRIMARY KEY, trace_id TEXT NOT NULL, plan_id TEXT, node_id TEXT,
            seq INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, payload TEXT,
            decision TEXT, prev_hash TEXT, hash TEXT, ts TEXT NOT NULL,
            UNIQUE(trace_id, seq))
idempotency(key TEXT PRIMARY KEY, txn_id INTEGER, status TEXT, response_hash TEXT, created_at TEXT)
daily_quota(user_id INTEGER, biz_date TEXT, used INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, biz_date))
sessions(id TEXT PRIMARY KEY, user_id INTEGER, channel TEXT DEFAULT 'app',
         state TEXT DEFAULT 'NORMAL', recent_entities TEXT DEFAULT '{}', updated_at TEXT)
freezes(id INTEGER PRIMARY KEY, user_id INTEGER, account_id INTEGER, amount INTEGER,
        status TEXT DEFAULT 'active', until TEXT, created_at TEXT)
reminders(id INTEGER PRIMARY KEY, user_id INTEGER, at TEXT, content TEXT, status TEXT DEFAULT 'active')
orders(id INTEGER PRIMARY KEY, user_id INTEGER, item TEXT, amount INTEGER, deliver_at TEXT,
       status TEXT DEFAULT 'placed', created_at TEXT)
collection_requests(id INTEGER PRIMARY KEY, user_id INTEGER, payer_contact_id INTEGER,
                    amount INTEGER, status TEXT DEFAULT 'pending', created_at TEXT)
pending_intents(id TEXT PRIMARY KEY, session_id TEXT, user_id INTEGER, plan_json TEXT,
                card_json TEXT, params_hash TEXT, token TEXT, status TEXT, expires_at TEXT,
                stepup_json TEXT, created_at TEXT)
payee_accounts(contact_id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL)
```

> `payee_accounts` 只服务于复式记账（每个联系人一个对手方账户），业务层不直接读写。
> `transactions.category` 供账单分析，`counterparty` 存收款人/商户名（冗余，避免联表）。

视图 `v_account_balance`：`SELECT a.id, a.opening_balance + COALESCE(SUM(CASE e.direction WHEN 'C' THEN e.amount ELSE -e.amount END),0) AS balance FROM accounts a LEFT JOIN ledger_entry e ON e.account_id=a.id GROUP BY a.id`。**任何余额都从这个视图算，禁止回写 `accounts.opening_balance`。**

---

## 4. bankcore API（主控实现，供 tools 调用）

连接：`connect()` 返回 `sqlite3.Connection`，`row_factory=sqlite3.Row`，`PRAGMA foreign_keys=ON`，`isolation_level=None`（手动 `BEGIN`/`COMMIT`）。

```python
init_db(path: str | None = None) -> None          # 建表 + 建视图，幂等
connect(path: str | None = None) -> sqlite3.Connection
reset_db(path: str | None = None) -> None         # 删库重建（测试用）
seed(conn, force: bool = False) -> dict           # 灌种子数据；users 非空时跳过，返回 {"seeded":bool,...}

# --- 账户 / 余额 ---
list_accounts(conn, user_id: int) -> list[dict]
    # [{"id","type","balance_cents","frozen_cents","available_cents","currency"}]
get_balance(conn, account_id: int) -> int
get_daily_used(conn, user_id: int, biz_date: str | None = None) -> int
occupy_quota(conn, user_id: int, amount_cents: int, limit_cents: int = 100000) -> bool
release_quota(conn, user_id: int, amount_cents: int, biz_date: str | None = None) -> None

# --- 联系人 ---
list_contacts(conn, user_id: int) -> list[dict]           # id,name,alias,phone,card_tail,is_new
find_contact(conn, user_id: int, alias_or_name: str) -> list[dict]

# --- 转账（核心） ---
create_transfer(conn, user_id, from_account_id, contact_id, amount_cents,
                memo: str = "", idem_key: str | None = None,
                txn_type: str = "transfer") -> dict
    # 成功 {"ok":True,"txn_id","txn_no","amount_cents","balance_after","payee_name"}
    # 失败 {"ok":False,"error":{"code","message"}}
    # 语义：单事务内 占额度 + 建单 + 双边分录 + 置 succeeded；
    #       幂等键冲突时返回首次结果（status=succeeded）
reverse_transaction(conn, txn_id: int, idem_key: str | None = None) -> dict
    # {"ok":True,"reversal_txn_id","reversal_txn_no"}；原单置 reversed 并写 reversed_by

# --- 流水 / 分析前置 ---
list_transactions(conn, user_id, start: str | None = None, end: str | None = None,
                  limit: int = 200) -> list[dict]
    # [{"id","txn_no","amount_cents","type","status","memo","created_at","direction"}]
    # direction: out/in（相对 user 主账户）

# --- 订阅 ---
list_subscriptions(conn, user_id) -> list[dict]   # id,merchant,amount_cents,cycle_days,next_charge_at,status,last_used_at
cancel_subscription(conn, user_id, sub_id) -> dict   # {"ok","effective_at","message"}

# --- 理财 ---
list_products(conn, risk_level=None, term_days=None) -> list[dict]
list_holdings(conn, user_id) -> list[dict]
purchase_wealth(conn, user_id, product_id, amount_cents) -> dict
redeem_wealth(conn, user_id, holding_id, amount_cents) -> dict

# --- 卡片 ---
list_cards(conn, user_id) -> list[dict]
report_card_lost(conn, user_id, card_id) -> dict
unlock_card(conn, user_id, card_id) -> dict
adjust_card_limit(conn, user_id, card_id, new_limit_cents) -> dict

# --- 资金冻结 ---
freeze_amount(conn, user_id, account_id, amount_cents, until=None) -> dict
    # {"ok","freeze_id","available_cents"}；可用 = balance - frozen - 活跃冻结
release_freeze(conn, freeze_id) -> dict

# --- 长期计划 / 提醒 / 代扣 / 下单 ---
create_standing_plan(conn, user_id, goal, trigger_rule: dict, actions: list[dict],
                     budget_cents: int) -> dict
list_standing_plans(conn, user_id) -> list[dict]
cancel_standing_plan(conn, plan_id) -> dict
set_reminder(conn, user_id, at: str, content: str) -> dict
create_collection_request(conn, user_id, payer_contact_ids: list[int], amount_each_cents: int) -> dict
apply_virtual_card(conn, user_id, card_type="virtual") -> dict
order_gift(conn, user_id, item, amount_cents, deliver_at: str) -> dict
notify(conn, user_id, content: str) -> dict
```

---

## 5. tools 注册表（WP-TOOLS）

`bankagent/specs.py`（主控提供）里的 `TOOL_SPECS` 是工具名/分组/参数/资金方向的唯一清单，**不得增删改名**。

```python
# tools.py
def call(name: str, conn, user_id: int, args: dict) -> dict   # 统一入口
REGISTRY: dict[str, Callable[[sqlite3.Connection, int, dict], dict]]
```

每个工具返回 **ToolResult**：

```json
{
  "ok": true,
  "data": {"txn_no": "T20261029001", "amount_cents": 50000, "balance_after": 1234567},
  "facts": [
    {"id": "f1", "key": "amount", "kind": "money", "value": 50000, "unit": "CNY"},
    {"id": "f2", "key": "txn_no", "kind": "id", "value": "T20261029001"},
    {"id": "f3", "key": "payee_name", "kind": "text", "value": "王秀兰"}
  ],
  "tainted": ["memo"],
  "error": null
}
```

- `facts` 必须覆盖回复里要出现的所有金额/单号；`kind ∈ money|id|ratio|term|text`。
- 失败：`{"ok": false, "data": {}, "facts": [], "tainted": [], "error": {"code": "...", "message": "..."}}`。
- 自由文本字段（memo/merchant/alias）必须列入 `tainted`。
- 所有写操作必须传 `idem_key`（`args["_idem_key"]`，由 executor 注入），工具透传给 bankcore。

---

## 6. policy（WP-POLICY）

```python
def decide(ctx: dict) -> dict
```

`ctx` 键（缺省即 `None`/`[]`）：

| 键 | 类型 | 说明 |
|---|---|---|
| `action` | str | 工具名，或 `adjust_card_limit_up` / `adjust_card_limit_down` |
| `amount` | int | 本次金额（分） |
| `daily_used` | int | 当日已用（分） |
| `payee_is_new` | bool | 是否新收款人 |
| `channel` | str | `app` / `im` |
| `hour` | int | 0–23 |
| `new_device` | bool | 设备指纹异常 |
| `risk_level` | str | `normal` / `high` |
| `plan_outflow` | int | 计划级净流出（分） |
| `batch_count` | int | 批量条数 |
| `stepup_age_seconds` | int \| None | 最近一次强验证距今秒数 |
| `stepup_payee` | str \| None | 最近一次强验证绑定的收款人 |
| `payee_id` | str \| None | 本次收款人 id |
| `risk_score` | int \| None | 0–100 |
| `session_state` | str | `NORMAL` / `WATCH` / `LOCKED` |

返回：

```python
{"level": "green|yellow|red|deny",
 "rule_id": "R-T-01",
 "reason": "累计 1,300.00 元超过 1,000.00 元免密额度",
 "required_factors": ["sms", "face_or_ushield"],
 "base_level": "yellow", "escalations": 1, "discount": 0}
```

规则必须与文档 §6.2.1/§6.2.2/§6.2.3 一致，其中：

- 阈值：`100000` 分（1000 元）、`50000` 分（500 元）、`20000` 分（200 元）、`5000000` 分（5 万）。
- 升级信号：`im_channel`（channel=im 且 amount>30000）、`night`（hour>=23 或 <6，且 amount>50000）、`new_device`、`high_risk`。**升级只作用于 base=yellow**。
- 降级：`stepup_age_seconds < 300` 且 `stepup_payee == payee_id` → discount=1；**只抵消升级项，不抵扣金额阈值类红**。
- `deny`：`modify_password`、`plan_outflow>5000000`、`risk_score>90`、`session_state=="locked"`、transfer 缺 `daily_used`/`amount`。
- 未登记动作 → `deny`。

`tests/test_policy.py` 至少覆盖文档 §8.3 的 40 条（结构化输入 → 期望等级）。

---

## 7. trust（WP-TRUST）

```python
class FactLedger:
    def __init__(self) -> None
    def add(self, facts: list[dict]) -> None
    def contains(self, value, kinds=("money", "id")) -> bool
    def get(self, key: str) -> dict | None
    def all(self) -> list[dict]

def money(cents: int) -> str                     # 50000 -> "500.00"；1234567 -> "12,345.67"
def render(template_id: str, **fields) -> str    # 缺失字段抛 KeyError
def postcheck(reply: str, ledger: FactLedger) -> tuple[bool, list[str]]
def scan_injection(text: str) -> dict            # {"hit": bool, "patterns": [...], "normalized": str}
def wrap_untrusted(label: str, text: str) -> str # <untrusted source="label">…</untrusted>

class AuditLog:
    def __init__(self, conn) -> None
    def append(self, trace_id, actor, action, payload=None, decision=None,
               plan_id=None, node_id=None) -> dict   # 自动 seq/prev_hash/hash
    def list(self, trace_id: str) -> list[dict]
    def verify_chain(self, trace_id: str) -> bool
```

模板至少：`transfer_success` `transfer_failed` `balance` `bill_summary` `subs_list` `plan_created` `frozen` `cancelled` `card_lost` `wealth_bought` `error` `confirm_intro` `stepup_intro`。

`postcheck` 只强校验 `kind in {money,id}`：回复中的金额/单号必须命中账本；`ratio/term/text` 不校验。百分比、日期、期限不得误判。

`scan_injection` 至少识别：指令覆盖（忽略/ignore/忽略以上/跳过确认）、角色伪装（我是行长/管理员/system）、分隔符注入（`---`、`[SYSTEM]`）、编码绕过（base64 片段、零宽字符、全角）。

---

## 8. NLU / Planner / Validator（WP-NLU）

```python
# nlu.py
def parse(text: str, ctx: dict) -> dict

# 返回
{"name": "transfer|bill_analysis|subscription|wealth|card|standing_plan|unknown",
 "slots": {"amount": 50000, "payee": "妈", "range": "this_month", ...},
 "missing": ["amount"], "ambiguity": [], "spans": {"amount": "500", "payee": "我妈"},
 "confidence": 0.92}
```

`ctx`：`{"user_id":1, "recent_entities": {...}, "last_intent": {...}|None, "channel":"app"}`。
必须支持：金额中文（五百 / 5百 / 半千 / 1200 / 1,200 / 5万）、别名（我妈/老婆/房东）、
"再转一次"（继承上一轮已确认 slots）、"上次那个"（recent_entities）、"都退了吧"（batch）、
"这个月/上个月/今年"（range）、AA（"AA"/"各转"/"平摊" → intent=transfer + mode=collection）。

```python
# planner.py
def build_plan(intent: dict, ctx: dict) -> dict
# {"plan_id":"pl_xxx","goal":"...","nodes":[
#    {"id":"n1","tool":"get_contacts","args":{"alias":"妈"},"provenance":{"alias":"user"},
#     "depends_on":[],"risk_hint":"green"}],
#  "confirm_points":["n3"]}
```

约束：节点 ≤8；只能引用 `specs.TOOL_SPECS` 里的工具；资金参数 provenance ∈ `user|tool|const`；
「再转一次」继承的已确认槽位 provenance 记 `user`。

映射（至少）：
| intent | 节点序列 |
|---|---|
| transfer（普通） | `get_contacts` → `quote_transfer` → `create_transfer`（金额≤1000）或 `submit_transfer`（>1000） |
| transfer（AA） | `get_contacts` ×N → `create_collection_request` |
| bill_analysis | `analyze_spending` → `detect_anomaly` |
| subscription | `get_subscriptions` →（取消时）`cancel_subscription` ×N |
| standing_plan | `get_contacts` → `get_profile` → `freeze_amount` → `create_standing_plan` |
| wealth | `get_wealth_products` →（申购）`purchase_wealth` |
| card | `get_accounts`（卡列表用 `list_cards` 对应工具）→ 对应动作 |

```python
# validator.py
def validate(plan: dict, tool_specs: dict) -> list[dict]
# [{"code":"E_TOOL:n9","message":"工具不在白名单"}]
```

七条硬检查：节点数≤8、无环、工具白名单、schema 必填、资金参数 provenance、单节点上限 5 万、计划净流出上限 5 万。

---

## 9. 执行与事件（主控实现，前端按此渲染）

一次 `POST /v1/chat` 的 SSE 事件序列（`seq` 从 1 递增）：

| type | data |
|---|---|
| `thinking` | `{"stage":"nlu"}` |
| `plan` | `{"plan": {...}, "summary": "3 步：查联系人 → 试算 → 转账"}` |
| `policy` | `{"level":"yellow","rule_id":"R-T-02","reason":"..."}` |
| `confirm` | `{"intent_id":"it_..","card":{...},"expires_at":"..."}` |
| `stepup` | `{"challenge_id":"ch_..","factors":["sms"],"hint":"演示验证码 123456"}` |
| `step` | `{"node_id":"n1","tool":"get_contacts","ok":true,"ms":3}` |
| `result` | `{"reply":"...","level":"yellow","txn_no":"T...","audit_url":"/audit/tr_.."}`
| `error` | `{"code":"E_..","message":"..."}` |
| `audit` | `{"event_count":12}` |

确认卡 `card`：

```json
{"action":"transfer","level":"yellow","params_hash":"sha256:...",
 "payee_name":"王秀兰","payee_tail":"8821","amount":"500.00","arrival":"实时",
 "params":{"payee_name":"王秀兰","payee_tail":"8821","amount":"500.00","arrival":"实时"},
 "expires_at":"2026-10-29T20:40:00+08:00","intent_id":"it_7f3a","token":"一次性 token"}
```

> 金额一律**已格式化的字符串**（"500.00"），不是分。
> 卡片字段**同时**提供平铺（`payee_name`/`amount`）与 `params` 嵌套两种形状，客户端按任一读取即可；`token` 只在确认卡出现，`params_hash` 用于改参检测。

状态：`POST /v1/chat` 返回 `{"trace_id","status":"awaiting_confirm|awaiting_stepup|done|error","events":[...]}`。

---

## 10. HTTP API（主控实现，前端消费）

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/` | – | `index.html` |
| GET | `/static/*` | – | 静态文件 |
| GET | `/health` | – | `{"ok":true,"db":"...","version":"1.0"}` |
| POST | `/v1/chat` | `{"session_id","text"}` | `{"trace_id","status","events","reply","pending"}` |
| GET | `/v1/stream/{trace_id}` | – | SSE（重放事件，20ms 间隔） |
| POST | `/v1/confirm` | `{"session_id","intent_id","token"}` | `{"ok","status","events","reply"}` |
| POST | `/v1/stepup/verify` | `{"session_id","challenge_id","code"}` | `{"ok","status","events","reply"}` |
| POST | `/v1/cancel` | `{"session_id","intent_id"}` | `{"ok","events"}` |
| GET | `/v1/audit/{trace_id}` | – | `{"trace_id","events":[...],"chain_ok":true}` |
| GET | `/v1/state/{session_id}` | – | `{"state","pending":{...}\|null}` |

错误体：`{"code":"E_PARAM","message":"...","trace_id":null}`，HTTP 400/404/409/500。

前端必须支持：对话流、计划步骤逐条浮现、确认卡（含参数与倒计时）、强验证弹窗、
结果卡（单号 + 审计链接）、审计时间轴页（`/audit/tr_xxx` 直接用 `GET /v1/audit` 渲染）。

---

## 11. 错误码

| code | 场景 |
|---|---|
| `E_CONTACT_NOT_FOUND` | 联系人不存在 |
| `E_INSUFFICIENT_FUND` | 余额不足 |
| `E_LIMIT_EXCEEDED` | 日额度超限 |
| `E_POLICY_DENIED` | 策略拒绝 |
| `E_CONFIRM_EXPIRED` | 确认卡过期 |
| `E_CONFIRM_USED` | token 已消费 |
| `E_PARAM` | 入参缺失 |
| `E_STEPUP_FAIL` | 强验证失败 |
| `E_NOT_FOUND` | 资源不存在 |
| `E_INTERNAL` | 内部错误 |

---

## 12. 测试要求

- 每个工作包提交 `tests/test_*.py`，用 `unittest`，在 `backend/` 下 `python -m unittest discover tests -v` 必须全绿。
- 测试必须**真实落库**（`BANK_DB` 指向临时文件或 `:memory:` 不行——多线程测试用临时文件），不 mock 账务。
- 不允许联网、不依赖本地时区外的库。
- 提交前自检：`python -c "import bankagent.<你的模块>"` 无报错。
