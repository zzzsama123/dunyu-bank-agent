"""执行器：拓扑序执行、幂等键注入、失败 Saga 逆序补偿。"""

from __future__ import annotations

import hashlib
import os
import time

from . import config, specs, tools


def topo_order(plan: dict) -> list[dict]:
    nodes = {n["id"]: n for n in plan.get("nodes", [])}
    indeg = {nid: 0 for nid in nodes}
    for nid, n in nodes.items():
        for dep in n.get("depends_on", []):
            if dep in nodes:
                indeg[nid] += 1
    queue = [nid for nid, d in indeg.items() if d == 0]
    out: list[dict] = []
    while queue:
        queue.sort()
        nid = queue.pop(0)
        out.append(nodes[nid])
        for other, n in nodes.items():
            if nid in n.get("depends_on", []):
                indeg[other] -= 1
                if indeg[other] == 0:
                    queue.append(other)
    if len(out) != len(nodes):
        raise ValueError("计划存在环")
    return out


def idem_key(plan_id: str, node_id: str) -> str:
    raw = f"{plan_id}:{node_id}".encode("utf-8")
    return "idem-" + hashlib.sha256(raw).hexdigest()[:24]


_MISS = object()


def _walk(obj, path: str):
    cur = obj
    for part in path.split("."):
        if not part:
            continue
        name, idx = part, None
        if "[" in part and part.endswith("]"):
            name, _, rest = part.partition("[")
            try:
                idx = int(rest[:-1])
            except ValueError:
                return _MISS
        if name:
            if isinstance(cur, dict):
                cur = cur.get(name, _MISS)
            elif isinstance(cur, list) and name.isdigit():
                cur = cur[int(name)]
            else:
                return _MISS
            if cur is _MISS:
                return _MISS
        if idx is not None:
            if isinstance(cur, (list, tuple)) and -len(cur) <= idx < len(cur):
                cur = cur[idx]
            else:
                return _MISS
    return cur


def _lookup(results_by_id: dict, ref):
    """解析 `$n1.contact_id` / `$n1.items[0].id` 这类前序节点引用。

    约定见 CONTRACT §8：引用只用于标识类参数；找不到时回退到 items[0]。
    """
    if not isinstance(ref, str) or not ref.startswith("$"):
        return ref
    head, _, path = ref[1:].partition(".")
    res = results_by_id.get(head)
    if not res:
        return ref
    data = res.get("data") or {}
    if not path:
        return data
    value = _walk(data, path)
    if value is not _MISS and value is not None:
        return value
    # 回退：单条结果时按同名字段 / items[0] 取
    items = data.get("items") or data.get("accounts") or data.get("cards") or []
    field = path.split(".")[-1].split("[")[0]
    if items and isinstance(items[0], dict):
        first = items[0]
        if field in first:
            return first[field]
        if field.endswith("_id"):
            return first.get(field) or first.get("id") or first.get("card_id")
    if field in data:
        return data[field]
    return ref


def _resolve_args(args: dict, results_by_id: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and v.startswith("$"):
            out[k] = _lookup(results_by_id, v)
        elif isinstance(v, list):
            out[k] = [_lookup(results_by_id, x) if isinstance(x, str) else x for x in v]
        else:
            out[k] = v
    return out


def _compensate_one(conn, node: dict, result: dict, audit, trace_id: str) -> dict | None:
    """按 specs.compensation 逆序补偿；返回补偿结果。"""
    spec = specs.TOOL_SPECS.get(node["tool"], {})
    mode = spec.get("compensation")
    data = (result or {}).get("data") or {}
    if mode == "reverse" and data.get("txn_id"):
        from . import bankcore

        r = bankcore.reverse_transaction(conn, int(data["txn_id"]), idem_key="cmp-" + str(data["txn_id"]))
        audit.append(trace_id, actor="system", action="compensate.reverse", node_id=node["id"],
                     payload={"txn_id": data["txn_id"], "ok": r.get("ok")},
                     decision="reversed" if r.get("ok") else "reversal_failed")
        return r
    if mode == "release_freeze" and data.get("freeze_id"):
        from . import bankcore

        r = bankcore.release_freeze(conn, int(data["freeze_id"]))
        audit.append(trace_id, actor="system", action="compensate.release_freeze", node_id=node["id"],
                     payload={"freeze_id": data["freeze_id"], "ok": r.get("ok")},
                     decision="released" if r.get("ok") else "release_failed")
        return r
    return None


def execute(conn, plan: dict, trace_id: str, audit, user_id: int) -> dict:
    """返回 {ok, results, facts, failed_node, compensations}。"""
    order = topo_order(plan)
    results: list[dict] = []
    facts: list[dict] = []
    executed: list[tuple[dict, dict]] = []
    by_node: dict[str, dict] = {}

    for node in order:
        args = _resolve_args(dict(node.get("args") or {}), by_node)
        args["_idem_key"] = idem_key(plan["plan_id"], node["id"])
        t0 = time.perf_counter()
        try:
            if os.environ.get("DEMO_FAIL_TOOL") == node["tool"]:
                res = {"ok": False, "data": {}, "facts": [], "tainted": [],
                       "error": {"code": "E_INJECTED", "message": "演示注入失败"}}
            else:
                res = tools.call(node["tool"], conn, user_id, args)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "data": {}, "facts": [], "tainted": [],
                   "error": {"code": "E_INTERNAL", "message": str(e)}}
        ms = int((time.perf_counter() - t0) * 1000)
        row = {"node_id": node["id"], "tool": node["tool"], "ok": bool(res.get("ok")),
               "data": res.get("data", {}), "error": res.get("error"), "ms": ms}
        results.append(row)
        facts.extend(res.get("facts") or [])
        audit.append(
            trace_id, actor="agent", action="tool.call", node_id=node["id"],
            payload={"tool": node["tool"], "args_keys": sorted(k for k in args if not k.startswith("_")),
                     "ok": row["ok"], "ms": ms, "error": row["error"]},
            decision="ok" if row["ok"] else "error",
            plan_id=plan.get("plan_id"),
        )
        if not row["ok"]:
            compensations = []
            for prev_node, prev_res in reversed(executed):
                c = _compensate_one(conn, prev_node, prev_res, audit, trace_id)
                if c is not None:
                    compensations.append({"node_id": prev_node["id"], "result": c})
            return {"ok": False, "results": results, "facts": facts, "failed_node": node["id"],
                    "failed_tool": node["tool"], "error": row["error"], "compensations": compensations}
        executed.append((node, res))
        by_node[node["id"]] = res

    return {"ok": True, "results": results, "facts": facts, "compensations": []}
