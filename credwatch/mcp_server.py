"""最小 MCP 服务器（stdio, JSON-RPC 2.0），零依赖。

给 Claude Code / Cursor 等 agent 用：执行任务前先调用 check_credentials，
发现将过期就调用 renew_credential。
"""
from __future__ import annotations

import json
import sys

from . import __version__, engine

TOOLS = [
    {
        "name": "preflight",
        "description": "开始一个长任务（大规模重构、批量部署、多 agent 并行）前调用：检查所需授权能否撑过预计时长，"
                       "能自动续的先续上。go=false 时先把 blockers 里的修复命令告诉用户，不要开跑。",
        "inputSchema": {"type": "object", "properties": {
            "duration_minutes": {"type": "integer", "description": "预计任务时长（分钟），默认 120"},
            "platforms": {"type": "array", "items": {"type": "string"},
                          "description": "只检查这些平台，如 [\"github\", \"cloudflare\"]"}}},
    },
    {
        "name": "diagnose_auth",
        "description": "当 git push / 部署报 401、403 但用户坚称已登录时调用：查找写死在 git 配置里的旧令牌、"
                       "被环境变量覆盖的登录等隐蔽原因，并给出修复命令。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_credentials",
        "description": "检查本机所有 CLI 授权（GitHub、Cloudflare 及自定义）的状态与剩余有效期。"
                       "在执行 git push、部署等需要授权的操作前调用。",
        "inputSchema": {"type": "object", "properties": {
            "only_problems": {"type": "boolean", "description": "只返回将过期/已失效/未登录的凭证"}}},
    },
    {
        "name": "renew_credential",
        "description": "续期一个凭证。可自动续期的会直接刷新；需要人工授权的返回需要用户执行的命令。",
        "inputSchema": {"type": "object", "required": ["id"], "properties": {
            "id": {"type": "string", "description": "check_credentials 返回的 id，如 cloudflare:wrangler CLI"}}},
    },
]


def _call(name: str, args: dict) -> str:
    if name == "preflight":
        import datetime as dt
        res = engine.preflight(dt.timedelta(minutes=int(args.get("duration_minutes") or 120)),
                               args.get("platforms"))
        return json.dumps(res, ensure_ascii=False, indent=2)
    if name == "diagnose_auth":
        from .doctor import diagnose, public
        return json.dumps({"findings": public(diagnose()),
                           "note": "auto_fix=true 的项可让用户运行 credwatch doctor --fix 一键修复"},
                          ensure_ascii=False, indent=2)
    creds = engine.check_all()
    if name == "check_credentials":
        data = [c.to_dict() for c in creds]
        if args.get("only_problems"):
            data = [d for d in data if d["status"] != "ok"]
        return json.dumps({"credentials": data}, ensure_ascii=False, indent=2)
    if name == "renew_credential":
        cred = engine.find(creds, args.get("id", ""))
        if not cred:
            return json.dumps({"ok": False, "message": f"找不到凭证 {args.get('id')}",
                               "available": [c.id for c in creds]}, ensure_ascii=False)
        ok, msg = engine.renew(cred)
        return json.dumps({"ok": ok, "message": msg, "needs_user": not ok}, ensure_ascii=False)
    raise ValueError(f"unknown tool {name}")


def handle(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")
    if rid is None:  # notification
        return None
    try:
        if method == "initialize":
            result = {"protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "credwatch", "version": __version__}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = req.get("params", {})
            result = {"content": [{"type": "text", "text": _call(p.get("name"), p.get("arguments") or {})}]}
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": f"错误：{e}"}], "isError": True}}


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
