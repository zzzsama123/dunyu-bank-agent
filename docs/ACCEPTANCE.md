# 验收对照（方案 0.3 的 S1–S8）

> 复跑命令都在 `bankagent/backend` 或 `bankagent/` 下；结果为 2026-10-30 实测。

| 编号 | 标准 | 本成品证据 | 结果 |
|---|---|---|---|
| S1 | 主线全链路 ≤5 分钟：模糊表达→计划→黄色确认→红色强验证→失败/撤销→审计回放 | `python scripts/demo.py`：六幕约 20 秒跑完，输出见 [DEMO_TRANSCRIPT.md](DEMO_TRANSCRIPT.md) | ✅ |
| S2 | 权限判定准确率 100%（40 条边界） | `python -m eval.runner` → 权限边界 **60/60**；`tests.test_policy` 88 例 | ✅ |
| S3 | 注入拦截 ≥95%，越权执行 0 | 红队集 **55/55**；`tests.test_e2e.TestInjection` 断言不进 done | ✅ |
| S4 | 回复事实可溯源率 100% | `trust.postcheck` 在发送前强制校验金额/单号；`FactLedger` 覆盖全部工具 facts；失败即降级 | ✅（机制 + 单测） |
| S5 | 意图准确率 ≥90%（模糊 ≥80%） | 意图集 **84/84**，含"再转一次""都退了吧""买5000那个" | ✅ |
| S6 | 任一资金操作可还原决策链（≥8 字段） | `GET /v1/audit/tr_xxx` 返回 12 事件：message→nlu→plan→validate→policy→stepup→tool.call→render，`chain_ok=true` | ✅ |
| S7 | 五类交付物齐备 | 源码/README/测试 ✅；PPT、视频、技术文档另属答辩物料（见主方案第十部分） | ⚠️ 部分 |
| S8 | 文档一致性 | `scripts/verify.ps1` **29/29**（主方案文档） | ✅ |

## 复跑清单

```powershell
# 1) 单元 + 集成（448 个：开发机 1 项跳过，便携版全通过）
cd bankagent\backend
python -m unittest discover tests -v

# 2) 评测集（243 条）
python -m eval.runner

# 3) 五幕剧（命令行）
cd ..
python scripts\demo.py

# 4) 交互式演示
python -m bankagent.api        # 需在 backend 下；打开 http://127.0.0.1:8000
```

## 关键截图位（答辩用）

| 画面 | 怎么复现 |
|---|---|
| 计划逐条浮现 + 绿色免确认 | 输入"我这个月钱都去哪了" |
| 黄色确认卡（金额/收款人/尾号/倒计时） | "转 100 给张三" |
| 红色强验证（累计 1,300） | "再给我妈转 800" → 输入 123456 |
| 批量不可逆动作的克制 | 先"我都被扣了什么会员"，再"把没用的都退了" |
| 长周期 + 撤销补偿 | "我老婆下个月生日，帮我准备一下，别超过 1000" → "算了" |
| 审计时间轴 + 哈希链 | 结果卡点"查看审计"，或直接访问 `/v1/audit/<trace_id>` |
