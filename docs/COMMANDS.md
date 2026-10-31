# 命令 IR（模型先理解，再输出命令码）

模型不再直接"决定调什么工具"，而是把用户的话编译成**有限命令码 + 参数**：

```json
{"cmd": "query.transactions", "args": {"range": "last_3m", "direction": "in", "counterparty": "妈"}}
```

系统负责：命令是否在白名单、参数类型与单位、权限裁决、执行、事实校验、审计。

## 命令清单（`backend/bankagent/commands.py`）

| 命令码 | 含义 | 主要参数 |
|---|---|---|
| `query.transactions` | 查流水/转账记录 | range, direction(in/out), counterparty, category |
| `query.bill` | 支出汇总/账单 | range |
| `query.anomaly` | 可疑扣费/异常 | range |
| `query.subscriptions` | 自动扣费列表 | — |
| `manage.subscription.cancel` | 取消订阅 | merchant |
| `query.wealth` | 理财/收益 | range |
| `transfer.create` | 转账 | amount(分), payee, memo |
| `transfer.collection` | AA 收款 | amount(分), payer_count |
| `plan.create` | 长期计划/生日 | payee, budget(分), when, goal |
| `card.query` / `card.lost` / `card.unlock` / `card.limit` | 卡片相关 | new_limit(分) |
| `human.handoff` | 转人工 | — |
| `none` | 无关/听不清 | reason: out_of_scope / unclear |

## 实测（DeepSeek，2026-10-30）

| 你说 | 命令码 | 结果 |
|---|---|---|
| 我妈这个月转给我多少钱 | `query.transactions` | 1 笔 1,100.00 元 |
| 这个月钱都花哪了 | `query.bill` | 支出合计 10,632.18 元 |
| 有没有乱扣费的 | `query.anomaly` | 2 笔可疑 |
| 退掉视频会员A | `manage.subscription.cancel` | 确认取消 |
| 给张三转 100 | `transfer.create` | 确认卡 |
| 我卡丢了 | `card.lost` | 强验证 |
| 老婆生日帮我准备一下 | `plan.create` | 先推荐方案 |
| 帮我把钱弄一下 | `none/unclear` | 给 3 个候选 + 转人工 |

## 安全边界

1. **模型不能发明命令**：不在 `CATALOG` 里的 `cmd` 直接判非法，回退确定性链路；
2. **模型不能改金额单位**：金额一律按"分"校验，越界丢弃；
3. **模型不能越权**：命令只表达"想办什么"，权限分级、确认卡、强验证、执行仍由代码决定；
4. **听不清就澄清**：`none/unclear` → 候选列表（回数字继续）；
5. **每一步可审计**：`command.parsed` 记录命令码、参数、耗时、是否合法。

## 模式切换

```powershell
# 默认：模型先理解成命令码（需要 LLM_API_KEY）
$env:AGENT_MODE = "model"

# 纯规则/离线评审：不调模型
$env:AGENT_MODE = "rules"
```
