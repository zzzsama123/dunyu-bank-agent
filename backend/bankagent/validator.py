"""Validator：计划静态校验（纯函数，禁止 I/O）。

对外唯一入口：

    validate(plan: dict, tool_specs: dict) -> list[dict]
    # [{"code": "E_CYCLE", "message": "..."}]

七条硬检查（CONTRACT.md §8 / §5.5.4）：
    1. 节点数 ≤ specs.MAX_PLAN_NODES（8）              -> E_PLAN_TOO_LARGE
    2. depends_on 无环                                  -> E_CYCLE
    3. 工具必须在白名单（且不得是 system 组）           -> E_TOOL:<node_id>
    4. schema 必填参数 / 类型                           -> E_ARGS:<node_id>
    5. 资金参数 provenance ∈ {user, tool, const}        -> E_TAINTED_ARG:<id>.<param>
    6. 单节点金额 ≤ 5 万（5,000,000 分）                -> E_CAP:<node_id>
    7. 计划净流出 ≤ 5 万；流入相减                      -> E_PLAN_OUTFLOW_CAP

金额参数必须是具体整数分，不接受 "$n1.amount" 这类运行时引用——否则单笔与
计划级上限都可以被引用绕过。身份类参数（contact_id/card_id/product_id 等）
允许引用，由 executor 在执行前 hydrate。
"""

from __future__ import annotations

from . import specs

_MONEY_FIELDS = ("amount", "amount_each", "budget", "new_limit")
_ALLOWED_PROVENANCE = {"user", "tool", "const"}
_ID_FIELDS = {
    "contact_id", "card_id", "product_id", "holding_id", "freeze_id",
    "sub_id", "account_id", "from_account_id", "to_account_id",
}


def _err(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _node_id(node: dict, index: int) -> str:
    value = node.get("id")
    return str(value) if value not in (None, "") else f"n{index + 1}"


def _is_ref(value) -> bool:
    return isinstance(value, str) and value.startswith("$") and len(value) > 1


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _deps(node: dict) -> list[str]:
    value = node.get("depends_on")
    if not isinstance(value, list):
        return []
    return [str(x) for x in value if x not in (None, "")]


def _has_cycle(nodes: list[dict]) -> bool:
    ids: list[str] = []
    graph: dict[str, list[str]] = {}
    indegree: dict[str, int] = {}
    for index, node in enumerate(nodes):
        node_id = _node_id(node, index)
        if node_id in graph:
            continue
        ids.append(node_id)
        graph[node_id] = []
        indegree[node_id] = 0

    for index, node in enumerate(nodes):
        node_id = _node_id(node, index)
        for dep in _deps(node):
            if dep not in graph:
                continue  # 未知依赖不构成环；是否报错不在七条硬检查内
            graph[dep].append(node_id)
            indegree[node_id] = indegree.get(node_id, 0) + 1

    queue = [node_id for node_id in ids if indegree.get(node_id, 0) == 0]
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for nxt in graph.get(current, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return visited != len(ids)


def _type_ok(value, type_name: str, param: str) -> bool:
    if type_name == "int":
        if _is_ref(value):
            # 金额不接受引用；身份类参数接受
            return param not in _MONEY_FIELDS
        return _is_int(value)
    if type_name == "str":
        return isinstance(value, str)
    if type_name == "list":
        if not isinstance(value, list):
            return False
        if param == "payer_contact_ids":
            if not value:
                return False
            return all(_is_int(item) or _is_ref(item) for item in value)
        return True
    if type_name == "dict":
        return isinstance(value, dict)
    return True


def _money_of_out(node: dict) -> int:
    args = node.get("args") if isinstance(node.get("args"), dict) else {}
    for key in ("amount", "amount_each", "budget", "new_limit"):
        value = args.get(key)
        if _is_int(value):
            return value
    return 0


def _money_of_in(node: dict) -> int:
    args = node.get("args") if isinstance(node.get("args"), dict) else {}
    tool = node.get("tool")
    amount_each = args.get("amount_each")
    if tool == "create_collection_request" and _is_int(amount_each):
        payers = args.get("payer_contact_ids")
        count = len(payers) if isinstance(payers, list) and payers else 1
        return amount_each * count
    amount = args.get("amount")
    if _is_int(amount):
        return amount
    return 0


def _node_cap_exceeded(node: dict) -> bool:
    args = node.get("args") if isinstance(node.get("args"), dict) else {}
    for key in _MONEY_FIELDS:
        value = args.get(key)
        if _is_int(value) and value > specs.HARD_CAP_PER_NODE:
            return True
    return False


def validate(plan: dict, tool_specs: dict) -> list[dict]:
    """对 Plan 做七条硬检查，返回错误列表（空列表 = 通过）。"""
    errors: list[dict] = []
    if not isinstance(plan, dict):
        return [_err("E_ARGS:plan", "计划必须是 dict")]
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        return [_err("E_ARGS:plan", "计划 nodes 必须是列表")]

    # 1) 节点数
    if len(nodes) > specs.MAX_PLAN_NODES:
        errors.append(_err(
            "E_PLAN_TOO_LARGE",
            f"计划节点数 {len(nodes)} 超过上限 {specs.MAX_PLAN_NODES}",
        ))

    # 2) 环
    if _has_cycle([n if isinstance(n, dict) else {} for n in nodes]):
        errors.append(_err("E_CYCLE", "计划依赖存在环，无法拓扑执行"))

    valid_specs = tool_specs if isinstance(tool_specs, dict) else {}

    for index, raw_node in enumerate(nodes):
        node = raw_node if isinstance(raw_node, dict) else {}
        node_id = _node_id(node, index)
        tool = node.get("tool")
        spec = valid_specs.get(tool) if isinstance(tool, str) else None

        # 3) 工具白名单（system 工具模型不可直接生成）
        if not isinstance(tool, str) or not isinstance(spec, dict):
            errors.append(_err(f"E_TOOL:{node_id}", f"工具不在白名单：{tool!r}"))
            continue
        if spec.get("group") == "system":
            errors.append(_err(f"E_TOOL:{node_id}", f"系统工具不可由模型生成：{tool}"))
            continue

        # 4) schema 必填 / 类型
        args = node.get("args")
        if not isinstance(args, dict):
            errors.append(_err(f"E_ARGS:{node_id}", "args 必须是 dict"))
            continue
        params = spec.get("params") if isinstance(spec.get("params"), dict) else {}
        arg_problems: list[str] = []
        for param, param_spec in params.items():
            if not isinstance(param_spec, dict):
                continue
            required = bool(param_spec.get("required"))
            if param not in args or args.get(param) is None:
                if required:
                    arg_problems.append(f"缺少必填参数 {param}")
                continue
            if not _type_ok(args.get(param), param_spec.get("type", ""), param):
                arg_problems.append(f"参数 {param} 类型应为 {param_spec.get('type')}")
        if arg_problems:
            errors.append(_err(f"E_ARGS:{node_id}", "；".join(arg_problems)))

        # 5) 资金参数 provenance
        provenance = node.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
        for param in specs.FUND_PARAMS:
            if param not in args:
                continue
            if param not in provenance:
                errors.append(_err(
                    f"E_TAINTED_ARG:{node_id}.{param}",
                    f"资金参数 {param} 缺少 provenance 标注",
                ))
                continue
            source = provenance.get(param)
            if source not in _ALLOWED_PROVENANCE:
                errors.append(_err(
                    f"E_TAINTED_ARG:{node_id}.{param}",
                    f"资金参数 {param} provenance={source!r}，只允许 user/tool/const",
                ))

        # 6) 单节点上限
        if _node_cap_exceeded(node):
            errors.append(_err(
                f"E_CAP:{node_id}",
                f"单节点金额超过上限 {specs.HARD_CAP_PER_NODE} 分（5 万）",
            ))

    # 7) 计划净流出（流入相减）
    net = 0
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            continue
        tool = raw_node.get("tool")
        spec = valid_specs.get(tool) if isinstance(tool, str) else None
        flow = spec.get("flow") if isinstance(spec, dict) else specs.flow_of(tool or "")
        if flow == "out":
            net += _money_of_out(raw_node)
        elif flow == "in":
            net -= _money_of_in(raw_node)
    net = max(net, 0)
    if net > specs.HARD_CAP_PER_PLAN:
        errors.append(_err(
            "E_PLAN_OUTFLOW_CAP",
            f"计划净流出 {net} 分超过上限 {specs.HARD_CAP_PER_PLAN} 分（5 万），疑似拆分绕过",
        ))

    return errors
